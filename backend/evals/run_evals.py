"""Evaluation against the gold answers of the synthetic EHR.

Methods:
 rules the prescreen engine on structured data only, no LOINC mappings. Open patients: needs review.
 rules_mapped the same with the approved LOINC mappings of the Eastside local codes.
 single_prompt rules_mapped, then one model call per open patient with the whole chart.
 agents rules_mapped, then the chart agent + code guards + evidence reviewer per open patient. All on the same 240 patients and the same approved criteria. Non-agentic baseline.

    PYTHONPATH=. .venv/bin/python -m backend.evals.run_evals                 # all methods, all trials
    PYTHONPATH=. .venv/bin/python -m backend.evals.run_evals --methods rules,rules_mapped
    PYTHONPATH=. .venv/bin/python -m backend.evals.run_evals --limit 5       # 5 open patients per trial (smoke)

Writes reports/evals-<time>.json and .md."""

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.app.adjudicate import apply, verdict
from backend.app.agent.chart import ChartReviewer, build_questions
from backend.app.agent.llm import Models
from backend.app.agent.telemetry import UsageRecorder, tagged
from backend.app.config import ROOT, settings
from backend.app.criteria import load_approved
from backend.app.ehr.store import EHR
from backend.app.engine import Engine, atom_specs

TRIALS = ["NCT07668388", "NCT06742723", "NCT06736574"]
# The mappings that a terminologist approves for the Eastside local codes . See vocab.py.
APPROVED_MAPPINGS = {"ECH-POCA1C": "59261-8", "ECH-EGFR21": "98979-8", "ECH-MALBCR": "9318-7"}
LABEL = {"eligible": "eligible", "ineligible": "ineligible", "needs_review": "unknown"}


class CriterionStatus(BaseModel):
    id: str
    status: str = Field(description="met, not_met or unknown")


class SingleAnswer(BaseModel):
    criteria: list[CriterionStatus]


SINGLE_PROMPT = """You screen one patient for a clinical trial. Read the patient's record and notes. For each
criterion give met, not_met or unknown. A family member's condition is not the patient's. Negated statements are not
present. Respect time windows relative to the screening date. Convert units when needed."""


def chart_text(rec, today) -> str:
    p = rec.person
    lines = [f"Patient {p.person_id}, {p.sex}, age {p.age(today)}. Screening date {today}.", "CONDITIONS:"]
    lines += [f"- {c['code']} {c['name']} onset {c['onset']}{' ended ' + str(c['end']) if c['end'] else ''}" for c in rec.conditions]
    lines += ["MEDICATIONS:"] + [f"- {d['name']} {d['dose']} {d['start']} to {d['end'] or 'now'}" for d in rec.drugs]
    lines += ["LABS:"] + [f"- {lab['date']} {lab['source_name']} {lab['value']:g} {lab['unit']}" for lab in rec.labs[-30:]]
    lines += ["NOTES:"] + [f"[{n.note_id} {n.date} {n.type}]\n{n.text}" for n in rec.notes]
    return "\n".join(lines)


def single_prompt(models, rec, cset, screen, today, callbacks) -> list[dict]:
    """Ask once for the criteria that the rules left open; return them as resolutions on the whole criterion."""
    open_ids = [c.id for c in screen.criteria if not c.passes and not c.fails]
    text = {c.id: c.text for c in cset.criteria}
    prompt = chart_text(rec, today) + "\n\nCRITERIA:\n" + "\n".join(f"{i}: {text[i]}" for i in open_ids)
    out: SingleAnswer = models.for_agent("single_prompt").with_structured_output(SingleAnswer).invoke(
        [SystemMessage(SINGLE_PROMPT), HumanMessage(prompt)], tagged("single_prompt", rec.person.person_id, callbacks))
    got = {c.id: c.status for c in (out.criteria if out else [])}
    for c in screen.criteria:
        if c.id in open_ids and got.get(c.id) in ("met", "not_met"):
            c.status = got[c.id]
    return []


def metrics(pred: dict[str, str], gold: dict[str, dict], patients: dict[str, dict]) -> dict:
    g = {p: gold[p]["label"] for p in pred}
    tp = sum(1 for p in pred if pred[p] == "eligible" and g[p] == "eligible")
    fp = sum(1 for p in pred if pred[p] == "eligible" and g[p] != "eligible")
    fn = sum(1 for p in pred if pred[p] != "eligible" and g[p] == "eligible")
    missed = sum(1 for p in pred if pred[p] == "ineligible" and g[p] == "eligible")
    exact = sum(1 for p in pred if pred[p] == g[p])
    review = sum(1 for p in pred if pred[p] == "unknown")
    by = {}
    for attr in ("clinic", "race"):
        rows = defaultdict(lambda: [0, 0])
        for p in pred:
            if g[p] == "eligible":
                rows[patients[p][attr]][1] += 1
                rows[patients[p][attr]][0] += pred[p] == "eligible"
        by[attr] = {k: {"found": v[0], "eligible": v[1]} for k, v in sorted(rows.items())}
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "missed_as_ineligible": missed,
            "needs_review": review, "exact": exact, "n": len(pred), "accuracy": round(exact / len(pred), 3), "recall_by": by}


