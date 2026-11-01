"""Gold answers: what an expert chart reviewer concludes for each trial criterion, from the hidden truth.

Each criterion gets met, not_met or unknown. The patient label is:. No data to decide.
- ineligible: an inclusion criterion is not met, or an exclusion criterion is met
- eligible: all inclusion criteria are met and no exclusion criterion is met
- unknown: else. For example no HbA1c in the window.

The criterion ids are the ids of the approved criteria in backend/app/trials/library/<NCT>.criteria.json."""

from datetime import date, timedelta

from .generate import SCREEN, Patient, stay_days

GLUCOSE_LOWERING_OTHER = {"insulin_glargine", "insulin_lispro", "semaglutide", "tirzepatide", "glipizide",
                          "sitagliptin", "pioglitazone"}
LOOP = {"furosemide", "torsemide", "bumetanide"}


def ago(days: int) -> date:
    return SCREEN - timedelta(days=days)


def latest(p: Patient, analyte: str, within: int) -> float | None:
    values = [(d, v) for d, v in p.labs.get(analyte, []) if d >= ago(within)]
    return max(values)[1] if values else None


def current(p: Patient, keys: set[str], min_days: int = 0) -> bool:
    return any(m.key in keys and m.end is None and m.start <= ago(min_days) for m in p.meds)


def exposed(p: Patient, keys: set[str], within: int, longer_than: int = 0) -> bool:
    for m in p.meds:
        if m.key not in keys or (m.end is not None and m.end < ago(within)):
            continue
        if ((m.end or SCREEN) - m.start).days > longer_than:
            return True
    return False


def onset_within(p: Patient, keys: set[str], days: int) -> bool:
    return any(k in p.conds and p.conds[k].onset >= ago(days) for k in keys)


def hf_stay_within(p: Patient, days: int) -> bool:
    """An HF hospitalization counts when the stay reaches into the window. Discharge date in the window."""
    admit = p.facts.get("hf_admit")
    return admit is not None and admit + timedelta(days=stay_days(p.pid)) >= ago(days)


def yn(value: bool | None) -> str:
    return "unknown" if value is None else "met" if value else "not_met"


def t2dm_trial(p: Patient) -> dict[str, str]:
    a1c = latest(p, "hba1c", 180)
    bmi = latest(p, "bmi", 365)
    return {
        "I1": yn(18 <= p.age <= 75),
        "I2": yn(p.active("t2dm") and p.conds["t2dm"].onset <= ago(180)),
        "I3": yn(current(p, {"metformin"}, 90)),
        "I4": yn(None if a1c is None else 7.0 <= a1c <= 10.5),
        "I5": yn(None if bmi is None else 25.0 <= bmi <= 50.0),
        "E1": yn(exposed(p, GLUCOSE_LOWERING_OTHER, 90, longer_than=14)),
        "E2": yn(p.active("pdr_dme")),
        "E3": yn(p.facts.get("hypo_unaware") is True),
    }


