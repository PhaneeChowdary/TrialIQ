from datetime import date

from backend.app.agent.guards import (
    check_answer,
    parse_date,
    quote_in_text,
    quoted_date,
    unverified_codes,
    value_from_quote,
    value_in_quote,
)
from backend.app.agent.protocol import method_conflict
from backend.app.agent.schemas import Answer
from backend.app.criteria import Atom
from backend.app.ehr.store import Note

TODAY = date(2026, 10, 1)
NOTES = {
    "P0001-N1": Note("P0001-N1", "P0001", date(2026, 9, 20), "Progress note", "Dr. A",
                     "LABS: HbA1c 64 mmol/mol (POC, 09/12/2026); eGFR 41.\nFAMILY HISTORY: Mother with type 1 diabetes."),
    "P0001-N2": Note("P0001-N2", "P0001", date(2026, 7, 26), "Discharge summary", "Hospitalist",
                     "Steroid-related hyperglycemia was managed with insulin lispro sliding scale for 5 days "
                     "(07/21/2026 to 07/26/2026); insulin stopped before discharge."),
}
A1C = Atom(type="lab", concept="HbA1c", codes=["4548-4"], unit="%", min=7.0, max=10.5, lookback_days=180)
INSULIN = Atom(type="medication", concept="insulin", codes=["86009"], lookback_days=90, ignore_courses_up_to_days=14)


def answer(**kw) -> Answer:
    return Answer(**{"question_id": "Q1", "finding": "present", **kw})


def test_quote_matching():
    assert quote_in_text("HbA1c 64 mmol/mol", NOTES["P0001-N1"].text)
    assert quote_in_text('"hba1c 64 MMOL/MOL ... eGFR 41"', NOTES["P0001-N1"].text)
    assert not quote_in_text("HbA1c 8.1%", NOTES["P0001-N1"].text)
    assert value_in_quote(64, "HbA1c 64 mmol/mol") and not value_in_quote(6.4, "HbA1c 64 mmol/mol")
    assert parse_date("09/12/2026") == date(2026, 9, 12) and parse_date("2019") == date(2019, 1, 1)


def test_lab_value_is_converted_by_code():
    ok = answer(note_id="P0001-N1", quote="HbA1c 64 mmol/mol", value=64, unit="mmol/mol", date="2026-09-12")
    assert check_answer(ok, A1C, NOTES, "P0001", TODAY) == ("met", [])  # 64 mmol/mol = 8.0 %


def test_invented_evidence_is_caught():
    wrong_note = answer(note_id="P0002-N1", quote="HbA1c 64 mmol/mol", value=64, unit="mmol/mol")
    assert check_answer(wrong_note, A1C, NOTES, "P0001", TODAY)[0] == "unknown"
    misquote = answer(note_id="P0001-N1", quote="HbA1c 8.0%", value=8.0, unit="%")
    assert "not in note" in check_answer(misquote, A1C, NOTES, "P0001", TODAY)[1][0]
    wrong_value = answer(note_id="P0001-N1", quote="HbA1c 64 mmol/mol", value=8.0, unit="%")
    assert "not in the quote" in check_answer(wrong_value, A1C, NOTES, "P0001", TODAY)[1][0]
    old = A1C.model_copy(update={"lookback_days": 5})
    stale = answer(note_id="P0001-N1", quote="HbA1c 64 mmol/mol", value=64, unit="mmol/mol", date="2026-09-12")
    assert check_answer(stale, old, NOTES, "P0001", TODAY)[0] == "unknown"


def test_short_course_is_allowed_from_the_quote():
    a = answer(note_id="P0001-N2", quote="insulin lispro sliding scale for 5 days (07/21/2026 to 07/26/2026)")
    status, problems = check_answer(a, INSULIN, NOTES, "P0001", TODAY)
    assert status == "not_met" and "5 days" in problems[0]


def test_absent_and_unclear():
    a = answer(finding="absent", note_id="P0001-N1", quote="Mother with type 1 diabetes")
    t1 = Atom(type="condition", concept="type 1 diabetes", codes=["E10"])
    assert check_answer(a, t1, NOTES, "P0001", TODAY)[0] == "not_met"
    assert check_answer(answer(finding="unclear"), t1, NOTES, "P0001", TODAY)[0] == "unknown"


def test_codes_and_methods():
    assert unverified_codes(["E11", "E99.9"], "E11.9 | Type 2 diabetes") == ["E99.9"]
    assert method_conflict("GFR EST CKDEPI21", "Glomerular filtration rate ... formula (MDRD)/1.73 sq M")
    assert not method_conflict("GFR EST CKDEPI21", "Glomerular filtration rate ... formula (CKD-EPI 2021)/1.73 sq M")


