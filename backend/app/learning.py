"""Learning from coordinator decisions.

A coordinator marks a verdict or a criterion as wrong and says why. The reflection agent proposes a
lesson for each feedback item. The code checks each lesson, previews its effect on all patients without a model,
and a person approves it. An approved lesson makes a new version of the criteria. Feedback.

What a lesson can do:
- add_keyword / remove_keyword   change the words that find a fact in the notes
- add_code                       add a code that the EHR uses to a value set
- hint                           a reading hint for the chart agent, e.g. "IV furosemide in hospital is not an oral
                                 loop diuretic"
- data_gap / protocol_check      no change: a note for a person
What a lesson can never do: change a threshold, a unit, a time window, the logic, or the kind of a criterion. Those
come from the protocol."""

import copy
import re
from collections import Counter

from .criteria import Atom, CriteriaSet
from .ehr.store import EHR
from .engine import Engine, _pattern

KINDS = ("add_keyword", "remove_keyword", "add_code", "hint", "data_gap", "protocol_check")
CHANGES = {"add_keyword", "remove_keyword", "add_code", "hint"}
_ICD = re.compile(r"^[A-TV-Z]\d{2}(\.\w{1,4})?$")
_LOINC = re.compile(r"^\d{1,7}-\d$")
_RXCUI = re.compile(r"^\d{2,8}$")


def leaves(atoms: list[Atom]) -> list[Atom]:
    out = []
    for a in atoms:
        out += leaves(a.all_of) if a.type == "group" else [a]
    return out


def target_atom(cset: CriteriaSet, criterion_id: str, atom_index: int | None) -> Atom | None:
    """The leaf atom that a lesson changes: by 1-based index over the criterion's leaf atoms."""
    c = next((c for c in cset.criteria if c.id == criterion_id), None)
    if c is None:
        return None
    atoms = leaves(c.atoms)
    if not atoms:
        return None
    i = (atom_index or 1) - 1
    return atoms[i] if 0 <= i < len(atoms) else None


def check_lesson(lesson: dict, cset: CriteriaSet, ehr: EHR, patient_ids: list[str]) -> list[str]:
    """Why a lesson must not be used. Empty: it may go to a person for approval."""
    kind, value = lesson.get("kind"), (lesson.get("value") or "").strip()
    problems = []
    if kind not in KINDS:
        return [f"unknown kind {kind}"]
    if kind in ("data_gap", "protocol_check"):
        return []
    c = next((c for c in cset.criteria if c.id == lesson.get("criterion_id")), None)
    if c is None:
        return [f"unknown criterion {lesson.get('criterion_id')}"]
    if not value:
        return ["empty value"]
    if kind == "hint":
        if re.search(r"\d", value):
            problems.append("a hint cannot hold numbers: thresholds and windows come from the protocol")
        if len(value) > 200:
            problems.append("a hint must be short (200 characters)")
        if re.search(r"\b(ignore|always|never mind|disregard)\b", value, re.I):
            problems.append("a hint cannot tell the agent to ignore evidence")
        return problems
    atom = target_atom(cset, c.id, lesson.get("atom_index"))
    if atom is None:
        return [f"{c.id} has no atom {lesson.get('atom_index')}"]
    if kind == "add_keyword":
        if len(value) < 3:
            problems.append("a keyword needs at least 3 characters")
        elif re.search(r"\d", value) or len(value) > 40:
            problems.append("a keyword is a short phrase without numbers (doses and values belong to the protocol)")
        elif value.lower() in (k.lower() for k in atom.keywords):
            problems.append("the keyword is already there")
        else:
            notes = [n for pid in patient_ids if pid in ehr.records for n in ehr.get(pid).notes]
            if not any(_pattern(value).search(n.text) for n in notes):
                problems.append("the keyword is not in the notes of the feedback patients: no evidence for it")
    elif kind == "remove_keyword":
        if value.lower() not in (k.lower() for k in atom.keywords):
            problems.append("the atom has no such keyword")
        elif len(atom.keywords) <= 1:
            problems.append("an atom needs at least one keyword")
    elif kind == "add_code":
        fmt = {"condition": _ICD, "lab": _LOINC, "medication": _RXCUI}.get(atom.type)
        if fmt is None or not fmt.match(value):
            problems.append(f"'{value}' is not a {atom.type} code")
        elif value in atom.codes:
            problems.append("the code is already there")
        else:
            used = {c["code"] for r in ehr.records.values() for c in r.conditions} | \
                   {d["rxcui"] for r in ehr.records.values() for d in r.drugs} | \
                   {lab["loinc"] for r in ehr.records.values() for lab in r.labs if lab["loinc"]}
            if not any(u == value or u.startswith(value) for u in used):
                problems.append("this EHR does not use the code")
    return problems


def apply_lesson(cset: CriteriaSet, lesson: dict) -> CriteriaSet:
    """A new criteria set with the lesson applied. The thresholds, windows and logic stay the same."""
    new = copy.deepcopy(cset)
    kind, value = lesson["kind"], lesson["value"].strip()
    if kind not in CHANGES:
        return new
    c = next(c for c in new.criteria if c.id == lesson["criterion_id"])
    if kind == "hint":
        if value not in c.hints:
            c.hints.append(value)
        return new
    atom = target_atom(new, c.id, lesson.get("atom_index"))
    if kind == "add_keyword":
        atom.keywords.append(value)
    elif kind == "remove_keyword":
        atom.keywords = [k for k in atom.keywords if k.lower() != value.lower()]
    elif kind == "add_code":
        atom.codes.append(value)
    return new


def impact(ehr: EHR, today, mappings: dict, before: CriteriaSet, after: CriteriaSet, focus: list[str]) -> dict:
    """The prescreen of all patients with the old and the new criteria, without a model: which patients change stage
    (excluded, eligible, open, missing), and what happens to the patients of the feedback."""
    a = {s.person_id: s for s in Engine(ehr, today, mappings).screen_all(before)}
    b = {s.person_id: s for s in Engine(ehr, today, mappings).screen_all(after)}
    moved = {pid: (a[pid].stage, b[pid].stage) for pid in a if a[pid].stage != b[pid].stage}
    open_before = sum(len(s.open_atoms()) for s in a.values())
    open_after = sum(len(s.open_atoms()) for s in b.values())
    newly_excluded = [pid for pid, (x, y) in moved.items() if y == "excluded" and pid not in focus]
    return {
        "changed": len(moved),
        "warnings": [f"{len(newly_excluded)} other patient(s) would become excluded: check them before you approve"]
        if newly_excluded else [],
        "moves": dict(Counter(f"{x}→{y}" for x, y in moved.values())),
        "patients": sorted(moved)[:40],
        "open_facts": [open_before, open_after],
        "focus": {pid: [a[pid].stage, b[pid].stage] for pid in focus if pid in a},
    }
