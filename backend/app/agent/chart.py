"""Chart review of one patient: the open facts of the prescreen become questions, a chart agent answers them from
the notes with tools, the code checks each answer, and a reviewer agent checks what the code cannot.

    questions -> chart agent (tool loop, then structured answers) -> guards -> evidence reviewer
                      ^                                                         |
                      \\------ rejected answers, one time, with the reason -----/

The model never decides a threshold: for a lab it gives the number, the unit and the date, and the code converts
the unit and compares. A finding is about the fact; the code applies negation and the criterion logic."""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .. import security
from ..criteria import Atom
from ..ehr.store import PatientRecord
from ..engine import Hit, PatientScreen
from .guards import Deadline, check_answer, lab_unit, value_from_quote
from .schemas import ChartAnswers, ReviewVerdicts
from .telemetry import tagged
from .tools import chart_tools

CHART_PROMPT = """You are a chart abstraction agent for clinical trial screening. You answer questions about ONE
patient from the patient's notes. Rules:
- Use only the notes and tool results. Never guess.
- A fact about a family member ("mother", "father", "brother", "sister") is NOT about the patient: absent.
- A negated fact ("no", "denies", "not", "declined", "not listed", "not enrolled") is absent.
- A resolved or past fact is absent when the question asks about now. For a time window, do not judge the window
  yourself: if the fact is in the notes, answer present with its date. The code checks the window.
- "mild" is not "severe"; "nonproliferative" is not "proliferative"; "evaluated for" is not "had".
- For a lab or measurement question give the number and the unit exactly as written (do not convert units) and the
  date of the result.
- If the snippets are not enough, use the tools: search_notes, read_note, list_lab_results, list_medications.
When you have enough, stop calling tools and say DONE."""

ANSWER_PROMPT = """Answer every question now as JSON. For each question: finding (present, absent or unclear), the
note_id and an exact quote of at most 25 words from that note, and for a lab question the value, the unit as written
and the date (YYYY-MM-DD). Use unclear when the notes do not decide it."""

REVIEW_PROMPT = """You review a chart abstractor's answers for clinical trial screening. For each answer you get the
question, the finding, the quote and the note text around the quote. ok=true only when the quote is about this patient
(not a family member), is not negated or hypothetical, and supports the finding. A finding of "absent" is ok when the
context shows the fact is negated, about a family member, resolved, or mild instead of severe.
Do NOT judge dates or time windows: the code checks every date against the window. Name the problem in a few words
when not ok."""


@dataclass
class Question:
    id: str
    atom: Atom
    keys: list[str]
    hits: list[Hit]
    hints: list[str] = field(default_factory=list)  # from approved lessons of the criterion

    def text(self, today: date) -> str:
        base = self._text(today)
        return base + "".join(f" Hint: {h}" for h in self.hints)

    def _text(self, today: date) -> str:
        a = self.atom
        window = f" in the last {a.lookback_days} days (since {date.fromordinal(today.toordinal() - a.lookback_days)})" if a.lookback_days else ""
        if a.type == "lab":
            if a.aggregate == "any":
                return f"Is there a {a.concept} result{window} in the range of the criterion? Give the value, unit and date."
            return f"What is the most recent {a.concept} result{window}? Give the value, unit and date."
        if a.type == "medication":
            if a.lookback_days:
                skip = (f" Give the start date and the stop date of each course: the code drops courses of at most "
                        f"{a.ignore_courses_up_to_days} days.") if a.ignore_courses_up_to_days else ""
                return f"Did the patient take {a.concept}{window}?{skip}"
            since = f" Give the start date (it must be at least {a.min_duration_days} days ago)." if a.min_duration_days else ""
            return (f"Is the patient taking {a.concept} now?{since} If a note says it was stopped or the patient is not "
                    f"taking it, the answer is absent, even when the medicine list still shows it.")
        dated = " Give the date of the event as written in the note (for a hospital stay, the admission date)." if a.lookback_days else ""
        if a.type == "condition":
            when = window or (" at any time in the past or now" if a.status == "ever" else " now (active, not resolved)")
            since = f" Give the date of diagnosis (it must be at least {a.min_duration_days} days ago)." if a.min_duration_days else ""
            return f"Does the patient have {a.concept}{when}?{since}{dated}"
        return f"Do the notes show {a.concept} for the patient{window}?{dated}"


