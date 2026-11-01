"""Reflection agent: from coordinator feedback to proposed lessons.

For each feedback item it reads the criterion, what the system decided and with what evidence, what the coordinator says is right and why, and the note sentences
about the concept. It proposes the smallest lesson that would have prevented the error. learning.check_lesson then
checks each lesson; a person approves it. The prescreen facts, the chart agent's quotes and the guard problems.

The agent sees patient notes, so it uses the chart level: the local model unless cloud models may read notes."""

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .. import security
from ..criteria import CriteriaSet
from ..engine import note_hits
from ..learning import leaves
from .chart import note_local
from .telemetry import tagged

REFLECT_PROMPT = """You improve a clinical trial screening system from a coordinator's correction. For each feedback
item, find why the system was wrong and propose the smallest fix:
- add_keyword: a word or phrase that finds this fact in notes and was missing (copy it exactly from the note sentences)
- remove_keyword: a keyword that matched the wrong sentences (e.g. a word that also appears in family history)
- add_code: a code from the EHR that the value set missed
- hint: one short sentence that tells the chart agent how to read such notes, e.g. "IV furosemide during a hospital
  stay is not a regular oral loop diuretic". No numbers.
- data_gap: the data needed is not in the record; say what to collect
- protocol_check: the coordinator's view and the protocol text differ; a person must read the protocol
Never propose to change a threshold, a unit, a time window or the logic. atom_index is the 1-based number of the fact
in the criterion's list. Cite the feedback id."""


class LessonProposal(BaseModel):
    feedback_id: int
    kind: str = Field(description="add_keyword, remove_keyword, add_code, hint, data_gap or protocol_check")
    criterion_id: str
    atom_index: int | None = Field(None, description="1-based fact number in the criterion, for keywords and codes")
    value: str = Field("", description="The keyword, the code, the hint sentence, or what to collect/check")
    rationale: str = Field(description="One sentence: why the system was wrong and how this fixes it")


class LessonProposals(BaseModel):
    lessons: list[LessonProposal]


def _criterion_block(cset: CriteriaSet, cid: str) -> str:
    c = next((c for c in cset.criteria if c.id == cid), None)
    if c is None:
        return f"{cid}: (not in the criteria)"
    facts = "\n".join(f"   fact {i + 1}: {a.type} '{a.concept}' keywords={a.keywords} codes={a.codes[:8]}"
                      for i, a in enumerate(leaves(c.atoms)))
    hints = f"\n   hints: {c.hints}" if c.hints else ""
    return f"{c.id} ({c.kind}): {c.text}\n{facts}{hints}"


def describe(item: dict, result: dict | None, cset: CriteriaSet, rec) -> str:
    """The evidence of one feedback item as text for the agent."""
    lines = [f"FEEDBACK {item['id']}: patient {item['person_id']}, {item['kind']} "
             f"{item['criterion_id'] or 'verdict'}: system said '{item['system_value']}', coordinator says "
             f"'{item['correct_value']}'. Reason: {item['reason'] or '(none given)'}"]
    criteria = [item["criterion_id"]] if item["criterion_id"] else \
        [c["id"] for c in (result or {}).get("data", {}).get("screen", {}).get("criteria", []) if not c["passes"]]
    for cid in criteria[:3]:
        lines.append("CRITERION " + _criterion_block(cset, cid))
        crit = next((c for c in (result or {}).get("data", {}).get("screen", {}).get("criteria", []) if c["id"] == cid), None)
        if crit:
            def walk(atoms, depth=0):
                for a in atoms:
                    if a["children"]:
                        walk(a["children"], depth + 1)
                    else:
                        lines.append(f"   result {a['label']}: {a['status']} ({'; '.join(a['evidence'][:3])})")
            walk(crit["atoms"])
        for r in (result or {}).get("data", {}).get("resolutions", []):
            if any(k.split(".")[0] == cid for k in r.get("keys", [])):
                lines.append(f"   chart agent: {r['finding']} \"{r['quote']}\" ({r['note_id']}) problems={r['problems']} "
                             f"reviewer={r['reviewer']}")
        c = next((c for c in cset.criteria if c.id == cid), None)
        if c and rec is not None:
            words = list({w for a in leaves(c.atoms) for w in a.keywords + [a.concept]})
            for h in note_hits(rec.notes, words, limit=8):
                lines.append(f"   note {h.note_id} ({h.type}, {h.date}): {security.screen_text(h.sentence)[0]}")
    return "\n".join(lines)


class Reflector:
    def __init__(self, models, callbacks=None, on_event=None) -> None:
        self.models = models
        self.callbacks = callbacks or []
        self.on_event = on_event or (lambda *a, **k: None)

    def propose(self, items: list[dict], results: dict, cset: CriteriaSet, ehr) -> list[LessonProposal]:
        out: list[LessonProposal] = []
        llm = self.models.for_agent("reflection")
        note_local(llm, "reflection", "", self.on_event)
        for start in range(0, len(items), 4):
            batch = items[start:start + 4]
            text = "\n\n".join(describe(i, results.get((i["run_id"], i["person_id"])), cset,
                                        ehr.records.get(i["person_id"])) for i in batch)
            answer: LessonProposals = llm.with_structured_output(LessonProposals).invoke(
                [SystemMessage(REFLECT_PROMPT), HumanMessage(text)], tagged("reflection", callbacks=self.callbacks))
            got = answer.lessons if answer else []
            self.on_event("stage", agent="reflection", text=f"{len(got)} lesson(s) from {len(batch)} feedback item(s)")
            ids = {i["id"] for i in batch}
            out += [lp for lp in got if lp.feedback_id in ids]
        return out
