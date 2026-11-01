"""Prescreen engine: the criteria against the structured EHR data, without a model.

Each atom gets met, not_met or unknown. Unknown atoms are `open` when a note mentions the fact: then the chart
review agent reads the notes. Logic is three-valued: AND is false when one part is false, OR is true when
one part is true, else unknown. Kleene.

Rules for the absence of data:
- A diagnosis that has no code and that no note mentions is not_met. With a mention it is open.
- A medicine that is not in the medicine list and that no note mentions is not_met. A listed medicine that a later
  note says was stopped is open.
- A lab with no result in the window is unknown. It is open when a note or an unmapped local lab code may hold it."""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .criteria import Atom, CriteriaSet, Criterion, atom_label
from .ehr.store import EHR, Note, PatientRecord
from .units import UnitError, convert

STOP_WORDS = re.compile(r"\b(stop|stopped|discontinu\w*|not taking|held|intoleran\w*)\b", re.I)
MAX_HITS = 6


@dataclass
class Hit:
    note_id: str
    date: str
    type: str
    sentence: str


@dataclass
class AtomResult:
    key: str  # criterion id + atom path, e.g. "I4.2.1"
    label: str
    type: str
    status: str  # met | not_met | unknown
    evidence: list[str] = field(default_factory=list)
    open: bool = False
    hits: list[Hit] = field(default_factory=list)
    source: str = "structured"  # structured | notes. Set by the chart review.
    negate: bool = False
    children: list["AtomResult"] = field(default_factory=list)


@dataclass
class CriterionResult:
    id: str
    kind: str
    text: str
    status: str
    atoms: list[AtomResult]

    @property
    def fails(self) -> bool:
        return (self.kind == "inclusion" and self.status == "not_met") or (self.kind == "exclusion" and self.status == "met")

    @property
    def passes(self) -> bool:
        return (self.kind == "inclusion" and self.status == "met") or (self.kind == "exclusion" and self.status == "not_met")


@dataclass
class PatientScreen:
    person_id: str
    criteria: list[CriterionResult]

    @property
    def stage(self) -> str:
        """excluded | eligible | open | missing"""
        if any(c.fails for c in self.criteria):
            return "excluded"
        if all(c.passes for c in self.criteria):
            return "eligible"
        return "open" if self.open_atoms() else "missing"

    def open_atoms(self) -> list[AtomResult]:
        out = []

        def walk(a: AtomResult) -> None:
            if a.children:
                for c in a.children:
                    walk(c)
            elif a.open and a.status == "unknown":
                out.append(a)
        for c in self.criteria:
            if not c.passes and not c.fails:
                for a in c.atoms:
                    walk(a)
        return out

    def to_dict(self) -> dict:
        return {"person_id": self.person_id, "stage": self.stage, "criteria": [asdict(c) | {"fails": c.fails, "passes": c.passes}
                                                                              for c in self.criteria]}


def kleene_all(values: list[str]) -> str:
    if "not_met" in values:
        return "not_met"
    return "met" if all(v == "met" for v in values) else "unknown"


def kleene_any(values: list[str]) -> str:
    if "met" in values:
        return "met"
    return "not_met" if all(v == "not_met" for v in values) else "unknown"


def flip(status: str) -> str:
    return {"met": "not_met", "not_met": "met"}.get(status, status)


def _pattern(keyword: str) -> re.Pattern:
    flags = 0 if (len(keyword) <= 3 and keyword.isupper()) else re.I
    return re.compile(rf"(?<![\w-]){re.escape(keyword)}(?![\w-])", flags)


def note_hits(notes: list[Note], keywords: list[str], since: date | None = None, limit: int = MAX_HITS) -> list[Hit]:
    patterns = [_pattern(k) for k in keywords if k.strip()]
    hits = []
    for n in sorted(notes, key=lambda n: n.date, reverse=True):
        if since and n.date < since:
            continue
        for s in n.sentences():
            if any(p.search(s) for p in patterns):
                hits.append(Hit(n.note_id, n.date.isoformat(), n.type, s))
                if len(hits) >= limit:
                    return hits
    return hits


