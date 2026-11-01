"""Deterministic checks on agent output. A model can invent a quote, a number, a date or a code; these checks find
that before a person sees the result.

check_answer   the chart agent's answer: the note is the patient's, the quote is in that note, the value is in the
               quote, the unit converts, the date is in the window. The code then decides met or not_met from the
               value, not the model.
unverified_codes
               codes that the protocol agents wrote but that no tool returned.
Deadline       a time limit for one run.
"""

import re
import time
from datetime import date, timedelta

from ..criteria import Atom
from ..engine import in_range
from ..units import UnitError, alternatives, convert, unit_after

_PUNCT = re.compile(r"[^\w.%/]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _flat(text: str) -> str:
    return _PUNCT.sub(" ", text.lower()).replace(" .", " ").strip()


def quote_in_text(quote: str, text: str) -> bool:
    """True when each part of the quote (split at '...') is in the text, ignoring case, spaces and punctuation."""
    parts = [p for p in re.split(r"\.\.\.|…", quote.strip().strip("\"'“”")) if p.strip()]
    flat = f" {_flat(text)} "
    if not parts or len(_flat(" ".join(parts))) < 6:  # a quote of a few letters proves nothing
        return False
    return all(_flat(p) and _flat(p).rstrip(".") in flat for p in parts)


def value_in_quote(value: float, quote: str) -> bool:
    """The value is a number of the quote, not a part of a date. 12 is not in 09/12/2026."""
    text = _DATE_IN_TEXT.sub(" ", quote.replace(",", ""))
    return any(abs(float(n) - value) < 1e-6 for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text))


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    text = text.strip()
    for pattern, build in ((r"^(\d{4})-(\d{2})-(\d{2})", lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
                           (r"^(\d{1,2})/(\d{1,2})/(\d{4})", lambda m: date(int(m[3]), int(m[1]), int(m[2]))),
                           (r"^(\d{4})-(\d{2})$", lambda m: date(int(m[1]), int(m[2]), 1)),
                           (r"^(\d{4})$", lambda m: date(int(m[1]), 1, 1))):
        if m := re.match(pattern, text):
            try:
                return build(m)
            except ValueError:
                return None
    return None


_DATE_IN_TEXT = re.compile(r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}")
_STOPPED = re.compile(r"\b(?:stopped|discontinued|ended|held)\b\D{0,12}(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", re.I)
_FOR_DAYS = re.compile(r"\bfor (\d{1,3}) days?\b", re.I)


def course(answer) -> tuple[date | None, date | None]:
    """Start and stop of a medicine course: from the answer, else from two dates in the quote."""
    start, end = parse_date(answer.date), parse_date(answer.end_date)
    if not (start and end):
        found = [parse_date(d) for d in _DATE_IN_TEXT.findall(answer.quote or "")]
        found = [d for d in found if d]
        if len(found) >= 2:
            start, end = found[0], found[1]
        elif not end and (m := _STOPPED.search(answer.quote or "")):
            end = parse_date(m.group(1))  # "stopped 01/21/2026": the date of the stop
    return start, end


def _days_in(quote: str) -> int | None:
    m = _FOR_DAYS.search(quote or "")
    return int(m.group(1)) if m else None


_SINCE_YEAR = re.compile(r"\b(?:since|in|diagnosed)\s+((?:19|20)\d{2})\b|\(((?:19|20)\d{2})\)", re.I)


def quoted_date(quote: str) -> date | None:
    """A date that the quote itself gives: 09/12/2026, 2026-09-12, 'since 2014' or '(2015)'. It wins over the date
    the model writes, because the model can copy the note date instead."""
    if m := _DATE_IN_TEXT.search(quote or ""):
        return parse_date(m.group(0))
    if m := _SINCE_YEAR.search(quote or ""):
        return date(int(m.group(1) or m.group(2)), 1, 1)
    return None


def value_from_quote(quote: str) -> float | None:
    """The value of a lab answer that left it out: the one number of the quote, dates not counted."""
    text = _DATE_IN_TEXT.sub(" ", quote or "").replace(",", "")
    numbers = [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text)]  # not the 1 of "A1c"
    return numbers[0] if len(numbers) == 1 else None