@dataclass
class Resolution:
    question_id: str
    concept: str
    keys: list[str]
    status: str  # met | not_met | unknown: about the fact, before negate
    finding: str = "unclear"
    note_id: str = ""
    quote: str = ""
    value: float | None = None
    unit: str | None = None
    date: str | None = None
    reason: str = ""
    problems: list[str] = field(default_factory=list)
    reviewer: str = ""  # approved | rejected: <problem> | skipped
    round: int = 1
    end_date: str | None = None


def _fact_key(a: Atom) -> tuple:
    return (a.type, a.concept.lower(), tuple(sorted(a.codes)), a.lookback_days, a.min_duration_days, a.aggregate,
            a.ignore_courses_up_to_days, a.status, a.min, a.max)


def build_questions(screen: PatientScreen, specs: dict[str, Atom], hints: dict[str, list[str]] | None = None) -> list[Question]:
    """One question per distinct open fact. A lab question does not depend on the threshold, so 'LVEF < 25' and
    'LVEF < 30' are one question; the code applies each threshold to the one value. `hints`: criterion id -> the
    reading hints of its approved lessons."""
    by_fact: dict[tuple, Question] = {}
    for result in screen.open_atoms():
        atom = specs[result.key]
        key = _fact_key(atom) if atom.type != "lab" else ("lab", atom.concept.lower(), tuple(sorted(atom.codes)),
                                                           atom.lookback_days, atom.aggregate)
        if key not in by_fact:
            by_fact[key] = Question(f"Q{len(by_fact) + 1}", atom, [], result.hits)
        q = by_fact[key]
        q.keys.append(result.key)
        for h in (hints or {}).get(result.key.split(".")[0], []):
            if h not in q.hints:
                q.hints.append(h)
    return list(by_fact.values())


def _context(note_text: str, quote: str, width: int = 260) -> str:
    low, q = note_text.lower(), quote.lower().strip().strip("\"'")[:40]
    at = low.find(q[:25]) if q else -1
    if at < 0:
        return note_text[:2 * width]
    return note_text[max(0, at - width): at + len(quote) + width]


def run_tool_loop(llm, tools, system: str, task: str, rounds: int, config: dict, deadline: Deadline | None,
                  on_tool=None, on_event=None) -> list:
    """The agent may call only the tools on its permission list. Tool output is screened for instructions hidden in
    the data before the model reads it."""
    agent, patient = config["metadata"].get("agent", ""), config["metadata"].get("patient", "")
    on_event = on_event or (lambda *a, **k: None)
    tools, removed = security.permit(agent, tools)
    if removed:
        on_event("security", agent=agent, patient=patient, text=f"tools not on the permission list: {', '.join(removed)}",
                 data={"type": "tool_blocked", "outcome": "blocked"})
    bound = llm.bind_tools(tools) if tools else llm
    by_name = {t.name: t for t in tools}
    messages = [SystemMessage(system), HumanMessage(task)]
    for _ in range(rounds):
        if deadline and deadline.expired:
            break
        reply = bound.invoke(messages, config)
        messages.append(reply)
        if not getattr(reply, "tool_calls", None):
            break
        for call in reply.tool_calls:
            started = time.perf_counter()
            fn = by_name.get(call["name"])
            try:
                output = fn.invoke(call["args"]) if fn else f"Blocked: {call['name']} is not a tool of this agent."
                ok = fn is not None
            except Exception as exc:  # a bad argument goes back to the model, so it can correct the call
                output, ok = f"Tool error: {exc}", False
            if fn is None:
                on_event("security", agent=agent, patient=patient, text=f"call to {call['name']} blocked: not on the permission list",
                         data={"type": "tool_blocked", "outcome": "blocked"})
            output, found = security.screen_text(str(output))
            if found:
                on_event("security", agent=agent, patient=patient,
                         text=f"{call['name']}: removed instruction-like text: \"{found[0][:140]}\"",
                         data={"type": "injection", "outcome": "blocked"})
            messages.append(ToolMessage(str(output), tool_call_id=call["id"], name=call["name"]))
            if on_tool:
                on_tool(call["name"], call["args"], str(output), ok, round((time.perf_counter() - started) * 1000))
    return messages