def in_range(value: float, a: Atom) -> bool:
    if a.min is not None and (value <= a.min if a.min_exclusive else value < a.min):
        return False
    if a.max is not None and (value >= a.max if a.max_exclusive else value > a.max):
        return False
    return True


def code_match(code: str, prefixes: list[str]) -> bool:
    """ICD-10-CM codes match by prefix: E11 matches E11.9 and E11.3513."""
    return any(code.startswith(p) for p in prefixes)


class Engine:
    def __init__(self, ehr: EHR, screen_date: date, mappings: dict[str, str] | None = None) -> None:
        self.ehr = ehr
        self.today = screen_date
        self.mappings = mappings or {}  # approved: local source code -> LOINC

    def since(self, a: Atom) -> date | None:
        return self.today - timedelta(days=a.lookback_days) if a.lookback_days else None

    # --- atoms ---------------------------------------------------------------------------------------------

    def atom(self, rec: PatientRecord, a: Atom, key: str, kind: str = "inclusion") -> AtomResult:
        label = atom_label(a)
        if a.type == "group":
            kids = [self.atom(rec, x, f"{key}.{i + 1}", kind) for i, x in enumerate(a.all_of)]
            status = kleene_all([k.status for k in kids])
            return AtomResult(key, label, "group", flip(status) if a.negate else status, negate=a.negate, children=kids)
        r = getattr(self, f"_{a.type}")(rec, a, AtomResult(key, label, a.type, "unknown", negate=a.negate))
        if a.type == "lab" and kind == "exclusion" and r.status == "unknown" and not r.open and not a.negate:
            # no result to show the exclusion: the screening visit measures it
            r.status = "not_met"
            r.evidence.append("no result: check at the screening visit")
        if a.negate:
            r.status = flip(r.status)
        return r

    def _age(self, rec: PatientRecord, a: Atom, r: AtomResult) -> AtomResult:
        age = rec.person.age(self.today)
        r.status = "met" if in_range(age, a) else "not_met"
        r.evidence.append(f"age {age} (born {rec.person.birth_date.isoformat()})")
        return r

    def _sex(self, rec: PatientRecord, a: Atom, r: AtomResult) -> AtomResult:
        r.status = "met" if rec.person.sex == (a.value or "") else "not_met"
        r.evidence.append(f"sex {rec.person.sex}")
        return r

    def _condition(self, rec: PatientRecord, a: Atom, r: AtomResult) -> AtomResult:
        coded = [c for c in rec.conditions if code_match(c["code"], a.codes)]
        since = self.since(a)
        if coded:
            for c in coded:
                r.evidence.append(f"{c['code']} {c['name']}, onset {c['onset']}" + (f", ended {c['end']}" if c["end"] else ""))
            if since:
                ok = [c for c in coded if c["onset"] >= since]
                if ok:
                    r.status = "met"
                    return r
                r.evidence.append(f"coded onset before {since}")
                # a newer episode can be in the notes only: a note in the window that mentions it opens the fact
                return self._from_notes(rec, a, r, since, "a note in the window mentions it")
            usable = coded if a.status == "ever" else [c for c in coded if c["end"] is None or c["end"] >= self.today]
            if not usable:
                r.evidence.append("resolved in the codes")
                last_end = max(c["end"] for c in coded if c["end"])
                return self._from_notes(rec, a, r, last_end + timedelta(days=1), "a later note mentions it")
            if a.min_duration_days:
                limit = self.today - timedelta(days=a.min_duration_days)
                if not any(c["onset"] <= limit for c in usable):
                    r.evidence.append(f"first code after {limit}")
                    # the first code is not always the diagnosis date: a note can show an earlier one
                    hits = note_hits(rec.notes, a.keywords)
                    r.status, r.open, r.hits = ("unknown", True, hits) if hits else ("not_met", False, [])
                    return r
            r.status = "met"
            return r
        return self._from_notes(rec, a, r, since, "no code; notes mention it", "no code and no mention in notes")

    @staticmethod
    def _from_notes(rec: PatientRecord, a: Atom, r: AtomResult, since: date | None, open_text: str,
                    closed_text: str = "") -> AtomResult:
        """Open the fact when a note mentions it, else not_met. Since `since`."""
        hits = note_hits(rec.notes, a.keywords, since)
        if hits:
            r.status, r.open, r.hits = "unknown", True, hits
            r.evidence.append(open_text)
        else:
            r.status = "not_met"
            if closed_text:
                r.evidence.append(closed_text)
        return r

    def _medication(self, rec: PatientRecord, a: Atom, r: AtomResult) -> AtomResult:
        listed = [d for d in rec.drugs if d["rxcui"] in a.codes]
        since = self.since(a)
        if since:
            exposed = []
            for d in listed:
                if d["end"] is not None and d["end"] < since:
                    continue
                days = ((d["end"] or self.today) - d["start"]).days
                if a.ignore_courses_up_to_days and days <= a.ignore_courses_up_to_days:
                    r.evidence.append(f"{d['name']} {d['start']} to {d['end']} ({days} d): short course, allowed")
                    continue
                exposed.append(d)
                r.evidence.append(f"{d['name']} {d['dose']}, {d['start']} to {d['end'] or 'now'}")
            if exposed:
                r.status = "met"
                return r
        else:
            current = [d for d in listed if d["end"] is None or d["end"] >= self.today]
            for d in current:
                r.evidence.append(f"{d['name']} {d['dose']} since {d['start']}")
            if current:
                limit = self.today - timedelta(days=a.min_duration_days or 0)
                if not any(d["start"] <= limit for d in current):
                    r.status = "not_met"
                    r.evidence.append(f"started after {limit}")
                    return r
                stopped = [h for h in note_hits(rec.notes, a.keywords, min(d["start"] for d in current), limit=200)
                           if STOP_WORDS.search(h.sentence)][:MAX_HITS]
                if stopped:
                    r.status, r.open, r.hits = "unknown", True, stopped
                    r.evidence.append("a later note may say it was stopped")
                    return r
                r.status = "met"
                return r
        hits = note_hits(rec.notes, a.keywords, since or self.today - timedelta(days=400))
        if hits:
            r.status, r.open, r.hits = "unknown", True, hits
            r.evidence.append("not in the medicine list; notes mention it")
        else:
            r.status = "not_met"
            r.evidence.append("not in the medicine list and not in notes")
        return r

    def lab_values(self, rec: PatientRecord, a: Atom) -> tuple[list[tuple[date, float, str]], list[str]]:
        values, notes = [], []
        for lab in rec.labs:
            loinc = lab["loinc"] or self.mappings.get(lab["source_code"])
            if loinc not in a.codes:
                continue
            try:
                v = convert(lab["value"], lab["unit"], a.unit)
            except UnitError as exc:
                notes.append(f"{lab['source_code']} on {lab['date']}: {exc}")
                continue
            how = lab["source_code"] + (" (mapped)" if not lab["loinc"] else "")
            if lab["unit"] != a.unit and a.unit:
                how += f", {lab['value']:g} {lab['unit']}"
            values.append((lab["date"], v, how))
        return values, notes

    def _lab(self, rec: PatientRecord, a: Atom, r: AtomResult) -> AtomResult:
        values, problems = self.lab_values(rec, a)
        r.evidence += problems
        since = self.since(a)
        window = [v for v in values if not since or v[0] >= since]
        if window:
            if a.aggregate == "any":
                hits = [v for v in window if in_range(v[1], a)]
                r.status = "met" if hits else "not_met"
                for d, v, how in (hits or window[-3:]):
                    r.evidence.append(f"{a.concept} {v:g} {a.unit or ''} on {d} [{how}]")
            else:
                d, v, how = max(window, key=lambda x: x[0])
                r.status = "met" if in_range(v, a) else "not_met"
                r.evidence.append(f"{a.concept} {v:g} {a.unit or ''} on {d} [{how}]")
            return r
        if values:
            d, v, _ = max(values, key=lambda x: x[0])
            r.evidence.append(f"last {a.concept} {v:g} on {d}, outside {a.lookback_days} d")
        else:
            r.evidence.append(f"no {a.concept} result")
        unmapped = [lab for lab in rec.labs if not lab["loinc"] and lab["source_code"] not in self.mappings
                    and (not since or lab["date"] >= since)
                    and any(k.lower() in lab["source_name"].lower() for k in a.keywords + [a.concept])]
        if unmapped:
            r.evidence.append(f"{len(unmapped)} result(s) under unmapped local code {unmapped[0]['source_code']}")
        hits = note_hits(rec.notes, a.keywords, since, limit=30)
        hits.sort(key=lambda h: not re.search(r"\d", re.sub(r"\d{1,2}/\d{1,2}/\d{4}", "", h.sentence)))  # values first
        r.open = bool(hits or unmapped)
        r.hits = hits[:MAX_HITS]
        return r

    def _note(self, rec: PatientRecord, a: Atom, r: AtomResult) -> AtomResult:
        hits = note_hits(rec.notes, a.keywords, self.since(a))
        if hits:
            r.status, r.open, r.hits = "unknown", True, hits
            r.evidence.append(f"{len(hits)} note mention(s)")
        else:
            r.status = "not_met"
            r.evidence.append("no mention in notes" + (f" in {a.lookback_days} d" if a.lookback_days else ""))
        return r

    # --- criteria ------------------------------------------------------------------------------------------

    def criterion(self, rec: PatientRecord, c: Criterion) -> CriterionResult:
        if not c.atoms:  # screenable but no computable rule: never treat it as passed
            return CriterionResult(c.id, c.kind, c.text, "unknown", [AtomResult(
                f"{c.id}.0", "no computable rule", "note", "unknown", ["no computable rule: a person checks it"])])
        atoms = [self.atom(rec, a, f"{c.id}.{i + 1}", c.kind) for i, a in enumerate(c.atoms)]
        statuses = [a.status for a in atoms]
        status = kleene_all(statuses) if c.combine == "all" else kleene_any(statuses)
        return CriterionResult(c.id, c.kind, c.text, status, atoms)

    def screen(self, rec: PatientRecord, cset: CriteriaSet) -> PatientScreen:
        return PatientScreen(rec.person.person_id, [self.criterion(rec, c) for c in cset.criteria if c.screenable])

    def screen_all(self, cset: CriteriaSet) -> list[PatientScreen]:
        return [self.screen(rec, cset) for rec in self.ehr.records.values()]


def recombine(c: CriterionResult, combine: str) -> None:
    """After the chart review changed atom statuses: compute the groups and the criterion again."""
    def walk(a: AtomResult) -> str:
        if a.children:
            status = kleene_all([walk(k) for k in a.children])
            a.status = flip(status) if a.negate else status
        return a.status
    statuses = [walk(a) for a in c.atoms]
    c.status = kleene_all(statuses) if combine == "all" else kleene_any(statuses)


def funnel(screens: list[PatientScreen]) -> dict[str, int]:
    out = {"pool": len(screens), "excluded": 0, "eligible": 0, "open": 0, "missing": 0}
    for s in screens:
        out[s.stage] += 1
    return out


def atom_specs(cset: CriteriaSet) -> dict[str, Atom]:
    """Atom key -> atom, with the same keys as AtomResult.key."""
    out: dict[str, Atom] = {}

    def walk(a: Atom, key: str) -> None:
        out[key] = a
        for i, x in enumerate(a.all_of):
            walk(x, f"{key}.{i + 1}")
    for c in cset.criteria:
        for i, a in enumerate(c.atoms):
            walk(a, f"{c.id}.{i + 1}")
    return out
