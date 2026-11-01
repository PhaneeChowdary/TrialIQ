"""The synthetic EHR: an OMOP-style SQLite database. The app opens it read-only."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE person (
  person_id TEXT PRIMARY KEY, name TEXT, birth_date TEXT, sex TEXT, race TEXT, ethnicity TEXT,
  clinic TEXT, pcp TEXT
);
CREATE TABLE condition_occurrence (
  id INTEGER PRIMARY KEY, person_id TEXT, vocabulary TEXT, code TEXT, name TEXT, onset_date TEXT, end_date TEXT
);
CREATE TABLE drug_exposure (
  id INTEGER PRIMARY KEY, person_id TEXT, rxcui TEXT, name TEXT, dose TEXT, start_date TEXT, end_date TEXT
);
CREATE TABLE measurement (
  id INTEGER PRIMARY KEY, person_id TEXT, date TEXT, source_code TEXT, source_name TEXT, loinc TEXT,
  value REAL, unit TEXT
);
CREATE TABLE lab_code (
  source_code TEXT PRIMARY KEY, source_name TEXT, unit TEXT, loinc TEXT, clinic TEXT, row_count INTEGER
);
CREATE TABLE note (
  note_id TEXT PRIMARY KEY, person_id TEXT, date TEXT, type TEXT, author TEXT, text TEXT
);
CREATE INDEX ix_cond ON condition_occurrence(person_id);
CREATE INDEX ix_drug ON drug_exposure(person_id);
CREATE INDEX ix_meas ON measurement(person_id);
CREATE INDEX ix_note ON note(person_id);
"""


def create(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def connect(path: Path) -> sqlite3.Connection:
    """A read-only connection. Each thread opens its own."""
    con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con
