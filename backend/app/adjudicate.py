"""Apply the checked chart review answers to the prescreen result, and give each patient a verdict.

- eligible: every screenable criterion passes. The coordinator still checks the criteria that are not screenable.
- ineligible: a criterion fails
- needs_review: else; the open facts are listed for a person"""

from collections import Counter, defaultdict

from .criteria import Atom, CriteriaSet
from .engine import AtomResult, PatientScreen, flip, in_range, recombine
from .units import UnitError, convert


def _walk(atoms: list[AtomResult]):
    for a in atoms:
        yield a
        yield from _walk(a.children)


def apply(screen: PatientScreen, specs: dict[str, Atom], cset: CriteriaSet, resolutions: list[dict]) -> None:
    index = {a.key: a for c in screen.criteria for a in _walk(c.atoms)}
    for r in resolutions:
        for key in r["keys"]:
            a, spec = index.get(key), specs.get(key)
            if a is None or spec is None:
                continue
            status = r["status"]
            if spec.type == "lab" and status != "unknown" and r.get("value") is not None:
                if not r.get("unit"):  # the guard accepted the value only with a unit; no unit here means no decision
                    status = "unknown"
                try:
                    value = convert(r["value"], r.get("unit"), spec.unit)
                    status = "met" if in_range(value, spec) else "not_met" if status != "unknown" else "unknown"
                except UnitError:
                    status = "unknown"
            if spec.negate:
                status = flip(status)
            a.status = status
            a.source = "notes"
            if r.get("quote"):
                a.evidence.append(f"{r['note_id']}: \"{r['quote']}\"" + (f" → {r['value']:g} {r.get('unit') or ''}"
                                                                          if r.get("value") is not None else ""))
            for problem in r.get("problems", []):
                a.evidence.append(f"check: {problem}")
            if status != "unknown":
                a.open = False
    combine = {c.id: c.combine for c in cset.criteria}
    for c in screen.criteria:
        recombine(c, combine[c.id])


def verdict(screen: PatientScreen) -> str:
    if any(c.fails for c in screen.criteria):
        return "ineligible"
    if all(c.passes for c in screen.criteria):
        return "eligible"
    return "needs_review"


def reasons(screen: PatientScreen) -> list[str]:
    out = []
    for c in screen.criteria:
        if c.fails:
            out.append(f"{c.id} {'not met' if c.kind == 'inclusion' else 'present'}")
        elif not c.passes:
            out.append(f"{c.id} open")
    return out


GROUPS = ("race", "ethnicity", "sex", "clinic", "age_band")


def age_band(age: int) -> str:
    return "<50" if age < 50 else "50-64" if age < 65 else "65-74" if age < 75 else "75+"


def equity(rows: list[dict]) -> dict:
    """Funnel by group. rows: person attributes + prescreen stage + final verdict. A group with a much lower share
    of decided patients points to missing or unmapped data in that group, not to a property of its patients."""
    out = {}
    for g in GROUPS:
        table: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            t = table[r[g]]
            t["pool"] += 1
            t["structured_eligible"] += r["stage"] == "eligible"
            t["needed_notes"] += r["stage"] in ("open", "missing")
            t["eligible"] += r["verdict"] == "eligible"
            t["needs_review"] += r["verdict"] == "needs_review"
        out[g] = {k: dict(v) for k, v in sorted(table.items())}
    return out
