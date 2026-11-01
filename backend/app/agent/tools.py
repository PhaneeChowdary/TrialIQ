"""Tools of the agents. Chart tools see one patient only. Terminology tools call free NLM APIs, with a cache."""

import json
import re
import threading
from pathlib import Path

import httpx
from langchain_core.tools import tool

from ..ehr.store import EHR, PatientRecord
from ..engine import _pattern

MAX_SENTENCES = 8


def chart_tools(rec: PatientRecord) -> list:
    """Read-only tools over one patient's chart."""
    notes = {n.note_id: n for n in rec.notes}

    @tool
    def search_notes(query: str) -> str:
        """Search this patient's notes. Give one to three words, e.g. 'hospice' or 'ejection fraction'.
        Returns matching sentences with the note id and date."""
        words = [w for w in re.split(r"\s+", query.strip()) if w]
        if not words:
            return "Give a word to search."
        patterns = [_pattern(w) for w in words]
        out = []
        for n in sorted(rec.notes, key=lambda n: n.date, reverse=True):
            for s in n.sentences():
                if all(p.search(s) for p in patterns):
                    out.append(f"[{n.note_id} | {n.date} | {n.type}] {s}")
        if not out:
            return f"No sentence has all of: {', '.join(words)}."
        return "\n".join(out[:MAX_SENTENCES]) + (f"\n({len(out) - MAX_SENTENCES} more)" if len(out) > MAX_SENTENCES else "")

    @tool
    def read_note(note_id: str) -> str:
        """Read one whole note of this patient by its id, e.g. P0012-N3."""
        n = notes.get(note_id.strip())
        if n is None:
            return f"No note {note_id} for this patient. Notes: {', '.join(notes)}"
        return f"[{n.note_id} | {n.date} | {n.type} | {n.author}]\n{n.text}"

    @tool
    def list_lab_results(name: str) -> str:
        """List this patient's lab results whose name contains the text, e.g. 'A1C', 'EGFR', 'ALB'. Shows local
        codes that have no LOINC code too."""
        key = name.strip().lower()
        rows = [lab for lab in rec.labs if key in lab["source_name"].lower() or key in lab["source_code"].lower()]
        if not rows:
            return f"No lab result matches '{name}'."
        return "\n".join(f"{lab['date']} {lab['source_code']} '{lab['source_name']}' = {lab['value']:g} {lab['unit']} "
                         f"(LOINC {lab['loinc'] or 'none: unmapped local code'})" for lab in rows[-10:])

    @tool
    def list_medications() -> str:
        """List this patient's medicine list with start and end dates. The list can be out of date: check the notes."""
        return "\n".join(f"{d['name']} {d['dose']}: {d['start']} to {d['end'] or 'now (no end date)'}" for d in rec.drugs) or "Empty."

    return [search_notes, read_note, list_lab_results, list_medications]


# --- Terminology ------------------------------------------------------------------------------------------

class TerminologyCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self.data = {}

    def get(self, key: str, fetch) -> str:
        with self.lock:
            if key in self.data:
                return self.data[key]
        try:
            value = fetch()
        except httpx.HTTPError as exc:
            return f"The terminology service is not reachable ({type(exc).__name__}). Use search_ehr_concepts."
        with self.lock:
            self.data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=0))
        return value


def nlm_search(table: str, text: str, fields: str, limit: int = 8) -> list[list[str]]:
    r = httpx.get(f"https://clinicaltables.nlm.nih.gov/api/{table}/v3/search",
                  params={"terms": text, "df": fields, "maxList": limit}, timeout=20)
    r.raise_for_status()
    return r.json()[3] or []


def protocol_tools(ehr: EHR, cache: TerminologyCache) -> list:
    conditions: dict[str, tuple[str, int]] = {}
    drugs: dict[str, tuple[str, int]] = {}
    for rec in ehr.records.values():
        for c in rec.conditions:
            name, n = conditions.get(c["code"], (c["name"], 0))
            conditions[c["code"]] = (name, n + 1)
        for d in rec.drugs:
            name, n = drugs.get(d["rxcui"], (d["name"], 0))
            drugs[d["rxcui"]] = (name, n + 1)

    @tool
    def search_ehr_concepts(text: str, domain: str) -> str:
        """Search the codes that this EHR uses. domain: condition, medication or lab (local. ICD-10-CM. RxNorm.
        lab codes with their LOINC code). Returns code, name and the number of records."""
        words = [w.lower() for w in re.findall(r"\w+", text)]
        if domain.startswith("cond"):
            items = [(code, name, n) for code, (name, n) in conditions.items()]
        elif domain.startswith("med") or domain.startswith("drug"):
            items = [(code, name, n) for code, (name, n) in drugs.items()]
        elif domain.startswith("lab"):
            items = [(c["source_code"], f"{c['source_name']} [{c['unit']}] LOINC {c['loinc'] or 'unmapped'}", c["row_count"])
                     for c in ehr.lab_codes]
        else:
            return "domain must be condition, medication or lab"
        found = [i for i in items if any(w in i[1].lower() or w in i[0].lower() for w in words)]
        found.sort(key=lambda i: -i[2])
        return "\n".join(f"{code} | {name} | {n} records" for code, name, n in found[:15]) or "No match in this EHR."

    @tool
    def search_loinc(text: str) -> str:
        """Search LOINC. Returns LOINC code, long name and example units. NLM Clinical Tables."""
        def fetch():
            rows = nlm_search("loinc_items", text, "LOINC_NUM,LONG_COMMON_NAME,EXAMPLE_UCUM_UNITS")
            return "\n".join(f"{r[0]} | {r[1]} | {r[2] or ''}" for r in rows) or "No LOINC match."
        return cache.get(f"loinc:{text.lower()}", fetch)

    @tool
    def search_icd10(text: str) -> str:
        """Search ICD-10-CM. Returns code and name. NLM Clinical Tables."""
        def fetch():
            rows = nlm_search("icd10cm", text, "code,name", 12)
            return "\n".join(f"{r[0]} | {r[1]}" for r in rows) or "No ICD-10-CM match."
        return cache.get(f"icd10:{text.lower()}", fetch)

    @tool
    def search_rxnorm(text: str) -> str:
        """Find the RxNorm ingredient RXCUI of a medicine name. IN. NLM RxNav."""
        def fetch():
            r = httpx.get("https://rxnav.nlm.nih.gov/REST/rxcui.json", params={"name": text, "search": 2}, timeout=20)
            r.raise_for_status()
            ids = r.json().get("idGroup", {}).get("rxnormId", [])
            if not ids:
                return "No RxNorm match. Give one ingredient name, e.g. 'metformin'."
            out = []
            for rxcui in ids[:3]:
                p = httpx.get(f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json", timeout=20).json()
                props = p.get("properties", {})
                out.append(f"{rxcui} | {props.get('name')} | {props.get('tty')}")
            return "\n".join(out)
        return cache.get(f"rxnorm:{text.lower()}", fetch)

    return [search_ehr_concepts, search_loinc, search_icd10, search_rxnorm]