def supported_date(answer, note) -> date | None:
    """The date of a fact, only when the evidence shows it: a date in the quote, or the model's date when the note
    has that date written in it. A date that only the model gives is not evidence."""
    if found := quoted_date(answer.quote):
        return found
    d = parse_date(answer.date)
    if d and (d.isoformat() in note.text or d.strftime("%m/%d/%Y") in note.text
              or f"{d.month}/{d.day}/{d.year}" in note.text):
        return d
    if "discharge" in note.type.lower() and (m := _ADMISSION.search(note.text)):
        return parse_date(m.group(1))  # a discharge summary documents one stay: its admission date dates the event
    return None


_ADMISSION = re.compile(r"\bAdmission:\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", re.I)


def lab_unit(answer, atom: Atom) -> tuple[str | None, str]:
    """The unit of a lab value: the unit written after the value in the quote wins over the model's unit. With no
    unit at all, the criterion's unit is used only when the analyte has one unit (eGFR); else the unit is unknown."""
    in_quote = unit_after(answer.value, answer.quote)
    if in_quote:
        return in_quote, ""
    if answer.unit:
        return answer.unit, ""
    if not alternatives(atom.unit):
        return atom.unit, ""
    return None, f"no unit for {answer.value:g}, and {atom.concept} has more than one unit"


def check_answer(answer, atom: Atom, notes: dict, person_id: str, today: date) -> tuple[str, list[str]]:
    """The status that the code accepts for this answer and the problems found. Met, not_met, unknown.
    `notes`: note_id -> Note of this patient. Statuses are about the fact; the caller applies `negate`."""
    problems: list[str] = []
    if answer.finding == "unclear":
        return "unknown", problems
    note = notes.get(answer.note_id)
    if note is None:
        return "unknown", [f"note {answer.note_id or '(none)'} is not a note of patient {person_id}"]
    if not answer.quote.strip() or not quote_in_text(answer.quote, note.text):
        return "unknown", [f"the quote is not in note {answer.note_id}"]
    dated = supported_date(answer, note)
    when = dated or note.date
    if when > today:
        problems.append(f"date {when} is after the screening date")
        return "unknown", problems
    since = today - timedelta(days=atom.lookback_days) if atom.lookback_days else None

    if atom.type == "lab":
        if answer.finding == "absent":
            # "no value in range" can close an any-value question; a latest-value question needs the value
            return ("not_met", problems) if atom.aggregate == "any" else ("unknown", ["a lab question needs a value"])
        if answer.value is None:
            return "unknown", ["no value for a lab question"]
        if not value_in_quote(answer.value, answer.quote):
            return "unknown", [f"the value {answer.value:g} is not in the quote"]
        if since and when < since:
            return "unknown", [f"the result of {when} is older than {atom.lookback_days} days"]
        unit, problem = lab_unit(answer, atom)
        if unit is None:
            return "unknown", [problem]
        try:
            value = convert(answer.value, unit, atom.unit)
        except UnitError as exc:
            return "unknown", [str(exc)]
        return ("met" if in_range(value, atom) else "not_met"), problems

    if answer.finding == "present":
        if atom.type == "medication" and atom.ignore_courses_up_to_days:
            start, end = course(answer)
            days = (end - start).days if start and end else _days_in(answer.quote)
            if days is not None and 0 <= days <= atom.ignore_courses_up_to_days:
                return "not_met", [f"course of {days} days: allowed"]
            if end and since and end < since:
                return "not_met", [f"stopped {end}, before the window"]
        ongoing_medicine = atom.type == "medication" and answer.end_date is None
        if since and not ongoing_medicine:
            if dated is None and atom.type in ("condition", "note"):
                # an event in a window needs its date in the evidence: "history of stroke" has no date
                return "unknown", ["no date in the evidence for an event in a window: a person checks it"]
            if when < since:
                # the fact is real but outside the window: for an event in a window, it does not count
                return "not_met", [f"{when} is older than the {atom.lookback_days}-day window; counted as absent"]
        if atom.min_duration_days:
            if dated is None:
                return "unknown", [f"no start date in the evidence; it must be at least {atom.min_duration_days} days ago"]
            if dated > today - timedelta(days=atom.min_duration_days):
                return "not_met", [f"started {dated}, less than {atom.min_duration_days} days ago"]
        return "met", problems
    return "not_met", problems


def unverified_codes(codes: list[str], tool_text: str) -> list[str]:
    return [c for c in codes if c and c not in tool_text]


class Deadline:
    def __init__(self, seconds: float | None) -> None:
        self.end = time.monotonic() + seconds if seconds else None

    @property
    def expired(self) -> bool:
        return self.end is not None and time.monotonic() > self.end
