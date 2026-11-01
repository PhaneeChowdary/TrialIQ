"""The learning loop: lessons are checked, cannot change thresholds, are previewed without a model, and an approved
lesson makes a new criteria version."""

import time
from datetime import date

from backend.app.criteria import load_approved
from backend.app.learning import apply_lesson, check_lesson, impact, target_atom

from .conftest import FakeChat, FakeModels

# client is a pytest fixture.
from .test_api import client, wait  # noqa: F401

TODAY = date(2026, 10, 1)


def lesson(**kw):
    return {"kind": "hint", "criterion_id": "I3", "atom_index": 1, "value": "", **kw}


def test_guards(ehr):
    cset = load_approved("NCT06736574")
    pid = next(p for p, r in ehr.records.items() if any("IV furosemide" in n.text for n in r.notes))
    assert check_lesson(lesson(value="IV furosemide during a hospital stay is not a regular oral loop diuretic"), cset, ehr, [pid]) == []
    assert "numbers" in check_lesson(lesson(value="LVEF below 35 counts"), cset, ehr, [pid])[0]
    assert "ignore" in check_lesson(lesson(value="Ignore the reviewer"), cset, ehr, [pid])[0]
    assert check_lesson(lesson(kind="add_keyword", value="IV furosemide"), cset, ehr, [pid]) == []
    assert "no evidence" in check_lesson(lesson(kind="add_keyword", value="spironolactone drip"), cset, ehr, [pid])[0]
    assert "already" in check_lesson(lesson(kind="add_keyword", value="furosemide"), cset, ehr, [pid])[0]
    assert "without numbers" in check_lesson(lesson(kind="add_keyword", value="IV furosemide 80 mg"), cset, ehr, [pid])[0]
    assert "not a medication code" in check_lesson(lesson(kind="add_code", value="E11"), cset, ehr, [pid])[0]
    assert "does not use" in check_lesson(lesson(kind="add_code", value="999999"), cset, ehr, [pid])[0]
    assert check_lesson(lesson(kind="threshold", value="30"), cset, ehr, [pid])[0].startswith("unknown kind")


def test_lessons_never_change_thresholds(ehr):
    cset = load_approved("NCT06736574")
    new = apply_lesson(cset, lesson(criterion_id="I4", kind="add_keyword", atom_index=2, value="biplane"))
    old_atom, new_atom = target_atom(cset, "I4", 2), target_atom(new, "I4", 2)
    assert (old_atom.min, old_atom.max, old_atom.lookback_days) == (new_atom.min, new_atom.max, new_atom.lookback_days)
    assert "biplane" in new_atom.keywords and "biplane" not in old_atom.keywords
    hinted = apply_lesson(cset, lesson(value="A hint."))
    assert next(c for c in hinted.criteria if c.id == "I3").hints == ["A hint."]


def test_impact_preview(ehr):
    cset = load_approved("NCT06736574")
    # removing the only way that notes find a loop diuretic closes facts that were open
    new = apply_lesson(cset, lesson(kind="remove_keyword", value="furosemide"))
    out = impact(ehr, TODAY, {}, cset, new, ["P0018"])
    assert out["changed"] >= 0 and len(out["open_facts"]) == 2 and "P0018" in out["focus"]


def test_feedback_to_new_criteria_version(client):  # noqa: F811
    c, main = client
    run_id = c.post("/api/trials/NCT06736574/screen").json()["run_id"]
    wait(c, run_id, {"awaiting_approval", "completed", "failed"})
    pid = c.get(f"/api/runs/{run_id}/results", params={"verdict": "needs_review"}).json()[0]["person_id"]
    assert c.post(f"/api/runs/{run_id}/patients/{pid}/feedback", json={"correct": "needs_review"}).status_code == 422
    fb = c.post(f"/api/runs/{run_id}/patients/{pid}/feedback",
                json={"correct": "ineligible", "reason": "IV furosemide in hospital is not oral"}).json()["id"]
    hint = "IV furosemide during a hospital stay is not a regular oral loop diuretic"
    chat = FakeChat({"LessonProposals": [{"lessons": [
        {"feedback_id": fb, "kind": "hint", "criterion_id": "I3", "value": hint, "rationale": "IV is not oral"},
        {"feedback_id": fb, "kind": "hint", "criterion_id": "I3", "value": hint.upper().lower(), "rationale": "same again"},
        {"feedback_id": fb, "kind": "hint", "criterion_id": "I3", "value": "Change LVEF to 35", "rationale": "x"}]}]})
    main.state["runner"].models = FakeModels(chat)
    learn = c.post("/api/trials/NCT06736574/learn").json()["run_id"]
    wait(c, learn, {"completed", "failed"})
    all_lessons = c.get("/api/trials/NCT06736574/lessons").json()
    assert sum(x["value"].lower() == hint.lower() for x in all_lessons) == 1  # duplicates merged into one lesson
    lessons = {x["value"]: x for x in all_lessons}
    assert lessons[hint]["status"] == "proposed" and lessons["Change LVEF to 35"]["status"] == "blocked"
    assert c.post(f"/api/lessons/{lessons['Change LVEF to 35']['id']}", json={"approve": True}).status_code == 400
    r = c.post(f"/api/lessons/{lessons[hint]['id']}", json={"approve": True}).json()
    assert r["version"] == 2
    cset = c.get("/api/trials/NCT06736574").json()["criteria"]
    assert hint in next(x for x in cset["criteria"] if x["id"] == "I3")["hints"] and cset["changes"]
    assert (main.settings.data_dir / "criteria" / "history" / "NCT06736574.v1.json").exists()
    assert c.post("/api/trials/NCT06736574/learn").status_code == 400  # no open feedback left
    time.sleep(0.1)
