"""In-memory view of the EHR for screening. The database is small, so the app loads it once per screening run."""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import db

SENTENCE = re.compile(r"(?<=[.;])\s+|\n+")


@dataclass
class Person:
    person_id: str
    name: str
    birth_date: date
    sex: str
    race: str
    ethnicity: str
    clinic: str
    pcp: str

    def age(self, on: date) -> int:
        b = self.birth_date
        return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


@dataclass
class Note:
    note_id: str
    person_id: str
    date: date
    type: str
    author: str
    text: str

    def sentences(self) -> list[str]:
        return [s.strip() for s in SENTENCE.split(self.text) if s.strip()]


@dataclass
class PatientRecord:
    person: Person
    conditions: list[dict] = field(default_factory=list)
    drugs: list[dict] = field(default_factory=list)
    labs: list[dict] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


def _d(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


class EHR:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        con = db.connect(self.path)
        try:
            self.records: dict[str, PatientRecord] = {}
            for r in con.execute("SELECT * FROM person ORDER BY person_id"):
                self.records[r["person_id"]] = PatientRecord(Person(
                    r["person_id"], r["name"], _d(r["birth_date"]), r["sex"], r["race"], r["ethnicity"], r["clinic"], r["pcp"]))
            for r in con.execute("SELECT * FROM condition_occurrence"):
                self.records[r["person_id"]].conditions.append(
                    {"code": r["code"], "name": r["name"], "onset": _d(r["onset_date"]), "end": _d(r["end_date"])})
            for r in con.execute("SELECT * FROM drug_exposure"):
                self.records[r["person_id"]].drugs.append(
                    {"rxcui": r["rxcui"], "name": r["name"], "dose": r["dose"], "start": _d(r["start_date"]), "end": _d(r["end_date"])})
            for r in con.execute("SELECT * FROM measurement ORDER BY date"):
                self.records[r["person_id"]].labs.append(
                    {"date": _d(r["date"]), "source_code": r["source_code"], "source_name": r["source_name"],
                     "loinc": r["loinc"], "value": r["value"], "unit": r["unit"]})
            for r in con.execute("SELECT * FROM note ORDER BY date"):
                self.records[r["person_id"]].notes.append(
                    Note(r["note_id"], r["person_id"], _d(r["date"]), r["type"], r["author"], r["text"]))
            self.lab_codes = [dict(r) for r in con.execute("SELECT * FROM lab_code ORDER BY source_code")]
        finally:
            con.close()

    def get(self, person_id: str) -> PatientRecord:
        return self.records[person_id]

    def note(self, note_id: str) -> Note | None:
        pid = note_id.split("-")[0]
        rec = self.records.get(pid)
        return next((n for n in rec.notes if n.note_id == note_id), None) if rec else None

    def unmapped_codes(self) -> list[dict]:
        return [c for c in self.lab_codes if not c["loinc"] and c["row_count"]]
