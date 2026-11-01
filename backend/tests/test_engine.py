from collections import Counter
from datetime import date

import pytest

from backend.app.criteria import Atom, load_approved
from backend.app.engine import Engine, atom_specs, funnel, kleene_all, kleene_any, note_hits
from backend.app.units import UnitError, convert

TRIALS = ["NCT07668388", "NCT06742723", "NCT06736574"]
MAPS = {"ECH-POCA1C": "59261-8", "ECH-EGFR21": "98979-8", "ECH-MALBCR": "9318-7"}
TODAY = date(2026, 10, 1)


def test_units():
    assert convert(53, "mmol/mol", "%") == pytest.approx(7.0, abs=0.01)
    assert convert(7.0, "%", "mmol/mol") == pytest.approx(53.0, abs=0.1)
    assert convert(3.39, "mg/mmol", "mg/g") == pytest.approx(30, abs=0.1)
    assert convert(140, "mmHg", "mm[Hg]") == 140
    with pytest.raises(UnitError):
        convert(5, "mg/dL", "%")


def test_kleene():
    assert kleene_all(["met", "unknown"]) == "unknown"
    assert kleene_all(["met", "not_met", "unknown"]) == "not_met"
    assert kleene_any(["not_met", "unknown"]) == "unknown"
    assert kleene_any(["unknown", "met"]) == "met"


@pytest.mark.parametrize("nct", TRIALS)
@pytest.mark.parametrize("maps", [{}, MAPS])
def test_engine_never_contradicts_gold(ehr, gold, nct, maps):
    """Each criterion that the rules decide has the gold answer; an eligible patient is never excluded."""
    screens = Engine(ehr, TODAY, maps).screen_all(load_approved(nct))
    g = gold["trials"][nct]
    wrong = Counter()
    for s in screens:
        for c in s.criteria:
            if c.status != "unknown" and c.status != g[s.person_id]["criteria"][c.id]:
                wrong[c.id] += 1
        if s.stage == "excluded":
            assert g[s.person_id]["label"] == "ineligible", s.person_id
    assert not wrong


def test_mappings_shrink_chart_review(ehr):
    cset = load_approved("NCT06742723")
    before = sum(len(s.open_atoms()) for s in Engine(ehr, TODAY, {}).screen_all(cset))
    after = sum(len(s.open_atoms()) for s in Engine(ehr, TODAY, MAPS).screen_all(cset))
    assert after < before / 2


def test_funnel_adds_up(ehr):
    f = funnel(Engine(ehr, TODAY, MAPS).screen_all(load_approved("NCT06736574")))
    assert f["excluded"] + f["eligible"] + f["open"] + f["missing"] == f["pool"] == 240


def test_keyword_boundaries(ehr):
    rec = next(r for r in ehr.records.values() if any("nonproliferative" in n.text for n in r.notes))
    hits = note_hits(rec.notes, ["proliferative"])
    assert all("nonproliferative" not in h.sentence.lower() or "proliferative diabetic retinopathy with" in h.sentence.lower()
               for h in hits)


def test_atom_specs_keys_match_results(ehr):
    cset = load_approved("NCT06742723")
    specs = atom_specs(cset)
    screen = Engine(ehr, TODAY, MAPS).screen(next(iter(ehr.records.values())), cset)
    keys = []

    def walk(a):
        keys.append(a.key)
        for k in a.children:
            walk(k)
    for c in screen.criteria:
        for a in c.atoms:
            walk(a)
    assert set(keys) <= set(specs)
    assert isinstance(specs["I2.1.1"], Atom)