def test_stopped_before_the_window():
    notes = {"P0001-N3": Note("P0001-N3", "P0001", date(2026, 9, 20), "Progress note", "Dr. A",
                              "- Previously on insulin glargine 18 units nightly, stopped 01/21/2026")}
    a = answer(note_id="P0001-N3", quote="Previously on insulin glargine 18 units nightly, stopped 01/21/2026", date="2023-01-09")
    status, problems = check_answer(a, INSULIN, notes, "P0001", TODAY)
    assert status == "not_met" and "before the window" in problems[0]


def test_value_from_quote():
    assert value_from_quote("LVEF by biplane Simpson's method: 27%.") == 27
    assert value_from_quote("HbA1c 64 mmol/mol (POC, 09/12/2026)") == 64
    assert value_from_quote("eGFR 41; K 4.2") is None


def test_quoted_date_wins_over_the_model_date():
    notes = {"P0001-N4": Note("P0001-N4", "P0001", date(2026, 9, 28), "Progress note", "Dr. A", "- Type 2 diabetes mellitus, since 2014")}
    t2 = Atom(type="condition", concept="type 2 diabetes", codes=["E11"], min_duration_days=180)
    a = answer(note_id="P0001-N4", quote="Type 2 diabetes mellitus, since 2014", date="2026-09-28")
    assert quoted_date(a.quote) == date(2014, 1, 1)
    assert check_answer(a, t2, notes, "P0001", TODAY)[0] == "met"


def test_units_come_from_the_quote():
    a = answer(note_id="P0001-N1", quote="HbA1c 64 mmol/mol", value=64, unit="%", date="2026-09-12")
    assert check_answer(a, A1C, NOTES, "P0001", TODAY)[0] == "met"  # the quote says mmol/mol: 8.0 %, not 64 %
    no_unit = answer(note_id="P0001-N1", quote="HbA1c 64 mmol/mol", value=64, unit=None, date="2026-09-12")
    assert check_answer(no_unit, A1C, NOTES, "P0001", TODAY)[0] == "met"
    egfr = Atom(type="lab", concept="eGFR", codes=["98979-8"], unit="mL/min/1.73m2", min=30, max=59, lookback_days=90)
    assert check_answer(answer(note_id="P0001-N1", quote="eGFR 41", value=41), egfr, NOTES, "P0001", TODAY)[0] == "met"
    bare = {"P0001-N5": Note("P0001-N5", "P0001", date(2026, 9, 20), "Progress note", "Dr. A", "A1c was 64 at the last visit.")}
    status, problems = check_answer(answer(note_id="P0001-N5", quote="A1c was 64 at the last visit", value=64), A1C, bare, "P0001", TODAY)
    assert status == "unknown" and "more than one unit" in problems[0]


def test_values_and_dates_need_evidence():
    assert not value_in_quote(12, "HbA1c 8.1% (09/12/2026)")  # 12 is part of a date
    stroke = Atom(type="condition", concept="stroke", codes=["I63"], lookback_days=90)
    notes = {"P0001-N6": Note("P0001-N6", "P0001", date(2026, 9, 20), "Progress note", "Dr. A", "- History of stroke.")}
    undated = answer(note_id="P0001-N6", quote="History of stroke.", date="2026-09-01")  # the date is not in the note
    status, problems = check_answer(undated, stroke, notes, "P0001", TODAY)
    assert status == "unknown" and "no date" in problems[0]
    assert not quote_in_text("AF", "Atrial fibrillation (AF) on apixaban")  # too short to prove anything


def test_discharge_summary_dates_its_stay():
    hf = Atom(type="note", concept="heart failure hospitalization", lookback_days=365)
    notes = {"P0001-N7": Note("P0001-N7", "P0001", date(2026, 9, 13), "Discharge summary", "Hospitalist",
                              "Admission: 09/08/2026    Discharge: 09/13/2026\nHOSPITAL COURSE: Admitted with volume overload.")}
    a = answer(note_id="P0001-N7", quote="Admitted with volume overload.")
    assert check_answer(a, hf, notes, "P0001", TODAY)[0] == "met"
    old = {"P0001-N8": Note("P0001-N8", "P0001", date(2024, 12, 14), "Discharge summary", "Hospitalist",
                            "Admission: 12/10/2024    Discharge: 12/14/2024\nHOSPITAL COURSE: Admitted with volume overload.")}
    assert check_answer(answer(note_id="P0001-N8", quote="Admitted with volume overload."), hf, old, "P0001", TODAY)[0] == "not_met"
