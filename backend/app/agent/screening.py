"""The screening run, a LangGraph graph:

    START -> prescreen --(Send, one per open patient)--> chart_review -> adjudicate -> equity -> outreach
                    \\--(no open patient)-----------------------------/                              |
                                                                                approval (interrupt) |
                                                                    END <- finalize <---------------/

prescreen the engine applies the approved criteria and the approved LOINC mappings to all patients. No model.
chart_review one chart review per open patient, run in parallel up to TRIALIQ_CONCURRENCY.
adjudicate applies the checked answers; each patient gets eligible, ineligible or needs_review.
equity the funnel by race, ethnicity, sex, clinic and age band.
outreach a referral draft to the primary care doctor of each eligible patient. It sends nothing.
approval pauses the run until the coordinator approves, edits or rejects each draft. Chart.py. LangGraph interrupt.
              The checkpointer keeps the paused run, so it continues after a server restart.
finalize records the approved referrals in the outbox. With TRIALIQ_DRY_RUN=true nothing leaves. Default."""

import json
import operator
import re
import threading
from dataclasses import asdict
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from ..adjudicate import age_band, apply, equity, reasons, verdict
from ..criteria import CriteriaSet
from ..engine import Engine, PatientScreen, atom_specs, funnel
from .chart import ChartReviewer, build_questions, note_local, resolutions_to_dicts
from .guards import Deadline
from .schemas import Referral
from .telemetry import tagged

OUTREACH_PROMPT = """Write a short secure message from a clinical research coordinator to a patient's primary care
doctor. The patient may be eligible for a clinical trial; ask whether the doctor agrees that the study team contacts
the patient. Use only the facts given, in plain words; do not add facts. Minimum necessary: the patient's name and MRN,
the trial, and the screening facts listed. Do not promise eligibility: a study visit confirms it. Do not mention code
status or end-of-life care. Plain text, under 150 words."""


def _merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class ScreenState(TypedDict, total=False):
    nct_id: str
    funnel: dict
    candidates: list[str]
    reviews: Annotated[dict[str, list[dict]], _merge]
    verdicts: dict[str, str]
    equity: dict
    drafts: list[dict]
    decisions: list[dict]
    sent: Annotated[list[dict], operator.add]


class ScreenContext:
    """What the nodes need at run time. Not part of the checkpointed state."""

    def __init__(self, run_id: str, trial: dict, cset: CriteriaSet, ehr, store, models, settings, callbacks, deadline: Deadline,
                 mappings: dict[str, str] | None = None):
        self.run_id, self.trial, self.cset = run_id, trial, cset
        self.mappings = mappings or {}  # frozen when the run started: a later approval does not change this run
        self.errors: dict[str, str] = {}
        self.ehr, self.store, self.models, self.settings = ehr, store, models, settings
        self.callbacks, self.deadline = callbacks, deadline
        self.specs = atom_specs(cset)
        self.hints = {c.id: c.hints for c in cset.criteria if c.hints}
        self._screens: dict[str, PatientScreen] | None = None
        self._lock = threading.Lock()
        self.reviewed = 0

    def event(self, kind: str, agent: str = "", patient: str = "", text: str = "", data: dict | None = None) -> None:
        self.store.event(self.run_id, kind, agent, patient, text, data)

    def engine(self) -> Engine:
        return Engine(self.ehr, self.settings.screen_date, self.mappings)

    def screens(self) -> dict[str, PatientScreen]:
        with self._lock:
            if self._screens is None:
                self._screens = {s.person_id: s for s in self.engine().screen_all(self.cset)}
            return self._screens