def note_local(llm, agent: str, patient: str, on_event) -> None:
    """Records each cloud model that this call skipped because patient data stays local."""
    held = [p for p, reason in getattr(llm, "skipped", []) if reason == "patient data stays local"]
    if held:
        on_event("security", agent=agent, patient=patient, text=f"patient data kept local: {', '.join(held)} skipped",
                 data={"type": "egress_blocked", "outcome": "blocked"})


def transcript(messages: list) -> str:
    """The tool results as plain text, for the structured answer call. Works for every provider."""
    parts = []
    for m in messages:
        if isinstance(m, ToolMessage):
            parts.append(f"--- {m.name} result ---\n{m.content}")
        elif isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip() and m.content.strip() != "DONE":
            parts.append(f"--- your note ---\n{m.content.strip()[:600]}")
    return "\n".join(parts)


class ChartReviewer:
    def __init__(self, models, today: date, rounds: int = 4, callbacks: list | None = None, on_event=None) -> None:
        self.models = models
        self.today = today
        self.rounds = rounds
        self.callbacks = callbacks or []
        self.on_event = on_event or (lambda *a, **k: None)

    def _task(self, rec: PatientRecord, questions: list[Question], feedback: dict[str, str] | None = None,
              previous: dict[str, "Resolution"] | None = None) -> str:
        p = rec.person
        lines = [f"Patient {p.person_id}: {p.sex}, age {p.age(self.today)}. Screening date: {self.today}.",
                 "Notes: " + ", ".join(f"{n.note_id} ({n.type}, {n.date})" for n in rec.notes), "", "QUESTIONS:"]
        for q in questions:
            lines.append(f"{q.id}. {q.text(self.today)}")
            if feedback and q.id in feedback:
                last = (previous or {}).get(q.id)
                said = f" You answered '{last.finding}' with the quote \"{last.quote}\"." if last else ""
                lines.append(f"   {said} The reviewer rejected it: {feedback[q.id]}. Answer again.")
            for h in q.hits[:4]:
                sentence, found = security.screen_text(h.sentence)
                if found:
                    self.on_event("security", agent="chart", patient=p.person_id,
                                  text=f"snippet {h.note_id}: removed instruction-like text: \"{found[0][:140]}\"",
                                  data={"type": "injection", "outcome": "blocked"})
                lines.append(f"    snippet [{h.note_id} | {h.date} | {h.type}] {sentence}")
        return "\n".join(lines)

    def answer(self, rec: PatientRecord, questions: list[Question], deadline: Deadline | None,
               feedback: dict[str, str] | None = None, previous: dict | None = None) -> list:
        pid = rec.person.person_id
        llm = self.models.for_agent("chart")
        note_local(llm, "chart", pid, self.on_event)
        config = tagged("chart", pid, self.callbacks)
        task = self._task(rec, questions, feedback, previous)

        def on_tool(name, args, output, ok, ms):
            self.on_event("tool", agent="chart", patient=pid, text=f"{name}({json.dumps(args)[:120]})",
                          data={"output": output[:600], "ok": ok, "ms": ms})
        messages = run_tool_loop(llm, chart_tools(rec), CHART_PROMPT, task, self.rounds, config, deadline, on_tool,
                                 self.on_event)
        tools_text = transcript(messages)
        prompt = f"{task}\n\nTOOL RESULTS:\n{tools_text or '(none)'}\n\n{ANSWER_PROMPT}"
        out: ChartAnswers = llm.with_structured_output(ChartAnswers).invoke(
            [SystemMessage(CHART_PROMPT), HumanMessage(prompt)], config)
        return out.answers if out else []

    def review(self, rec: PatientRecord, questions: list[Question], resolutions: list[Resolution]) -> dict[str, str]:
        """question id -> problem, for the answers that the reviewer rejects."""
        notes = {n.note_id: n for n in rec.notes}
        items = [r for r in resolutions if r.status != "unknown" and r.note_id in notes]
        if not items:
            return {}
        qtext = {q.id: q.text(self.today) for q in questions}
        lines = []
        for r in items:
            n = notes[r.note_id]
            lines.append(f"{r.question_id}. QUESTION: {qtext[r.question_id]}\n   FINDING: {r.finding}"
                         f"{f' (value {r.value:g} {r.unit or ''}, {r.date or n.date})' if r.value is not None else ''}\n"
                         f"   QUOTE: \"{r.quote}\"\n   NOTE {n.note_id} ({n.type}, {n.date}): "
                         f"...{security.screen_text(_context(n.text, r.quote))[0]}...")
        llm = self.models.for_agent("evidence_reviewer")
        out: ReviewVerdicts = llm.with_structured_output(ReviewVerdicts).invoke(
            [SystemMessage(REVIEW_PROMPT), HumanMessage(f"Patient {rec.person.person_id}. Screening date {self.today}.\n\n"
                                                        + "\n".join(lines))],
            tagged("evidence_reviewer", rec.person.person_id, self.callbacks))
        rejected = {}
        for v in (out.verdicts if out else []):
            if not v.ok and v.question_id in {r.question_id for r in items}:
                rejected[v.question_id] = v.problem or "not supported"
        return rejected

    def resolve(self, rec: PatientRecord, questions: list[Question], answers: list, round_: int) -> list[Resolution]:
        notes = {n.note_id: n for n in rec.notes}
        by_id = {a.question_id.strip().upper(): a for a in answers}
        out = []
        for q in questions:
            a = by_id.get(q.id)
            if a is None:
                out.append(Resolution(q.id, q.atom.concept, q.keys, "unknown", problems=["no answer"], round=round_))
                continue
            if q.atom.type == "lab" and a.value is None and a.finding == "present":
                if (found := value_from_quote(a.quote)) is not None:
                    a = a.model_copy(update={"value": found})
            if q.atom.type == "lab" and a.value is not None:
                unit, _ = lab_unit(a, q.atom)  # store the unit the code used, so adjudication converts the same way
                a = a.model_copy(update={"unit": unit})
            status, problems = check_answer(a, q.atom, notes, rec.person.person_id, self.today)
            out.append(Resolution(q.id, q.atom.concept, q.keys, status, a.finding, a.note_id, a.quote, a.value, a.unit,
                                  a.date, a.reason, problems, round=round_, end_date=a.end_date))
        return out

    def run(self, rec: PatientRecord, questions: list[Question], deadline: Deadline | None = None) -> list[Resolution]:
        pid = rec.person.person_id
        self.on_event("stage", agent="chart", patient=pid, text=f"{len(questions)} question(s)")
        resolutions = self.resolve(rec, questions, self.answer(rec, questions, deadline), 1)
        for r in resolutions:
            if r.problems:
                self.on_event("guard", agent="guards", patient=pid, text=f"{r.question_id}: {'; '.join(r.problems)}")
        rejected = self.review(rec, questions, resolutions)
        for r in resolutions:
            r.reviewer = f"rejected: {rejected[r.question_id]}" if r.question_id in rejected else (
                "approved" if r.status != "unknown" else "skipped")
        if rejected and not (deadline and deadline.expired):
            self.on_event("review", agent="evidence_reviewer", patient=pid,
                          text="; ".join(f"{k}: {v}" for k, v in rejected.items()))
            again = [q for q in questions if q.id in rejected]
            previous = {r.question_id: r for r in resolutions}
            retry = self.resolve(rec, again, self.answer(rec, again, deadline, rejected, previous), 2)
            still = self.review(rec, again, retry)
            fixed = {r.question_id: r for r in retry}
            for i, r in enumerate(resolutions):
                if r.question_id not in fixed:
                    continue
                new = fixed[r.question_id]
                if r.question_id in still or new.status == "unknown":
                    new.status = "unknown"
                    new.reviewer = f"rejected: {still.get(r.question_id, 'unresolved')}"
                    new.problems.append("needs a person: the reviewer did not accept the evidence")
                else:
                    new.reviewer = "approved"
                resolutions[i] = new
        elif rejected:
            for r in resolutions:
                if r.question_id in rejected:
                    r.status = "unknown"
        self.on_event("stage", agent="evidence_reviewer", patient=pid,
                      text=f"{sum(r.status != 'unknown' for r in resolutions)}/{len(resolutions)} resolved")
        return resolutions


def resolutions_to_dicts(resolutions: list[Resolution]) -> list[dict]:
    return [asdict(r) for r in resolutions]
