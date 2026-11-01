"""Protocol agents. They see no patient data, so they can use cloud models.

Criteria builder: eligibility text -> computable criteria, for a person to approve.
    split (code) -> criteria agent (per chunk) -> terminology agent (tool loop per criterion) -> guards -> reviewer
    The quote of each criterion must be in the protocol text, and each code must be in a tool result.

LOINC mapper: local lab codes without LOINC -> proposals, for a person to approve.
    mapper agent (tool loop: search_loinc, search_ehr_concepts) -> guards (code in tool output, unit converts)"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from ..criteria import Atom, CriteriaSet, Criterion
from ..ehr.vocab import LABS, LOINC_ANALYTE
from ..units import UnitError, convert
from .chart import run_tool_loop, transcript
from .guards import Deadline, quote_in_text, unverified_codes
from .schemas import CodeChoices, CriteriaReview, DraftCriteria, MappingProposal
from .telemetry import tagged
from .tools import protocol_tools

CRITERIA_PROMPT = """You turn clinical trial eligibility criteria into computable criteria for EHR prescreening.
For each criterion give: kind (inclusion or exclusion), text (copy the protocol words exactly), screenable (false
when EHR data cannot decide it: consent, willingness, investigator judgment, central lab at the visit, study partner),
combine (all or any) and atoms. Atom types: age, sex, condition, lab, medication, note (a fact found only in notes,
e.g. NYHA class, hospice), group (all_of atoms joined with AND, for 'X and Y, or Z and W').
For lab atoms set min/max with min_exclusive/max_exclusive for < and >, the unit, and lookback_days for windows such
as 'within 3 months' (90). For 'at least N days before screening' use min_duration_days. Use negate for 'not' or
'without'. keywords: 2-5 words a clinician writes in notes for the fact. Leave codes empty: another agent finds them."""

TERMINOLOGY_PROMPT = """You find standard codes for one eligibility criterion. Use the tools: search_ehr_concepts
(codes this EHR uses), search_icd10 (ICD-10-CM; give the shortest code prefix that covers the concept, e.g. E11 for
type 2 diabetes), search_rxnorm (one ingredient per call) and search_loinc. Prefer codes that the EHR uses. When you
have the codes, stop and say DONE."""

CODES_PROMPT = """Now give the codes for each atom as JSON: criterion_id, atom_index (1-based) and codes. Copy codes
exactly from the tool results. Conditions: ICD-10-CM prefixes. Medications: RxNorm ingredient RXCUIs. Labs: LOINC codes."""

REVIEW_PROMPT = """You review computable eligibility criteria against the protocol text. Check: every screenable
criterion of the protocol is present; inclusion and exclusion are not swapped; thresholds, units and windows match the
text; exceptions in the text are kept; codes fit the concept (for example no type 1 diabetes codes for type 2).
approved=true only if there is no problem. Name the criterion id in each problem."""

MAPPER_PROMPT = """You map a local laboratory code to LOINC. Search LOINC with search_loinc using the words of the
local name (expand abbreviations: HGB=hemoglobin, ALB=albumin, CREAT=creatinine, MALB=microalbumin, POC=point of
care, GFR EST=estimated glomerular filtration rate). Check that the LOINC component, specimen and scale fit the
local test and its unit. Keep the method: CKD-EPI 2021 is not MDRD and not the 2009 CKD-EPI; IFCC (mmol/mol) is
not NGSP (%). When you have the best code, stop and say DONE."""

MAPPING_ANSWER = """Give the mapping as JSON: source_code, loinc (copied from the search_loinc results, or empty if
none fits), confidence (high, medium, low) and a one-sentence reason that names the component and the unit."""

_ITEM = re.compile(r"^\s*(?:[*\-•]|\d+[.)]|[a-z][.)])\s+", re.M)


def split_items(text: str) -> list[tuple[str, str]]:
    """for each bullet of the eligibility text. A line that names the section switches the kind. Kind, item."""
    kind, items = "inclusion", []
    for line in text.replace("\\", "").splitlines():
        low = line.strip().lower()
        if not low:
            continue
        if low.startswith("inclusion criteria"):
            kind = "inclusion"
            continue
        if low.startswith("exclusion criteria"):
            kind = "exclusion"
            continue
        item = _ITEM.sub("", line).strip()
        if len(item) > 12:
            items.append((kind, item))
    return items


def _number(criteria: list[Criterion]) -> None:
    counts = {"I": 0, "E": 0, "N": 0}
    for c in criteria:
        prefix = "N" if not c.screenable else ("I" if c.kind == "inclusion" else "E")
        counts[prefix] += 1
        c.id = f"{prefix}{counts[prefix]}"


def _atoms(c: Criterion):
    for i, a in enumerate(c.atoms):
        yield i + 1, a
        for j, b in enumerate(a.all_of):
            yield (i + 1) * 100 + j + 1, b  # nested atoms: 101, 102 ... for atom 1


class CriteriaBuilder:
    def __init__(self, models, ehr, cache, callbacks=None, on_event=None, deadline: Deadline | None = None) -> None:
        self.models, self.ehr, self.cache = models, ehr, cache
        self.callbacks = callbacks or []
        self.on_event = on_event or (lambda *a, **k: None)
        self.deadline = deadline

    def extract(self, trial: dict, chunk: int = 4) -> tuple[list[Criterion], list[str]]:
        text = trial["eligibility"].replace("\\", "")
        items = split_items(trial["eligibility"])
        llm = self.models.for_agent("criteria")
        criteria, warnings = [], []
        for start in range(0, len(items), chunk):
            if self.deadline and self.deadline.expired:
                warnings.append("time limit reached during extraction")
                break
            part = items[start:start + chunk]
            prompt = "\n".join(f"[{k}] {t}" for k, t in part)
            out: DraftCriteria = llm.with_structured_output(DraftCriteria).invoke(
                [SystemMessage(CRITERIA_PROMPT), HumanMessage(f"Trial {trial['nct_id']}. Criteria:\n{prompt}")],
                tagged("criteria", callbacks=self.callbacks))
            for c in (out.criteria if out else []):
                if not quote_in_text(c.text, text):
                    warnings.append(f"text not in the protocol, dropped: {c.text[:80]}")
                    continue
                criteria.append(c)
            self.on_event("stage", agent="criteria", text=f"{len(criteria)} criteria from {start + len(part)}/{len(items)} items")
        _number(criteria)
        return criteria, warnings

    def find_codes(self, criteria: list[Criterion]) -> list[str]:
        tools = protocol_tools(self.ehr, self.cache)
        llm = self.models.for_agent("terminology")
        warnings = []
        for c in criteria:
            coded = [(i, a) for i, a in _atoms(c) if a.type in ("condition", "medication", "lab")]
            if not coded or (self.deadline and self.deadline.expired):
                continue
            task = f"Criterion {c.id} ({c.kind}): {c.text}\nAtoms:\n" + "\n".join(
                f"{i}. {a.type}: {a.concept}" for i, a in coded)

            def on_tool(name, args, output, ok, ms, cid=c.id):
                self.on_event("tool", agent="terminology", text=f"{cid} {name}({json.dumps(args)[:100]})",
                              data={"output": output[:500], "ok": ok, "ms": ms})
            config = tagged("terminology", callbacks=self.callbacks)
            messages = run_tool_loop(llm, tools, TERMINOLOGY_PROMPT, task, 5, config, self.deadline, on_tool, self.on_event)
            seen = transcript(messages)
            out: CodeChoices = llm.with_structured_output(CodeChoices).invoke(
                [SystemMessage(TERMINOLOGY_PROMPT), HumanMessage(f"{task}\n\nTOOL RESULTS:\n{seen}\n\n{CODES_PROMPT}")], config)
            by_index = dict(coded)
            for choice in (out.choices if out else []):
                atom = by_index.get(choice.atom_index)
                if atom is None:
                    continue
                bad = unverified_codes(choice.codes, seen)
                if bad:
                    warnings.append(f"{c.id}: codes not in any tool result, dropped: {', '.join(bad)}")
                atom.codes = [x for x in choice.codes if x not in bad]
            for i, a in coded:
                if not a.codes:
                    warnings.append(f"{c.id}.{i}: no codes for {a.concept}; only notes can decide it")
        return warnings

    def review(self, trial: dict, criteria: list[Criterion]) -> CriteriaReview:
        llm = self.models.for_agent("criteria_reviewer")
        draft = json.dumps([c.model_dump(exclude_defaults=True) for c in criteria], indent=0)[:14000]
        out = llm.with_structured_output(CriteriaReview).invoke(
            [SystemMessage(REVIEW_PROMPT), HumanMessage(f"PROTOCOL TEXT:\n{trial['eligibility']}\n\nDRAFT:\n{draft}")],
            tagged("criteria_reviewer", callbacks=self.callbacks))
        return out or CriteriaReview(approved=False, problems=["no review"])

    def run(self, trial: dict) -> tuple[CriteriaSet, list[str]]:
        criteria, warnings = self.extract(trial)
        warnings += self.find_codes(criteria)
        review = self.review(trial, criteria)
        self.on_event("review", agent="criteria_reviewer", text="approved" if review.approved else "; ".join(review.problems)[:400])
        warnings += [f"reviewer: {p}" for p in review.problems]
        return CriteriaSet(nct_id=trial["nct_id"], criteria=criteria, source="agent_draft"), warnings


METHODS = [("ckdepi2021", ("ckdepi21", "ckdepi2021")), ("mdrd", ("mdrd",)), ("ckdepi", ("ckdepi",)), ("ifcc", ("ifcc",))]


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def loinc_name(code: str, tool_text: str) -> str:
    for line in tool_text.splitlines():
        if line.startswith(f"{code} |"):
            return line.split("|")[1].strip()
    return ""


def method_conflict(local_name: str, loinc_long_name: str) -> str:
    """A method in one name that the other name does not have, e.g. local CKD-EPI 2021 against LOINC MDRD."""
    local, loinc = _squash(local_name), _squash(loinc_long_name)
    if not loinc:
        return ""
    for method, marks in METHODS:
        in_local = any(m in local for m in marks)
        in_loinc = method in loinc or any(m in loinc for m in marks)
        if method == "ckdepi2021":
            in_loinc = "ckdepi2021" in loinc
        if in_local != in_loinc and (in_local or method in ("mdrd", "ckdepi2021")):
            if in_loinc and not in_local and method == "ckdepi2021":
                continue  # the LOINC code is more specific than the local name: allowed
            return f"method differs: local '{local_name}' vs LOINC '{loinc_long_name}'"
    return ""


class LoincMapper:
    def __init__(self, models, ehr, cache, callbacks=None, on_event=None) -> None:
        self.models, self.ehr, self.cache = models, ehr, cache
        self.callbacks = callbacks or []
        self.on_event = on_event or (lambda *a, **k: None)

    def map_one(self, code: dict) -> tuple[MappingProposal, list[str]]:
        tools = [t for t in protocol_tools(self.ehr, self.cache) if t.name in ("search_loinc", "search_ehr_concepts")]
        llm = self.models.for_agent("mapper")
        samples = [lab for rec in self.ehr.records.values() for lab in rec.labs if lab["source_code"] == code["source_code"]][:5]
        task = (f"Local code {code['source_code']}: '{code['source_name']}', unit {code['unit']}, clinic {code['clinic']}, "
                f"{code['row_count']} results. Sample values: {', '.join(f'{s['value']:g}' for s in samples)}.")

        def on_tool(name, args, output, ok, ms):
            self.on_event("tool", agent="mapper", text=f"{code['source_code']} {name}({json.dumps(args)[:100]})",
                          data={"output": output[:500], "ok": ok, "ms": ms})
        config = tagged("mapper", callbacks=self.callbacks)
        messages = run_tool_loop(llm, tools, MAPPER_PROMPT, task, 4, config, None, on_tool, self.on_event)
        seen = transcript(messages)
        out: MappingProposal = llm.with_structured_output(MappingProposal).invoke(
            [SystemMessage(MAPPER_PROMPT), HumanMessage(f"{task}\n\nTOOL RESULTS:\n{seen}\n\n{MAPPING_ANSWER}")], config)
        if out is None:
            raise RuntimeError("no answer from the model")
        out.source_code = code["source_code"]
        problems = []
        if out.loinc and (conflict := method_conflict(code["source_name"], loinc_name(out.loinc, seen))):
            problems.append(conflict)
            out.confidence = "low"
        if out.loinc and out.loinc not in seen:
            problems.append(f"{out.loinc} is not in any search result")
            out.loinc, out.confidence = "", "low"
        analyte = LOINC_ANALYTE.get(out.loinc)
        if out.loinc and analyte and analyte in LABS:
            try:
                convert(1.0, code["unit"], LABS[analyte][2])
            except UnitError as exc:
                problems.append(f"unit check: {exc}")
                out.confidence = "low"
        elif out.loinc:
            problems.append("unit check not possible: LOINC code not in the analyte table")
        return out, problems

    def run(self, codes: list[dict]) -> list[tuple[MappingProposal, list[str]]]:
        out = []
        for code in codes:
            try:
                proposal, problems = self.map_one(code)
            except Exception as exc:  # one code that fails does not stop the others; a person maps it
                proposal = MappingProposal(source_code=code["source_code"], loinc="", confidence="low",
                                           reason=f"The mapper failed: {str(exc)[:120]}")
                problems = ["map it by hand"]
            self.on_event("stage", agent="mapper", text=f"{code['source_code']} -> {proposal.loinc or 'none'} ({proposal.confidence})")
            out.append((proposal, problems))
        return out


def compare_to_approved(draft: CriteriaSet, approved: CriteriaSet) -> dict:
    """How close an agent draft is to the approved criteria: for each approved screenable criterion, the draft
    criterion with the same kind and the most shared words, and whether thresholds and codes match."""
    def words(t: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9.]+", t.lower()) if len(w) > 2}

    def leaves(c: Criterion) -> list[Atom]:
        return [b for a in c.atoms for b in ([a] if a.type != "group" else a.all_of)]
    rows = []
    for gold in [c for c in approved.criteria if c.screenable]:
        candidates = [d for d in draft.criteria if d.kind == gold.kind]
        best = max(candidates, key=lambda d: len(words(d.text) & words(gold.text)), default=None)
        overlap = len(words(best.text) & words(gold.text)) / max(1, len(words(gold.text))) if best else 0
        if not best or overlap < 0.5:
            rows.append({"id": gold.id, "found": False})
            continue
        g_atoms, d_atoms = leaves(gold), leaves(best)
        thresholds = all(any(d.min == g.min and d.max == g.max for d in d_atoms) for g in g_atoms if g.type == "lab")
        g_codes = {x for a in g_atoms for x in a.codes}
        d_codes = {x for a in d_atoms for x in a.codes}
        code_recall = (len({g for g in g_codes if any(g.startswith(d) or d.startswith(g) for d in d_codes)}) / len(g_codes)
                       if g_codes else None)
        rows.append({"id": gold.id, "found": True, "draft_id": best.id, "screenable": best.screenable,
                     "thresholds": thresholds, "code_recall": round(code_recall, 2) if code_recall is not None else None})
    found = [r for r in rows if r["found"]]
    return {"rows": rows, "recall": round(len(found) / max(1, len(rows)), 2),
            "thresholds_ok": round(sum(r["thresholds"] for r in found) / max(1, len(found)), 2),
            "code_recall": round(sum(r["code_recall"] or 0 for r in found if r["code_recall"] is not None)
                                 / max(1, sum(r["code_recall"] is not None for r in found)), 2)}