def ckd_trial(p: Patient) -> dict[str, str]:
    egfr, uacr = latest(p, "egfr", 90), latest(p, "uacr", 90)
    if egfr is None:
        kidney = None
    elif 30 <= egfr <= 59:
        kidney = None if uacr is None else 30 <= uacr <= 5000
    elif 60 <= egfr <= 75:
        kidney = None if uacr is None else 500 <= uacr <= 5000
    else:
        kidney = False  # out of both eGFR ranges: the UACR does not matter
    sbp28 = latest(p, "sbp", 28)
    k = latest(p, "potassium", 90)
    if k is None or egfr is None:
        potassium = None
    else:
        potassium = 3.0 <= k <= (4.8 if egfr >= 45 else 4.5)
    sbp, dbp = latest(p, "sbp", 90), latest(p, "dbp", 90)
    acei_now, arb_now = current(p, {"lisinopril"}), current(p, {"losartan", "valsartan"})
    stable = current(p, {"lisinopril"}, 28) or current(p, {"losartan", "valsartan"}, 28)
    a1c = latest(p, "hba1c", 90)
    na = latest(p, "sodium", 28)
    return {
        "I1": yn(p.age >= 18),
        "I2": yn(kidney),
        "I3": yn(None if sbp28 is None else p.active("htn") and sbp28 >= 130),
        "I4": yn(acei_now != arb_now and stable),
        "I5": yn(potassium),
        "E1": yn((sbp or 0) > 180 or (dbp or 0) > 110),
        "E2": yn(any(v >= 5.5 for d, v in p.labs.get("potassium", []) if d >= ago(90))),
        "E3": yn(na is not None and na < 135),
        "E4": yn(p.active("t1dm")),
        "E5": yn(a1c is not None and a1c > 10.5),
        "E6": yn(p.facts.get("nyha") == 4),
        "E7": yn(onset_within(p, {"stroke", "tia", "nstemi"}, 90) or hf_stay_within(p, 90)),
        "E8": yn(p.active("adrenal_insufficiency")),
        "E9": yn(p.active("dialysis")),
        "E10": yn(onset_within(p, {"aki"}, 90)),
        "E11": yn("kidney_transplant" in p.conds or "heart_transplant" in p.conds),
    }


def hf_trial(p: Patient) -> dict[str, str]:
    af = p.active("afib")
    echo = p.facts.get("echo")
    lvef = echo["lvef"] if echo and echo["date"] >= ago(180) else None
    bnp = latest(p, "ntprobnp", 180)
    sbp = latest(p, "sbp", 90)
    egfr = latest(p, "egfr", 90)
    escalation = p.facts.get("diuretic_escalation")
    return {
        "I1": yn(18 <= p.age <= 85),
        "I2": yn(p.active("hfref") and p.conds["hfref"].onset <= ago(90)),
        "I3": yn(current(p, LOOP)),
        "I4": yn(None if lvef is None else lvef < (25 if af else 30)),
        "I5": yn(None if bnp is None else bnp >= (3000 if af else 1000)),
        "I6": yn(hf_stay_within(p, 365) or (escalation is not None and escalation >= ago(365))),
        "I7": yn(None if sbp is None else sbp <= 140),
        "E1": yn(af and current(p, {"digoxin"})),
        "E2": yn(onset_within(p, {"nstemi"}, 90)),
        "E3": yn(p.facts.get("residence") in ("hospice", "long-term care")),
        "E4": yn((egfr is not None and egfr < 20) or p.active("dialysis")),
        "E5": yn("heart_transplant" in p.conds or "kidney_transplant" in p.conds),
        "E6": yn(p.active("pregnancy")),
        "E7": yn(p.active("attr_amyloid") or (echo is not None and echo["valve"] == "severe_as")),
    }


TRIALS = {"NCT07668388": t2dm_trial, "NCT06742723": ckd_trial, "NCT06736574": hf_trial}


def label(criteria: dict[str, str]) -> str:
    inc = {k: v for k, v in criteria.items() if k.startswith("I")}
    exc = {k: v for k, v in criteria.items() if k.startswith("E")}
    if "not_met" in inc.values() or "met" in exc.values():
        return "ineligible"
    if all(v == "met" for v in inc.values()) and all(v == "not_met" for v in exc.values()):
        return "eligible"
    return "unknown"


def gold_labels(patients: list[Patient]) -> dict:
    out = {"screen_date": SCREEN.isoformat(), "trials": {}, "patients": {}}
    for nct, rule in TRIALS.items():
        rows = {}
        for p in patients:
            criteria = rule(p)
            rows[p.pid] = {"label": label(criteria), "criteria": criteria}
        out["trials"][nct] = rows
    for p in patients:
        out["patients"][p.pid] = {"archetype": p.archetype, "clinic": p.clinic, "race": p.race, "ethnicity": p.ethnicity,
                                  "sex": p.sex, "age": p.age, "traps": sorted(p.traps), "note_only": sorted(p.note_only)}
    return out