def criterion_accuracy(screens, gold) -> tuple[int, int]:
    right = total = 0
    for s in screens:
        for c in s.criteria:
            if c.status == "unknown":
                continue
            total += 1
            right += c.status == gold[s.person_id]["criteria"][c.id]
    return right, total


def run(methods: list[str], limit: int | None, trials: list[str]) -> dict:
    ehr = EHR(settings.ehr_path)
    gold = json.loads((settings.data_dir / "gold.json").read_text())
    patients = gold["patients"]
    models = Models(settings)
    report = {"created": datetime.now().isoformat(timespec="seconds"), "model": models.describe(), "screen_date": str(settings.screen_date),
              "patients": len(ehr.records), "limit": limit, "trials": {}}
    today = settings.screen_date
    for nct in trials:
        cset, g = load_approved(nct), gold["trials"][nct]
        specs = atom_specs(cset)
        tr = report["trials"][nct] = {"gold": dict(Counter(r["label"] for r in g.values())), "methods": {}}
        # the hard set: patients that the rules with mappings cannot decide. Every method is scored on the same set.
        hard = [s.person_id for s in Engine(ehr, today, APPROVED_MAPPINGS).screen_all(cset) if s.stage == "open"]
        hard = set(hard[:limit] if limit else hard)
        tr["hard_set"] = {"n": len(hard), "gold": dict(Counter(g[p]["label"] for p in hard))}
        for method in methods:
            engine = Engine(ehr, today, {} if method == "rules" else APPROVED_MAPPINGS)
            screens = engine.screen_all(cset)
            stages = Counter(s.stage for s in screens)
            open_ = [s for s in screens if s.stage == "open"]
            if limit:
                open_ = open_[:limit]
            calls: list[dict] = []
            cb = [UsageRecorder(calls.append)]
            started = time.time()
            traces = {}
            guard_problems = rejected = answered = 0
            if method == "agents":
                reviewer = ChartReviewer(models, today, settings.chart_rounds, cb)
                for i, s in enumerate(open_):
                    try:
                        res = [asdict(r) for r in reviewer.run(ehr.get(s.person_id), build_questions(s, specs, {c.id: c.hints for c in cset.criteria if c.hints}))]
                    except Exception as exc:
                        res = []
                        traces[s.person_id] = {"error": str(exc)[:200]}
                    apply(s, specs, cset, res)
                    guard_problems += sum(1 for r in res if r["problems"])
                    rejected += sum(1 for r in res if r["reviewer"].startswith("rejected"))
                    answered += sum(1 for r in res if r["status"] != "unknown")
                    traces[s.person_id] = traces.get(s.person_id) or {"questions": len(res), "resolved": sum(r["status"] != "unknown" for r in res)}
                    print(f"  {nct} agents {i + 1}/{len(open_)} {s.person_id} -> {verdict(s)} (gold {g[s.person_id]['label']})", flush=True)
            elif method == "single_prompt":
                for i, s in enumerate(open_):
                    try:
                        single_prompt(models, ehr.get(s.person_id), cset, s, today, cb)
                    except Exception as exc:
                        traces[s.person_id] = {"error": str(exc)[:200]}
                    print(f"  {nct} single {i + 1}/{len(open_)} {s.person_id} -> {verdict(s)} (gold {g[s.person_id]['label']})", flush=True)
            if limit:  # only the patients that the method saw count
                keep = {s.person_id for s in open_}
                screens = [s for s in screens if s.stage != "open" or s.person_id in keep]
            pred = {s.person_id: LABEL[verdict(s)] for s in screens}
            m = metrics(pred, g, patients)
            right, total = criterion_accuracy(screens, g)
            traps = defaultdict(lambda: [0, 0])
            hard_pred = {p: pred.get(p, "unknown") for p in hard}
            hard_m = {"n": len(hard), "right": sum(hard_pred[p] == g[p]["label"] for p in hard),
                      "review": sum(hard_pred[p] == "unknown" for p in hard),
                      "wrong": sum(hard_pred[p] not in ("unknown", g[p]["label"]) for p in hard)}
            for s in screens:
                if s.person_id not in hard:
                    continue
                for t in patients[s.person_id]["traps"]:
                    traps[t][1] += 1
                    traps[t][0] += pred[s.person_id] == g[s.person_id]["label"]
            elapsed = time.time() - started
            tr["methods"][method] = m | {"hard": hard_m,
                "prescreen": dict(stages), "open_patients": len(open_), "criterion_accuracy": round(right / total, 3) if total else None,
                "criteria_decided": total, "traps": {k: {"right": v[0], "n": v[1]} for k, v in sorted(traps.items())},
                "llm_calls": len(calls), "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in calls),
                "failed_calls": sum(not c["ok"] for c in calls), "seconds": round(elapsed, 1),
                "seconds_per_open_patient": round(elapsed / len(open_), 1) if open_ and method in ("agents", "single_prompt") else None,
                "guard_problems": guard_problems, "reviewer_rejections": rejected, "answers_accepted": answered,
                "errors": {p: t["error"] for p, t in traces.items() if "error" in t},
            }
            print(f"{nct} {method}: precision {m['precision']} recall {m['recall']} accuracy {m['accuracy']} "
                  f"review {m['needs_review']} missed {m['missed_as_ineligible']} ({elapsed:.0f}s)", flush=True)
    report["overall"] = overall(report)
    return report


