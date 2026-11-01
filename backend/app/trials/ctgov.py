"""ClinicalTrials.gov API v2 client. Free, no key. https://clinicaltrials.gov/data-api/api"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

API = "https://clinicaltrials.gov/api/v2/studies"
LIBRARY = Path(__file__).resolve().parent / "library"
NCT = re.compile(r"^NCT\d{8}$")


class TrialNotFound(LookupError):
    pass


def _summary(study: dict) -> dict:
    p = study.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    status = p.get("statusModule", {})
    elig = p.get("eligibilityModule", {})
    design = p.get("designModule", {})
    sponsor = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    locations = p.get("contactsLocationsModule", {}).get("locations", [])
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "status": status.get("overallStatus"),
        "phases": design.get("phases", []),
        "conditions": p.get("conditionsModule", {}).get("conditions", []),
        "sponsor": sponsor.get("name"),
        "min_age": elig.get("minimumAge"),
        "max_age": elig.get("maximumAge"),
        "sex": elig.get("sex"),
        "eligibility": elig.get("eligibilityCriteria", ""),
        "indiana_sites": sorted({loc.get("facility", "") for loc in locations if loc.get("state") == "Indiana"}),
        "last_update": status.get("lastUpdatePostDateStruct", {}).get("date"),
        "fetched": datetime.now(UTC).date().isoformat(),
    }


def fetch(nct_id: str, timeout: float = 20) -> dict:
    nct_id = nct_id.strip().upper()
    if not NCT.match(nct_id):
        raise TrialNotFound(f"not an NCT id: {nct_id}")
    response = httpx.get(f"{API}/{nct_id}", timeout=timeout)
    if response.status_code == 404:
        raise TrialNotFound(nct_id)
    response.raise_for_status()
    return _summary(response.json())


def search(condition: str, state: str = "Indiana", recruiting: bool = True, limit: int = 10, timeout: float = 20) -> list[dict]:
    params = {"query.cond": condition, "pageSize": limit,
              "fields": "NCTId,BriefTitle,OverallStatus,Phase,Condition,LeadSponsorName,EligibilityModule"}
    if recruiting:
        params["filter.overallStatus"] = "RECRUITING"
    if state:
        params["filter.advanced"] = f"AREA[LocationState]{state}"
    response = httpx.get(API, params=params, timeout=timeout)
    response.raise_for_status()
    out = []
    for study in response.json().get("studies", []):
        s = _summary(study)
        out.append({k: s[k] for k in ("nct_id", "title", "status", "phases", "conditions", "sponsor", "min_age", "max_age")}
                   | {"criteria_chars": len(s["eligibility"])})
    return out


def snapshot(nct_id: str) -> dict:
    """Fetch a trial and keep it in the library, so the demo and the evaluation work offline."""
    trial = fetch(nct_id)
    LIBRARY.mkdir(exist_ok=True)
    (LIBRARY / f"{trial['nct_id']}.json").write_text(json.dumps(trial, indent=1))
    return trial


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        t = snapshot(arg)
        print(t["nct_id"], t["status"], t["title"][:80], len(t["eligibility"]), "chars")
