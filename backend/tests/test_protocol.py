import json

from backend.app.agent.protocol import compare_to_approved, split_items
from backend.app.criteria import LIBRARY, load_approved
from backend.app.ehr.generate import build
from backend.app.ehr.gold import gold_labels


def test_split_items_keeps_sections():
    text = json.loads((LIBRARY / "NCT07668388.json").read_text())["eligibility"]
    items = split_items(text)
    kinds = [k for k, _ in items]
    assert kinds[0] == "inclusion" and "exclusion" in kinds
    assert any("HbA1c" in t for k, t in items if k == "inclusion")
    assert any("hypoglycaemic unawareness" in t for k, t in items if k == "exclusion")


def test_compare_approved_to_itself():
    a = load_approved("NCT06736574")
    out = compare_to_approved(a, a)
    assert out["recall"] == 1.0 and out["thresholds_ok"] == 1.0 and out["code_recall"] == 1.0


def test_generator_is_deterministic():
    one, two = build(), build()
    assert [p.pid for p in one] == [p.pid for p in two]
    assert gold_labels(one)["trials"] == gold_labels(two)["trials"]


def test_referral_guard():
    from backend.app.agent.screening import referral_problem

    ids = ["P0001", "P0002"]
    assert referral_problem("Dear Dr. A, your patient Ann Lee (MRN P0001) may be eligible.", "P0001", "Ann Lee", ids) == ""
    assert "another patient" in referral_problem("Ann Lee (P0001) and P0002 may be eligible.", "P0001", "Ann Lee", ids)
    assert "does not name" in referral_problem("Your patient may be eligible.", "P0001", "Ann Lee", ids)
    assert "promises" in referral_problem("Ann Lee is eligible for the trial.", "P0001", "Ann Lee", ids)
    assert "care decision" in referral_problem("Ann Lee (P0001), no code status, may be eligible.", "P0001", "Ann Lee", ids)


def test_engine_handles_negated_groups_and_rules_without_atoms(ehr):
    from datetime import date

    from backend.app.criteria import CriteriaSet
    from backend.app.engine import Engine

    cset = CriteriaSet.model_validate({"nct_id": "X", "criteria": [
        {"id": "I1", "kind": "inclusion", "text": "no rule", "atoms": []},
        {"id": "E1", "kind": "exclusion", "text": "not (adult)", "atoms": [
            {"type": "group", "concept": "g", "negate": True, "all_of": [{"type": "age", "concept": "age", "min": 18}]}]}]})
    s = Engine(ehr, date(2026, 10, 1)).screen(next(iter(ehr.records.values())), cset)
    by = {c.id: c for c in s.criteria}
    assert by["I1"].status == "unknown"  # never counted as passed
    assert by["E1"].status == "not_met"  # adults: "not adult" is false


def test_referral_facts_hide_engine_wording(ehr):
    from datetime import date

    from backend.app.agent.screening import referral_facts
    from backend.app.engine import Engine

    cset = load_approved("NCT06736574")
    for s in Engine(ehr, date(2026, 10, 1)).screen_all(cset):
        text = " ".join(referral_facts(s))
        assert "no code" not in text and "no mention" not in text