def overall(report: dict) -> dict:
    out = {}
    methods = {m for t in report["trials"].values() for m in t["methods"]}
    for m in methods:
        rows = [t["methods"][m] for t in report["trials"].values() if m in t["methods"]]
        tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
        traps = defaultdict(lambda: [0, 0])
        for r in rows:
            for k, v in r["traps"].items():
                traps[k][0] += v["right"]
                traps[k][1] += v["n"]
        by_clinic = defaultdict(lambda: [0, 0])
        for r in rows:
            for k, v in r["recall_by"]["clinic"].items():
                by_clinic[k][0] += v["found"]
                by_clinic[k][1] += v["eligible"]
        out[m] = {"precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                  "accuracy": round(sum(r["exact"] for r in rows) / sum(r["n"] for r in rows), 3),
                  "needs_review": sum(r["needs_review"] for r in rows), "missed_as_ineligible": sum(r["missed_as_ineligible"] for r in rows),
                  "false_eligible": fp, "llm_calls": sum(r["llm_calls"] for r in rows), "tokens": sum(r["tokens"] for r in rows),
                  "seconds": round(sum(r["seconds"] for r in rows), 1),
                  "hard_n": sum(r["hard"]["n"] for r in rows), "hard_right": sum(r["hard"]["right"] for r in rows),
                  "hard_wrong": sum(r["hard"]["wrong"] for r in rows), "hard_review": sum(r["hard"]["review"] for r in rows),
                  "trap_accuracy": round(sum(v[0] for v in traps.values()) / max(1, sum(v[1] for v in traps.values())), 3),
                  "recall_by_clinic": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_clinic.items())}}
    return out


def markdown(report: dict) -> str:
    order = [m for m in ("rules", "rules_mapped", "single_prompt", "agents") if m in report["overall"]]
    o = report["overall"]
    lines = [f"# TrialIQ evaluation, {report['created']}", "",
             f"{report['patients']} synthetic patients, 3 trials, screening date {report['screen_date']}. "
             f"Models: {report['model']}.", "",
             f"Hard set: the {sum(t['hard_set']['n'] for t in report['trials'].values())} patient-trial pairs that the "
             "rules with LOINC mappings cannot decide. Trap rows count only hard-set patients.", "", "## Overall", "",
             "| | " + " | ".join(order) + " |", "|---|" + "---|" * len(order)]
    for key, name in [("hard_right", "Hard set: right"), ("hard_wrong", "Hard set: wrong"),
                      ("hard_review", "Hard set: left for review"), ("recall", "Recall of eligible"), ("precision", "Precision of eligible"), ("accuracy", "Exact label"),
                      ("needs_review", "Left for review"), ("missed_as_ineligible", "Eligible called ineligible"),
                      ("false_eligible", "Ineligible called eligible"), ("trap_accuracy", "Label right on trap patients"),
                      ("llm_calls", "Model calls"), ("tokens", "Tokens"), ("seconds", "Seconds")]:
        lines.append(f"| {name} | " + " | ".join(str(o[m][key]) for m in order) + " |")
    lines.append("| Eligible found, by clinic | " + " | ".join("<br>".join(f"{k.split()[0]} {v}" for k, v in o[m]["recall_by_clinic"].items())
                                                         for m in order) + " |")
    for nct, t in report["trials"].items():
        lines += ["", f"## {nct}", "", f"Gold: {t['gold']}. Hard set: {t['hard_set']}.", "", "| | " + " | ".join(order) + " |", "|---|" + "---|" * len(order)]
        for key in ("recall", "precision", "accuracy", "needs_review", "open_patients", "criterion_accuracy", "llm_calls",
                    "seconds_per_open_patient", "guard_problems", "reviewer_rejections"):
            lines.append(f"| {key} | " + " | ".join(str(t["methods"][m].get(key)) for m in order if m in t["methods"]) + " |")
        traps = sorted({k for m in order if m in t["methods"] for k in t["methods"][m]["traps"]})
        if traps:
            lines += ["", "| trap | " + " | ".join(order) + " |", "|---|" + "---|" * len(order)]
            for k in traps:
                lines.append(f"| {k} | " + " | ".join(
                    (lambda v: f"{v['right']}/{v['n']}" if v else "-")(t["methods"][m]["traps"].get(k)) for m in order if m in t["methods"]) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="rules,rules_mapped,single_prompt,agents")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--trials", default=",".join(TRIALS))
    args = ap.parse_args()
    report = run(args.methods.split(","), args.limit, args.trials.split(","))
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out / f"evals-{stamp}.json").write_text(json.dumps(report, indent=1))
    (out / f"evals-{stamp}.md").write_text(markdown(report))
    print(markdown(report))
    print(f"wrote reports/evals-{stamp}.md")


if __name__ == "__main__":
    main()