def build_screening(ctx: ScreenContext, checkpointer):
    def prescreen(state: ScreenState) -> dict:
        ctx.store.update_run(ctx.run_id, stage="prescreen")
        screens = ctx.screens()
        f = funnel(list(screens.values()))
        candidates = [pid for pid, s in screens.items() if s.stage == "open"]
        questions = sum(len(build_questions(screens[p], ctx.specs, ctx.hints)) for p in candidates)
        ctx.event("stage", "prescreen", text=f"{f['pool']} patients: {f['excluded']} excluded, {f['eligible']} eligible, "
                                              f"{f['open']} to chart review, {f['missing']} missing data")
        ctx.store.update_run(ctx.run_id, summary={"funnel": f, "questions": questions, "mappings": len(ctx.mappings)})
        return {"funnel": f, "candidates": candidates, "reviews": {}}

    def route(state: ScreenState):
        if not state["candidates"]:
            return "adjudicate"
        ctx.store.update_run(ctx.run_id, stage="chart_review")
        return [Send("chart_review", {"pid": pid}) for pid in state["candidates"]]

    def chart_review(task: dict) -> dict:
        pid = task["pid"]
        saved = ctx.store.review(ctx.run_id, pid)
        if saved is not None:  # finished before a restart: reuse it
            return {"reviews": {pid: saved}}
        if ctx.deadline.expired:
            ctx.event("guard", "runner", pid, "time limit reached: left for a person")
            return {"reviews": {pid: []}}
        rec = ctx.ehr.get(pid)
        questions = build_questions(ctx.screens()[pid], ctx.specs, ctx.hints)
        reviewer = ChartReviewer(ctx.models, ctx.settings.screen_date, ctx.settings.chart_rounds, ctx.callbacks, ctx.event)
        try:
            resolutions = resolutions_to_dicts(reviewer.run(rec, questions, ctx.deadline))
        except Exception as exc:  # one failed patient does not stop the run; a person reviews it
            ctx.event("error", "chart", pid, f"chart review failed: {str(exc)[:200]}")
            resolutions = [{"question_id": "-", "concept": "chart review", "keys": [], "status": "unknown", "finding": "unclear",
                            "note_id": "", "quote": "", "value": None, "unit": None, "date": None, "reason": "",
                            "problems": [f"chart review failed: {str(exc)[:160]}"], "reviewer": "error", "round": 1,
                            "end_date": None}]
        ctx.store.save_review(ctx.run_id, pid, resolutions)
        with ctx._lock:
            ctx.store.update_run(ctx.run_id, summary={"reviewed": ctx.store.reviews_done(ctx.run_id)})
        return {"reviews": {pid: resolutions}}

    def adjudicate(state: ScreenState) -> dict:
        ctx.store.update_run(ctx.run_id, stage="adjudicate")
        verdicts, rows = {}, []
        for pid, screen in ctx.screens().items():
            stage = screen.stage
            resolutions = state.get("reviews", {}).get(pid, [])
            if resolutions:
                apply(screen, ctx.specs, ctx.cset, resolutions)
            v = verdict(screen)
            verdicts[pid] = v
            person = ctx.ehr.get(pid).person
            row = {"race": person.race, "ethnicity": person.ethnicity, "sex": person.sex, "clinic": person.clinic,
                   "age_band": age_band(person.age(ctx.settings.screen_date)), "stage": stage, "verdict": v}
            rows.append(row)
            ctx.store.save_result(ctx.run_id, pid, stage, v, {
                "person": {"name": person.name, "age": person.age(ctx.settings.screen_date), "sex": person.sex,
                           "race": person.race, "ethnicity": person.ethnicity, "clinic": person.clinic, "pcp": person.pcp},
                "reasons": reasons(screen), "screen": screen.to_dict(), "resolutions": resolutions,
                "review_error": next((r["problems"][0] for r in resolutions if r.get("reviewer") == "error"), "")})
        counts = {k: sum(1 for v in verdicts.values() if v == k) for k in ("eligible", "needs_review", "ineligible")}
        ctx.event("stage", "adjudicate", text=f"{counts['eligible']} eligible, {counts['needs_review']} need review")
        ctx.store.update_run(ctx.run_id, summary={"verdicts": counts})
        return {"verdicts": verdicts, "equity": equity(rows)}

    def equity_node(state: ScreenState) -> dict:
        ctx.store.update_run(ctx.run_id, summary={"equity": state["equity"]})
        return {}

    def outreach(state: ScreenState) -> dict:
        ctx.store.update_run(ctx.run_id, stage="outreach")
        eligible = [pid for pid, v in state["verdicts"].items() if v == "eligible"]
        llm = ctx.models.for_agent("outreach")
        if eligible:
            note_local(llm, "outreach", "", ctx.event)
        drafts = []
        for pid in eligible:
            rec = ctx.ehr.get(pid)
            p = rec.person
            screen = ctx.screens()[pid]
            facts = referral_facts(screen)
            not_screenable = [c.text for c in ctx.cset.criteria if not c.screenable]
            prompt = (f"Doctor: {p.pcp}\nPatient: {p.name}, MRN {pid}, age {p.age(ctx.settings.screen_date)}\n"
                      f"Trial: {ctx.trial['nct_id']} - {ctx.trial['title']}\nSites in Indiana: {', '.join(ctx.trial.get('indiana_sites') or [])}\n"
                      f"Screening facts:\n- " + "\n- ".join(facts) +
                      "\nThe study team still checks at the visit:\n- " + "\n- ".join(not_screenable[:3]))
            try:
                out: Referral = llm.with_structured_output(Referral).invoke(
                    [SystemMessage(OUTREACH_PROMPT), HumanMessage(prompt)], tagged("outreach", pid, ctx.callbacks))
                subject, body = out.subject.strip(), out.body.strip()
                if problem := referral_problem(subject + "\n" + body, pid, p.name, list(ctx.screens())):
                    ctx.event("security", "outreach", pid, f"referral draft blocked: {problem}",
                              {"type": "referral_blocked", "outcome": "blocked"})
                    raise ValueError(problem)
                if ctx.trial["nct_id"] not in subject + body:
                    subject = f"{subject} ({ctx.trial['nct_id']})"
            except Exception as exc:
                ctx.event("guard", "outreach", pid, f"referral draft replaced by the template: {str(exc)[:120]}")
                subject = f"Possible trial referral: {ctx.trial['nct_id']}"
                body = (f"Dear {p.pcp},\n\nYour patient {p.name} (MRN {pid}) may be eligible for {ctx.trial['title']} "
                        f"({ctx.trial['nct_id']}). Screening facts:\n- " + "\n- ".join(facts) +
                        "\n\nMay the study team contact the patient? A study visit confirms eligibility.")
            ctx.store.save_outreach(ctx.run_id, pid, subject, body, p.pcp)
            drafts.append({"person_id": pid, "subject": subject})
        ctx.event("stage", "outreach", text=f"{len(drafts)} referral draft(s)")
        return {"drafts": drafts}

    def approval(state: ScreenState) -> dict:
        if not state.get("drafts"):
            return {"decisions": []}
        ctx.store.update_run(ctx.run_id, stage="approval", status="awaiting_approval")
        decisions = interrupt({"drafts": state["drafts"]})
        ctx.store.update_run(ctx.run_id, status="running", stage="finalize")
        return {"decisions": decisions or []}

    def finalize(state: ScreenState) -> dict:
        sent = []
        for d in state.get("decisions", []):
            if d.get("decision") == "approve":
                status = "approved (dry run)" if ctx.settings.dry_run else "sent"
                ctx.store.decide_outreach(ctx.run_id, d["person_id"], status, d.get("actor", "coordinator"),
                                          d.get("subject"), d.get("body"))
                sent.append({"person_id": d["person_id"], "status": status})
            elif d.get("decision") == "reject":
                ctx.store.decide_outreach(ctx.run_id, d["person_id"], "rejected", d.get("actor", "coordinator"))
        ctx.event("stage", "finalize", text=f"{len(sent)} referral(s) approved")
        return {"sent": sent}

    g = StateGraph(ScreenState)
    g.add_node("prescreen", prescreen)
    g.add_node("chart_review", chart_review)
    g.add_node("adjudicate", adjudicate)
    g.add_node("equity", equity_node)
    g.add_node("outreach", outreach)
    g.add_node("approval", approval)
    g.add_node("finalize", finalize)
    g.add_edge(START, "prescreen")
    g.add_conditional_edges("prescreen", route, ["chart_review", "adjudicate"])
    g.add_edge("chart_review", "adjudicate")
    g.add_edge("adjudicate", "equity")
    g.add_edge("equity", "outreach")
    g.add_edge("outreach", "approval")
    g.add_edge("approval", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def referral_facts(screen: PatientScreen) -> list[str]:
    """Plain facts for the referral: the evidence of the inclusion criteria that pass, and one fixed line for the
    exclusions. Engine wording such as "no code and no mention in notes" never reaches the letter writer: a model can
    misread it as a clinical fact ("no code status")."""
    def found(atoms) -> list[str]:
        out = []
        for a in atoms:
            if a.children:
                out += found(a.children)
            elif a.status == "met" and not a.negate:
                out += [e for e in a.evidence if not e.startswith(("no ", "check:", "first code", "coded onset", "resolved"))][:1]
        return out
    lines = [f"{c.id}: {'; '.join(found(c.atoms)) or 'met'}" for c in screen.criteria if c.kind == "inclusion" and c.passes]
    lines.append("Exclusion criteria: none found in the record; the study visit confirms them.")
    return lines[:8]


BANNED = re.compile(r"\b(code status|no code|dnr|do not resuscitate|full code|comfort care|hospice)\b", re.I)


def referral_problem(text: str, pid: str, name: str, all_ids: list[str]) -> str:
    """Why a referral draft must not be used: another patient's MRN, no name and no MRN of this patient, a promise of
    eligibility, or a care decision (code status, hospice) that the screening does not know."""
    others = {m for m in re.findall(r"\bP\d{4}\b", text) if m != pid and m in set(all_ids)}
    if others:
        return f"mentions another patient ({', '.join(sorted(others))})"
    if pid not in text and name not in text:
        return "does not name the patient"
    if re.search(r"\b(is|are) (definitely |confirmed )?eligible\b", text, re.I) and "may be eligible" not in text.lower():
        return "promises eligibility"
    if m := BANNED.search(text):
        return f"states a care decision that the screening does not know ('{m.group(0)}')"
    return ""


def as_json(obj) -> str:
    return json.dumps(asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj, default=str)
