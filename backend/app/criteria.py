"""Computable eligibility criteria: the format of the criteria agent's output, of the approved criteria in the
trial library, and of the input of the prescreen engine.

A criterion has atoms. An atom is one fact about the patient: an age range, a diagnosis, a medicine, a lab value
or a fact that only notes hold. `combine` joins the atoms. A group atom joins its `all_of` atoms with
AND, for rules such as "eGFR 30-59 and UACR 30-5000, or eGFR 60-75 and UACR 500-5000". All or any."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

AtomType = Literal["age", "sex", "condition", "lab", "medication", "note", "group"]


class Atom(BaseModel):
    type: AtomType
    concept: str = Field(description="What is checked, in a few words, e.g. 'HbA1c' or 'type 2 diabetes'")
    codes: list[str] = Field(default_factory=list,
                             description="ICD-10-CM code prefixes (condition), LOINC codes (lab) or RxNorm ingredient RXCUIs (medication)")
    keywords: list[str] = Field(default_factory=list, description="Words that find this fact in clinical notes")
    min: float | None = None
    max: float | None = None
    min_exclusive: bool = False
    max_exclusive: bool = False
    unit: str | None = Field(None, description="Unit of min and max, e.g. '%', 'mg/g', 'kg/m2'")
    value: str | None = Field(None, description="For sex: 'F' or 'M'")
    lookback_days: int | None = Field(None, description="Only data or events in this many days before screening")
    min_duration_days: int | None = Field(None, description="Diagnosis or medicine start at least this many days before screening")
    status: Literal["active", "ever"] = "active"
    aggregate: Literal["latest", "any"] = Field("latest", description="Lab: use the latest value, or any value in the window")
    ignore_courses_up_to_days: int | None = Field(None, description="Medicine courses of at most this many days do not count")
    negate: bool = Field(False, description="True: the atom holds when the fact is absent")
    all_of: list["Atom"] = Field(default_factory=list, description="Only for type 'group': atoms that must all hold")


class Criterion(BaseModel):
    id: str = Field(description="I1, I2... for inclusion; E1, E2... for exclusion; N1... for criteria that are not screenable")
    kind: Literal["inclusion", "exclusion"]
    text: str = Field(description="The words of the protocol, copied exactly")
    screenable: bool = Field(True, description="False when EHR data cannot decide it, e.g. consent or a central lab at the visit")
    combine: Literal["all", "any"] = "all"
    atoms: list[Atom] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list, description="Reading hints for the chart agent, from approved lessons")


class CriteriaSet(BaseModel):
    nct_id: str
    screen_date: str | None = None
    criteria: list[Criterion]
    approved_by: str | None = None
    approved_at: str | None = None
    source: Literal["approved", "agent_draft"] = "approved"
    version: int = 1
    changes: list[str] = Field(default_factory=list, description="What changed in each version, newest last")


LIBRARY = Path(__file__).resolve().parent / "trials" / "library"


def load_approved(nct_id: str) -> CriteriaSet:
    return CriteriaSet.model_validate(json.loads((LIBRARY / f"{nct_id}.criteria.json").read_text()))


def screenable(cset: CriteriaSet) -> list[Criterion]:
    return [c for c in cset.criteria if c.screenable]


def atom_label(a: Atom) -> str:
    """A short readable label, e.g. 'HbA1c 7.0-10.5 % '. 180 d."""
    if a.type == "group":
        return " + ".join(atom_label(x) for x in a.all_of)
    parts = [("no " if a.negate else "") + a.concept]
    if a.min is not None or a.max is not None:
        lo = "" if a.min is None else (">" if a.min_exclusive else "≥") + f"{a.min:g}"
        hi = "" if a.max is None else ("<" if a.max_exclusive else "≤") + f"{a.max:g}"
        parts.append(f"{lo}{' ' if lo and hi else ''}{hi}{' ' + a.unit if a.unit else ''}".strip())
    if a.min_duration_days:
        parts.append(f"≥{a.min_duration_days} d")
    if a.lookback_days:
        parts.append(f"in {a.lookback_days} d")
    return " ".join(parts)
