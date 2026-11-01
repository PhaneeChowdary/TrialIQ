"""The API end to end with a scripted model: a screening run pauses for approval, then finishes after the decisions."""

import importlib
import shutil
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from .conftest import DATA, FakeChat, FakeModels


@pytest.fixture()
def client(tmp_path, monkeypatch, ehr):
    shutil.copy(DATA / "ehr.db", tmp_path / "ehr.db")
    monkeypatch.setenv("TRIALIQ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRIALIQ_OLLAMA_URL", "http://127.0.0.1:9")  # no model server in tests
    import backend.app.config as config
    importlib.reload(config)
    import backend.app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        chat = FakeChat({"ChartAnswers": [], "ReviewVerdicts": [],
                         "Referral": [{"subject": "Possible referral", "body": "Dear doctor, ..."}] * 50})
        main.state["runner"].models = FakeModels(chat)
        yield c, main


def wait(c, run_id, statuses, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        r = c.get(f"/api/runs/{run_id}").json()
        if r["status"] in statuses:
            return r
        time.sleep(0.3)
    raise AssertionError(f"run stayed {r['status']}")


def test_screening_run_with_approval(client):
    c, _ = client
    assert {t["nct_id"] for t in c.get("/api/trials").json()} >= {"NCT07668388", "NCT06742723", "NCT06736574"}
    run_id = c.post("/api/trials/NCT07668388/screen", headers={"X-User": "tester"}).json()["run_id"]
    r = wait(c, run_id, {"awaiting_approval", "completed", "failed"})
    assert r["status"] == "awaiting_approval", r.get("error")
    assert r["summary"]["funnel"]["pool"] == 240
    eligible = c.get(f"/api/runs/{run_id}/results", params={"verdict": "eligible"}).json()
    drafts = c.get(f"/api/runs/{run_id}/outreach").json()
    assert eligible and len(drafts) == len(eligible)
    # a partial set, an unknown decision and an unknown patient are refused; the run keeps waiting
    partial = [{"person_id": drafts[0]["person_id"], "decision": "approve"}]
    assert c.post(f"/api/runs/{run_id}/decisions", json=partial).status_code == 400
    bad = [{"person_id": d["person_id"], "decision": "maybe"} for d in drafts]
    assert c.post(f"/api/runs/{run_id}/decisions", json=bad).status_code == 400
    stranger = [{"person_id": d["person_id"], "decision": "reject"} for d in drafts[1:]] + [{"person_id": "P9999", "decision": "approve"}]
    assert c.post(f"/api/runs/{run_id}/decisions", json=stranger).status_code == 400
    assert c.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
    decisions = [{"person_id": drafts[0]["person_id"], "decision": "approve"}] + \
                [{"person_id": d["person_id"], "decision": "reject"} for d in drafts[1:]]
    assert c.post(f"/api/runs/{run_id}/decisions", json=decisions).json()["ok"]
    r = wait(c, run_id, {"completed", "failed"})
    assert r["status"] == "completed", r.get("error")
    statuses = {o["person_id"]: o["status"] for o in c.get(f"/api/runs/{run_id}/outreach").json()}
    assert statuses[drafts[0]["person_id"]] == "approved (dry run)" and statuses[drafts[1]["person_id"]] == "rejected"
    detail = c.get(f"/api/runs/{run_id}/patients/{eligible[0]['person_id']}").json()
    assert detail["verdict"] == "eligible" and detail["notes"]
    actions = {a["action"] for a in c.get("/api/audit").json()}
    assert {"screen.start", "outreach.approve", "outreach.reject"} <= actions


def test_run_uses_its_frozen_criteria_and_mappings(client):
    c, main = client
    run_id = c.post("/api/trials/NCT06742723/screen").json()["run_id"]
    wait(c, run_id, {"awaiting_approval", "completed", "failed"})
    assert c.post("/api/mappings/ECH-EGFR21", json={"approve": True, "loinc": "98979-8"}).json()["ok"]
    r = c.get(f"/api/runs/{run_id}").json()
    assert r["mappings_used"] == {} and r["criteria"]["nct_id"] == "NCT06742723"
    ctx = main.state["runner"]._context(run_id)
    assert ctx.mappings == {}  # a resumed run still decides with what it started with


def test_bad_approvals_are_refused(client):
    c, _ = client
    assert c.post("/api/mappings/ECH-EGFR21", json={"approve": True, "loinc": "not-a-code"}).status_code == 422
    # a mg/mmol urine test cannot map to an eGFR code
    assert c.post("/api/mappings/ECH-MALBCR", json={"approve": True, "loinc": "98979-8"}).status_code == 422
    assert c.post("/api/mappings/NOPE", json={"approve": True, "loinc": "98979-8"}).status_code == 422
    broken = {"nct_id": "NCT07668388", "criteria": [
        {"id": "I1", "kind": "inclusion", "text": "Age 18-75 years", "atoms": []},
        {"id": "I1", "kind": "inclusion", "text": "HbA1c", "atoms": [{"type": "lab", "concept": "HbA1c", "codes": ["4548-4"]}]}]}
    r = c.post("/api/trials/NCT07668388/criteria/approve", json={"criteria": broken})
    assert r.status_code == 422 and "repeat" in r.json()["detail"]


def test_web_route_stays_in_the_app_folder(client):
    c, _ = client
    r = c.get("/..%2F..%2Fbackend%2F.env.example")
    assert "TRIALIQ_OLLAMA_URL" not in r.text


def test_mapping_decision_changes_the_prescreen(client):
    c, main = client
    assert c.post("/api/mappings/ECH-EGFR21", json={"approve": True, "loinc": "98979-8"}).json()["ok"]
    assert main.state["store"].approved_mappings() == {"ECH-EGFR21": "98979-8"}
    codes = {x["source_code"]: x for x in c.get("/api/lab-codes").json()}
    assert codes["ECH-EGFR21"]["mapping"]["status"] == "approved"


def test_unknown_trial_and_bad_nct(client):
    c, _ = client
    assert c.post("/api/trials/NCT00000000/screen").status_code == 400
    assert c.post("/api/trials", json={"nct_id": "not-an-id"}).status_code == 404


def test_mcp_tools():
    from backend.app.mcp_server import create_server

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/trials":
            return httpx.Response(200, json=[{"nct_id": "NCT1", "title": "T", "criteria": "approved", "last_screen": None}])
        return httpx.Response(404, json={"detail": "nope"})
    server = create_server(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t"))
    assert server is not None


def test_resumed_run_reuses_finished_chart_reviews(client):
    """A review saved before a crash is reused: the chart agent is not asked about that patient again."""
    c, main = client
    store, runner = main.state["store"], main.state["runner"]
    run_id = store.create_run("screen", "NCT06736574", "tester")
    cset = main.state["registry"].criteria("NCT06736574")
    store.set_snapshot(run_id, {"criteria": cset.model_dump(), "mappings": {}})
    ctx = runner._context(run_id)
    open_ids = [p for p, s in ctx.screens().items() if s.stage == "open"]
    done = open_ids[0]
    store.save_review(run_id, done, [])
    asked = []

    class Spy(FakeChat):
        def with_structured_output(self, schema, **kw):
            inner = super().with_structured_output(schema, **kw)

            class R:
                def invoke(self_, messages, config=None, **k):
                    asked.append((config or {}).get("metadata", {}).get("patient"))
                    return inner.invoke(messages, config)
            return R()
    runner.models = FakeModels(Spy({"Referral": [{"subject": "s", "body": "b"}] * 50}))
    runner._screen(run_id, {"nct_id": "NCT06736574"})
    assert done not in asked and set(open_ids[1:]) <= set(asked)
    assert store.reviews_done(run_id) == len(open_ids)


def test_security_overview(client):
    c, _ = client
    run_id = c.post("/api/trials/NCT07668388/screen", headers={"X-User": "tester"}).json()["run_id"]
    wait(c, run_id, {"awaiting_approval", "completed", "failed"})
    s = c.get("/api/security").json()
    assert {a["id"] for a in s["agents"]} >= {"chart", "evidence_reviewer", "outreach", "mapper"}
    chart = next(a for a in s["agents"] if a["id"] == "chart")
    assert chart["data"] == "chart" and "read_note" in chart["tools"]
    assert s["audit"]["ok"] and s["audit"]["entries"] >= 1
    assert s["egress"]["cloud"] == 0 and not s["cloud_phi"]
    assert any(p["type"] == "referrals" and p["run_id"] == run_id for p in s["pending"])
    assert c.get("/api/audit/verify").json()["ok"]
