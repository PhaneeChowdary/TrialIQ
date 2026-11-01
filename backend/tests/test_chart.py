"""The chart review flow with a scripted model: answers, guards, reviewer rejection, retry and adjudication."""

from dataclasses import asdict
from datetime import date

from backend.app.adjudicate import apply
from backend.app.agent.chart import ChartReviewer, build_questions
from backend.app.criteria import load_approved
from backend.app.engine import Engine, atom_specs

from .conftest import FakeChat, FakeModels

TODAY = date(2026, 10, 1)
MAPS = {"ECH-POCA1C": "59261-8", "ECH-EGFR21": "98979-8", "ECH-MALBCR": "9318-7"}


def stale_metformin_patient(ehr, gold):
    cset = load_approved("NCT07668388")
    for s in Engine(ehr, TODAY, MAPS).screen_all(cset):
        if s.stage == "open" and "stale_med_list" in gold["patients"][s.person_id]["traps"]:
            return cset, s
    raise AssertionError("no stale medicine list patient")


def test_undated_medicine_claim_waits_for_a_person(ehr, gold):
    """'Taking metformin' with no start date in the evidence cannot prove 90 days: the guard holds it."""
    cset, screen = stale_metformin_patient(ehr, gold)
    specs = atom_specs(cset)
    rec = ehr.get(screen.person_id)
    questions = build_questions(screen, specs)
    q_met = next(q for q in questions if q.atom.concept == "metformin")
    note = next(n for n in rec.notes if "Metformin stopped" in n.text)
    quote = next(s for s in note.sentences() if "Metformin stopped" in s)
    chat = FakeChat({"ChartAnswers": [{"answers": [{"question_id": q_met.id, "finding": "present", "note_id": note.note_id,
                                                    "quote": quote}]}], "ReviewVerdicts": [{"verdicts": []}]})
    res = ChartReviewer(FakeModels(chat), TODAY).run(rec, [q_met])
    assert res[0].status == "unknown" and "start date" in res[0].problems[0]


def test_reviewer_rejection_leads_to_a_corrected_answer(ehr, gold):
    """A negated sentence quoted as 'present': the reviewer rejects it, the agent answers again, the code accepts."""
    cset = load_approved("NCT07668388")
    specs = atom_specs(cset)
    for screen in Engine(ehr, TODAY, MAPS).screen_all(cset):
        rec = ehr.get(screen.person_id)
        q = next((q for q in build_questions(screen, specs) if q.atom.concept.startswith("unstable retinopathy")), None)
        note = next((n for n in rec.notes if "No diabetic retinopathy." in n.text), None)
        if q and note and gold["trials"]["NCT07668388"][screen.person_id]["criteria"]["E2"] == "not_met":
            break
    else:
        raise AssertionError("no patient with a negated retinopathy note")
    quote = "No diabetic retinopathy. No macular edema."
    chat = FakeChat({
        "ChartAnswers": [{"answers": [{"question_id": q.id, "finding": "present", "note_id": note.note_id, "quote": quote}]},
                         {"answers": [{"question_id": q.id, "finding": "absent", "note_id": note.note_id, "quote": quote}]}],
        "ReviewVerdicts": [{"verdicts": [{"question_id": q.id, "ok": False, "problem": "negated"}]},
                           {"verdicts": [{"question_id": q.id, "ok": True}]}],
    })
    events = []
    res = ChartReviewer(FakeModels(chat), TODAY, on_event=lambda kind, **k: events.append(kind)).run(rec, [q])
    assert res[0].status == "not_met" and res[0].round == 2 and res[0].reviewer == "approved"
    assert "review" in events
    apply(screen, specs, cset, [asdict(r) for r in res])
    assert next(c for c in screen.criteria if c.id == "E2").status == "not_met"


def test_invented_quote_stays_unknown(ehr, gold):
    cset, screen = stale_metformin_patient(ehr, gold)
    specs = atom_specs(cset)
    rec = ehr.get(screen.person_id)
    questions = build_questions(screen, specs)
    chat = FakeChat({"ChartAnswers": [{"answers": [{"question_id": q.id, "finding": "present", "note_id": rec.notes[0].note_id,
                                                    "quote": "patient takes metformin 1000 mg daily and is doing great"} for q in questions]}],
                     "ReviewVerdicts": [{"verdicts": []}]})
    res = ChartReviewer(FakeModels(chat), TODAY).run(rec, questions)
    assert all(r.status == "unknown" and r.problems for r in res)
