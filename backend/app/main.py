"""TrialIQ API. FastAPI.

    .venv/bin/uvicorn backend.app.main:app --port 8020"""

import json
import mimetypes
import re
import statistics
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, settings
from .criteria import CriteriaSet
from .ehr.store import EHR
from .ehr.vocab import CLINICS
from .runner import RunError, Runner
from .store import Store, now
from .trials import ctgov
from .trials.registry import Registry

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.ehr_path.exists():
        from .ehr.generate import main as generate
        generate()
    state["store"] = Store(settings.app_db)
    state["ehr"] = EHR(settings.ehr_path)
    state["registry"] = Registry(settings.data_dir)
    state["runner"] = Runner(settings, state["store"], state["ehr"], state["registry"])
    state["runner"].recover()
    yield
    state["runner"].pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="TrialIQ", lifespan=lifespan)


def store() -> Store:
    return state["store"]


def runner() -> Runner:
    return state["runner"]


def registry() -> Registry:
    return state["registry"]


def ehr() -> EHR:
    return state["ehr"]


def actor_of(x_user: str | None) -> str:
    return (x_user or "coordinator").strip()[:60] or "coordinator"


def _bad(exc: Exception, code: int = 400):
    raise HTTPException(code, str(exc))


# --- overview ---------------------------------------------------------------------------------------------

@app.get("/api/health")
def health():
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).json().get("models", [])
        ollama = {"ok": True, "has_model": any(m["name"] == settings.local_model for m in tags)}
    except (httpx.HTTPError, ValueError, AttributeError):
        ollama = {"ok": False, "has_model": False}
    from .agent import llm

    return {"ok": True, "ollama": ollama, "models": runner().models.describe(),
            "cloud_phi": llm.cloud_phi(settings, llm.ModelStore(settings.data_dir)),
            "dry_run": settings.dry_run, "screen_date": settings.screen_date.isoformat()}


@app.get("/api/overview")
def overview():
    runs = store().runs(limit=200)
    e = ehr()
    clinics = defaultdict(int)
    for rec in e.records.values():
        clinics[rec.person.clinic] += 1
    return {
        "patients": len(e.records), "notes": sum(len(r.notes) for r in e.records.values()),
        "clinics": dict(clinics), "trials": len(registry().list()),
        "unmapped_codes": len(e.unmapped_codes()), "approved_mappings": len(store().approved_mappings()),
        "awaiting_approval": sum(r["status"] == "awaiting_approval" for r in runs),
        "running": sum(r["status"] == "running" for r in runs),
        "recent_runs": runs[:8],
    }


# --- trials -----------------------------------------------------------------------------------------------

class TrialIn(BaseModel):
    nct_id: str


@app.get("/api/trials")
def trials():
    out = []
    for t in registry().list():
        last = next((r for r in store().runs(t["nct_id"], 20) if r["kind"] == "screen"), None)
        out.append(t | {"last_screen": last})
    return out


@app.post("/api/trials")
def add_trial(body: TrialIn, x_user: str | None = Header(None)):
    try:
        trial = registry().add(body.nct_id)
    except ctgov.TrialNotFound as exc:
        _bad(exc, 404)
    except httpx.HTTPError as exc:
        _bad(RuntimeError(f"ClinicalTrials.gov is not reachable: {exc}"), 502)
    store().audit(actor_of(x_user), "trial.add", trial["nct_id"])
    return trial


@app.get("/api/ctgov/search")
def ctgov_search(condition: str, state_name: str = "Indiana"):
    try:
        return ctgov.search(condition, state_name)
    except httpx.HTTPError as exc:
        _bad(RuntimeError(f"ClinicalTrials.gov is not reachable: {exc}"), 502)


@app.get("/api/trials/{nct_id}")
def trial(nct_id: str):
    t = registry().get(nct_id)
    if t is None:
        raise HTTPException(404, "unknown trial")
    cset = registry().criteria(nct_id)
    return {"trial": t, "criteria": cset.model_dump() if cset else None, "criteria_source": registry().criteria_source(nct_id),
            "drafts": store().drafts(nct_id), "runs": store().runs(nct_id)}


@app.post("/api/trials/{nct_id}/criteria/build")
def build_criteria(nct_id: str, x_user: str | None = Header(None)):
    try:
        return {"run_id": runner().start_criteria(nct_id, actor_of(x_user))}
    except RunError as exc:
        _bad(exc)


class ApproveCriteria(BaseModel):
    draft_id: str | None = None
    criteria: dict | None = None


@app.post("/api/trials/{nct_id}/criteria/approve")
def approve_criteria(nct_id: str, body: ApproveCriteria, x_user: str | None = Header(None)):
    actor = actor_of(x_user)
    if body.criteria:
        data = body.criteria
    else:
        draft = next((d for d in store().drafts(nct_id) if d["id"] == body.draft_id), None)
        if draft is None:
            raise HTTPException(404, "unknown draft")
        data = draft["data"]

    from pydantic import ValidationError
    try:
        cset = CriteriaSet.model_validate({**data, "nct_id": nct_id, "source": "approved", "approved_by": actor,
                                           "approved_at": datetime.now(UTC).date().isoformat()})
    except ValidationError as exc:
        raise HTTPException(422, f"The criteria are not valid: {exc.errors()[0]['msg']}") from exc
    if problems := criteria_problems(cset):
        raise HTTPException(422, "Fix before approving: " + "; ".join(problems[:5]))
    registry().approve(cset)
    store().audit(actor, "criteria.approve", f"{nct_id} ({len(cset.criteria)} criteria)")
    return {"ok": True}


def criteria_problems(cset: CriteriaSet) -> list[str]:
    """Checks before a set of criteria can screen patients."""
    from .criteria import Atom

    problems = []
    ids = [c.id for c in cset.criteria]
    if len(set(ids)) != len(ids):
        problems.append("criterion ids repeat")
    if not any(c.screenable for c in cset.criteria):
        problems.append("no screenable criterion")

    def leaves(a: Atom):
        return [x for b in a.all_of for x in leaves(b)] if a.type == "group" else [a]
    for c in cset.criteria:
        if not c.screenable:
            continue
        if not c.atoms:
            problems.append(f"{c.id} has no computable rule (mark it not screenable, or add atoms)")
        for a in (x for atom in c.atoms for x in leaves(atom)):
            if a.type == "group" and not a.all_of:
                problems.append(f"{c.id}: empty group")
            if a.type == "lab" and (a.min is None and a.max is None):
                problems.append(f"{c.id}: lab '{a.concept}' has no threshold")
            if a.type == "lab" and not a.unit:
                problems.append(f"{c.id}: lab '{a.concept}' has no unit")
            if a.type in ("condition", "medication", "lab") and not a.codes and not a.keywords:
                problems.append(f"{c.id}: '{a.concept}' has no codes and no keywords")
            if a.min is not None and a.max is not None and a.min > a.max:
                problems.append(f"{c.id}: '{a.concept}' min is above max")
    return problems


@app.get("/api/trials/{nct_id}/estimate")
def estimate(nct_id: str):
    """What a screening run would cost, from a prescreen without a model: patients for chart review, questions, and
    minutes at the past speed of the chart model."""
    from .agent.chart import build_questions
    from .engine import Engine, atom_specs, funnel

    cset = registry().criteria(nct_id)
    if cset is None:
        raise HTTPException(400, "approve the criteria of this trial first")
    screens = Engine(ehr(), settings.screen_date, store().approved_mappings()).screen_all(cset)
    specs, hints = atom_specs(cset), {c.id: c.hints for c in cset.criteria if c.hints}
    open_ = [s for s in screens if s.stage == "open"]
    per_patient: dict[tuple, int] = defaultdict(int)
    for c in store().llm_calls():
        if c["agent"] in ("chart", "evidence_reviewer") and c["patient"]:
            per_patient[(c["run_id"], c["patient"])] += c["ms"]
    seconds = statistics.median(per_patient.values()) / 1000 if per_patient else 20.0
    return {"funnel": funnel(screens), "questions": sum(len(build_questions(s, specs, hints)) for s in open_),
            "seconds_per_patient": round(seconds, 1), "minutes": round(len(open_) * seconds / 60 / max(1, settings.concurrency), 1),
            "chart_models": runner().models.describe()["chart"], "criteria_version": cset.version,
            "mappings": len(store().approved_mappings())}


@app.post("/api/trials/{nct_id}/screen")
def screen(nct_id: str, x_user: str | None = Header(None)):
    try:
        return {"run_id": runner().start_screen(nct_id, actor_of(x_user))}
    except RunError as exc:
        _bad(exc)


# --- runs -------------------------------------------------------------------------------------------------

def _usage(calls: list[dict]) -> dict:
    per_agent: dict[str, dict] = defaultdict(lambda: {"calls": 0, "ms": 0, "tokens_in": 0, "tokens_out": 0, "failed": 0})
    models: dict[str, int] = defaultdict(int)
    for c in calls:
        a = per_agent[c["agent"]]
        a["calls"] += 1
        a["ms"] += c["ms"]
        a["tokens_in"] += c["tokens_in"]
        a["tokens_out"] += c["tokens_out"]
        a["failed"] += not c["ok"]
        if c["ok"]:
            models[c["model"] or c["provider"] or "?"] += 1
    return {"calls": len(calls), "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in calls),
            "failed": sum(not c["ok"] for c in calls), "per_agent": dict(per_agent), "models": dict(models),
            "median_ms": statistics.median([c["ms"] for c in calls]) if calls else None}


@app.get("/api/runs")
def runs(nct_id: str | None = None):
    return store().runs(nct_id)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    r = store().run(run_id)
    if r is None:
        raise HTTPException(404, "unknown run")
    snap = store().snapshot(run_id)
    return r | {"usage": _usage(store().llm_calls(run_id)), "busy": run_id in runner().busy,
                "criteria": snap.get("criteria"), "mappings_used": snap.get("mappings")}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0):
    return store().events(run_id, after)


@app.get("/api/runs/{run_id}/results")
def run_results(run_id: str, verdict: str | None = None):
    out = []
    for r in store().results(run_id, verdict):
        d = r["data"]
        out.append({"person_id": r["person_id"], "stage": r["stage"], "verdict": r["verdict"], "person": d["person"],
                    "reasons": d["reasons"], "criteria": {c["id"]: c["status"] for c in d["screen"]["criteria"]},
                    "notes_used": sum(1 for x in d["resolutions"] if x["status"] != "unknown"),
                    "flags": sum(1 for x in d["resolutions"] if x["problems"] or x["reviewer"].startswith("rejected"))})
    return out


@app.get("/api/runs/{run_id}/patients/{person_id}")
def run_patient(run_id: str, person_id: str):
    r = store().result(run_id, person_id)
    if r is None or person_id not in ehr().records:
        raise HTTPException(404, "no result")
    rec = ehr().get(person_id)
    return r | {"notes": [{"note_id": n.note_id, "date": n.date.isoformat(), "type": n.type, "author": n.author, "text": n.text}
                          for n in rec.notes]}


@app.get("/api/runs/{run_id}/outreach")
def run_outreach(run_id: str):
    return store().outreach(run_id)


class Decision(BaseModel):
    person_id: str
    decision: str  # approve | reject
    subject: str | None = None
    body: str | None = None
    reason: str | None = None  # why a referral is rejected: feedback for the learning loop


@app.post("/api/runs/{run_id}/decisions")
def decide(run_id: str, decisions: list[Decision], x_user: str | None = Header(None)):
    try:
        runner().decide(run_id, [d.model_dump() for d in decisions], actor_of(x_user))
    except RunError as exc:
        _bad(exc)
    return {"ok": True}


class FeedbackIn(BaseModel):
    criterion_id: str = ""  # empty: feedback on the verdict
    correct: str            # verdict: eligible | ineligible | needs_review; criterion: met | not_met | unknown
    reason: str = ""


@app.post("/api/runs/{run_id}/patients/{person_id}/feedback")
def add_feedback(run_id: str, person_id: str, body: FeedbackIn, x_user: str | None = Header(None)):
    r, result = store().run(run_id), store().result(run_id, person_id)
    if r is None or result is None:
        raise HTTPException(404, "no result")
    if body.criterion_id:
        crit = next((c for c in result["data"]["screen"]["criteria"] if c["id"] == body.criterion_id), None)
        if crit is None or body.correct not in ("met", "not_met", "unknown"):
            raise HTTPException(422, "unknown criterion, or the value is not met, not_met or unknown")
        kind, system = "criterion", crit["status"]
    else:
        if body.correct not in ("eligible", "ineligible", "needs_review"):
            raise HTTPException(422, "the verdict must be eligible, ineligible or needs_review")
        kind, system = "verdict", result["verdict"]
    if body.correct == system:
        raise HTTPException(422, "that is what the system decided already")
    fid = store().add_feedback(r["nct_id"], run_id, person_id, kind, body.criterion_id, system, body.correct,
                               body.reason.strip()[:500], actor_of(x_user))
    store().audit(actor_of(x_user), "feedback.add", f"{r['nct_id']} {person_id} {body.criterion_id or 'verdict'}: {system} -> {body.correct}")
    return {"id": fid}


@app.get("/api/trials/{nct_id}/feedback")
def trial_feedback(nct_id: str):
    return store().feedback(nct_id)


@app.post("/api/trials/{nct_id}/learn")
def learn(nct_id: str, x_user: str | None = Header(None)):
    try:
        return {"run_id": runner().start_learning(nct_id, actor_of(x_user))}
    except RunError as exc:
        _bad(exc)


@app.get("/api/trials/{nct_id}/lessons")
def trial_lessons(nct_id: str):
    return store().lessons(nct_id)


class LessonDecision(BaseModel):
    approve: bool


@app.post("/api/lessons/{lesson_id}")
def decide_lesson(lesson_id: str, body: LessonDecision, x_user: str | None = Header(None)):
    try:
        new = runner().decide_lesson(lesson_id, body.approve, actor_of(x_user))
    except RunError as exc:
        _bad(exc)
    return {"ok": True, "version": new.version if new else None}


@app.get("/api/runs/{run_id}/patients/{person_id}/trace")
def patient_trace(run_id: str, person_id: str):
    """Every model call, tool call, guard and reviewer decision of one patient, with times, for the waterfall."""
    calls = [c for c in store().llm_calls(run_id) if c["patient"] == person_id]
    events = [e for e in store().events(run_id) if e["patient"] == person_id]
    return {"calls": calls, "events": events}


@app.get("/api/compare")
def compare(a: str, b: str):
    """What changed between two screening runs: verdicts, criteria version, mappings and models."""
    ra, rb = store().run(a), store().run(b)
    if ra is None or rb is None:
        raise HTTPException(404, "unknown run")
    va = {r["person_id"]: r["verdict"] for r in store().results(a)}
    vb = {r["person_id"]: r["verdict"] for r in store().results(b)}
    moved = [{"person_id": p, "a": va[p], "b": vb.get(p)} for p in sorted(va) if p in vb and va[p] != vb[p]]
    sa, sb = store().snapshot(a), store().snapshot(b)

    def crit_map(snap):
        return {c["id"]: c for c in (snap.get("criteria") or {}).get("criteria", [])}
    ca, cb = crit_map(sa), crit_map(sb)
    criteria = [{"id": k, "change": "added" if k not in ca else "removed" if k not in cb else "changed"}
                for k in sorted(set(ca) | set(cb)) if ca.get(k) != cb.get(k)]

    def models_of(run_id):
        return dict(_usage(store().llm_calls(run_id))["models"])
    return {"a": ra, "b": rb, "moved": moved, "moves": dict(Counter(f"{m['a']}→{m['b']}" for m in moved)),
            "criteria": criteria, "versions": [(sa.get("criteria") or {}).get("version", 1), (sb.get("criteria") or {}).get("version", 1)],
            "mappings": [sa.get("mappings") or {}, sb.get("mappings") or {}], "models": [models_of(a), models_of(b)],
            "verdicts": [ra["summary"].get("verdicts"), rb["summary"].get("verdicts")]}


# --- terminology ------------------------------------------------------------------------------------------

@app.get("/api/lab-codes")
def lab_codes():
    maps = {m["source_code"]: m for m in store().mappings()}
    return [c | {"mapping": maps.get(c["source_code"]), "clinic_name": CLINICS.get(c["clinic"], c["clinic"])}
            for c in ehr().lab_codes]


@app.post("/api/mappings/run")
def run_mapper(x_user: str | None = Header(None)):
    return {"run_id": runner().start_mapping(actor_of(x_user))}


class MappingDecision(BaseModel):
    approve: bool
    loinc: str | None = None


LOINC_FORMAT = re.compile(r"^\d{1,7}-\d$")


def mapping_problem(source_code: str, loinc: str | None) -> str:
    """Why a mapping must not be approved: an unknown local code, a code that is not LOINC, or a unit that the
    analyte of the LOINC code cannot use."""
    from .ehr.vocab import LABS, LOINC_ANALYTE
    from .units import UnitError, convert

    code = next((c for c in ehr().lab_codes if c["source_code"] == source_code), None)
    if code is None:
        return "unknown local code"
    if not loinc or not LOINC_FORMAT.match(loinc):
        return f"'{loinc}' is not a LOINC code (format 12345-6)"
    analyte = LOINC_ANALYTE.get(loinc)
    if analyte in LABS:
        try:
            convert(1.0, code["unit"], LABS[analyte][2])
        except UnitError:
            return f"the unit {code['unit']} of {source_code} does not fit {loinc} ({LABS[analyte][1]})"
    return ""


@app.post("/api/mappings/{source_code}")
def decide_mapping(source_code: str, body: MappingDecision, x_user: str | None = Header(None)):
    if body.approve:
        loinc = body.loinc or (store().mapping(source_code) or {}).get("loinc")
        if problem := mapping_problem(source_code, loinc):
            raise HTTPException(422, problem)
    if store().mapping(source_code) is None:
        if not body.loinc:
            raise HTTPException(404, "no proposal for this code")
        store().propose_mapping(source_code, body.loinc, "high", "entered by a person", actor_of(x_user))
    store().decide_mapping(source_code, body.approve, actor_of(x_user), body.loinc)
    return {"ok": True}


# --- models, evals, audit ---------------------------------------------------------------------------------

def _models_overview() -> dict:
    from .agent import llm

    st = llm.ModelStore(settings.data_dir)
    data = st.load()
    calls = store().llm_calls()
    usage = _usage(calls)
    by_provider: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    for c in calls:
        key = c["provider"] or ("ollama" if c["model"] == settings.local_model else "")
        by_provider[key]["calls"] += 1
        by_provider[key]["tokens"] += c["tokens_in"] + c["tokens_out"]
    providers = []
    for pid, info in llm.PROVIDERS.items():
        key = llm.api_key(settings, st, pid)
        saved = data.get("keys", {}).get(pid, "")
        providers.append({"id": pid, "name": info["name"], "maker": info["maker"], "keys_url": info["keys_url"],
                          "connected": bool(key), "key_hint": f"…{key[-4:]}" if key else "",
                          "key_source": "page" if saved else ("env" if key else ""),
                          "models": data.get("models", {}).get(pid, []), "model": llm.provider_model(settings, st, pid),
                          "paused": llm.paused(pid), "usage": by_provider.get(pid, {"calls": 0, "tokens": 0})})
    levels = [{"level": lv, "about": llm.LEVEL_INFO[lv], "phi": lv in llm.PHI_LEVELS, "route": llm.routes(settings, st)[lv],
               "chain": [{"provider": p, "model": m, "skipped": r} for p, m, r in llm.chain_for(settings, st, lv)]}
              for lv in llm.LEVELS]
    return {"providers": providers, "levels": levels, "cloud_phi": llm.cloud_phi(settings, st), "custom_routes": bool(data.get("routes")),
            "local": {"model": settings.local_model, "usage": by_provider.get("ollama", {"calls": 0, "tokens": 0})},
            "chains": runner().models.describe(), "usage": usage}


@app.get("/api/models")
def models():
    return _models_overview()


class KeyBody(BaseModel):
    api_key: str


@app.put("/api/models/{provider}/key")
def save_key(provider: str, body: KeyBody, x_user: str | None = Header(None)):
    from .agent import llm

    if provider not in llm.PROVIDERS:
        raise HTTPException(404, "unknown provider")
    key = body.api_key.strip()
    if len(key) < 8:
        raise HTTPException(422, "The key is too short.")
    try:
        names = llm.list_models(provider, key)  # a key that the provider rejects is not saved
    except llm.ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    default = llm.PROVIDERS[provider]["default"]

    def change(data: dict) -> None:
        data.setdefault("keys", {})[provider] = key
        data.setdefault("models", {})[provider] = names
        data.setdefault("key_meta", {})[provider] = {"saved_at": now()}
        if not data.setdefault("selected", {}).get(provider):
            data["selected"][provider] = default if default in names or not names else names[0]
    llm.ModelStore(settings.data_dir).update(change)
    store().audit(actor_of(x_user), "models.key_saved", f"{llm.PROVIDERS[provider]['maker']} key saved (…{key[-4:]})")
    return _models_overview()


@app.delete("/api/models/{provider}/key")
def remove_key(provider: str, x_user: str | None = Header(None)):
    from .agent import llm

    def change(data: dict) -> None:
        data.get("keys", {}).pop(provider, None)
        data.get("models", {}).pop(provider, None)
        data.get("key_meta", {}).pop(provider, None)
    llm.ModelStore(settings.data_dir).update(change)
    store().audit(actor_of(x_user), "models.key_removed", provider)
    return _models_overview()


class ModelChoice(BaseModel):
    model: str


@app.put("/api/models/{provider}/model")
def choose_model(provider: str, body: ModelChoice, x_user: str | None = Header(None)):
    from .agent import llm

    if provider not in llm.PROVIDERS or not body.model.strip():
        raise HTTPException(404, "unknown provider or empty model")
    llm.ModelStore(settings.data_dir).update(lambda d: d.setdefault("selected", {}).__setitem__(provider, body.model.strip()))
    store().audit(actor_of(x_user), "models.model_chosen", f"{provider}: {body.model}")
    return _models_overview()


@app.post("/api/models/{provider}/check")
def check_model(provider: str, body: ModelChoice):
    from .agent import llm

    key = llm.api_key(settings, llm.ModelStore(settings.data_dir), provider)
    if not key:
        raise HTTPException(400, "Add a key first.")
    try:
        checks = llm.check_model(provider, body.model, key)
    except Exception as exc:  # the client could not even be made: show it as a failed check
        checks = [{"name": "Setup", "ok": False, "detail": str(exc)[:220], "ms": 0}]
    ok = all(c["ok"] for c in checks)
    llm.ModelStore(settings.data_dir).update(
        lambda d: d.setdefault("key_meta", {}).setdefault(provider, {}).update({"checked_at": now(), "check_ok": ok}))
    return {"model": body.model, "checks": checks, "ok": ok}


class RoutesBody(BaseModel):
    routes: dict[str, list[str]]


@app.put("/api/models/routes")
def save_routes(body: RoutesBody, x_user: str | None = Header(None)):
    from .agent import llm

    clean = {}
    for level in llm.LEVELS:
        chain = [p for p in body.routes.get(level, []) if p in llm.PROVIDERS]
        if len(set(chain)) != len(chain):
            raise HTTPException(422, f"The {level} chain repeats a provider.")
        clean[level] = chain
    llm.ModelStore(settings.data_dir).update(lambda d: d.__setitem__("routes", clean))
    store().audit(actor_of(x_user), "models.routes_saved", "; ".join(f"{k}: {' > '.join(v) or 'local'}" for k, v in clean.items()))
    return _models_overview()


class PrivacyBody(BaseModel):
    cloud_phi: bool


@app.put("/api/models/privacy")
def save_privacy(body: PrivacyBody, x_user: str | None = Header(None)):
    from .agent import llm

    llm.ModelStore(settings.data_dir).update(lambda d: d.__setitem__("cloud_phi", body.cloud_phi))
    store().audit(actor_of(x_user), "models.cloud_phi", "on" if body.cloud_phi else "off")
    return _models_overview()


# --- security -----------------------------------------------------------------------------------------------

def _stopped(r: dict) -> bool:
    """An answer that a code guard or the evidence reviewer stopped. An answer the model did not give is not."""
    real = [p for p in r.get("problems", []) if p != "no answer"]
    return bool(real) or str(r.get("reviewer", "")).startswith("rejected") or r.get("reviewer") == "error"


def _age_days(at: str | None) -> int | None:
    if not at:
        return None
    return (datetime.now(UTC) - datetime.fromisoformat(at)).days


@app.get("/api/security")
def security_overview():
    from . import security as sec
    from .agent import llm

    st = llm.ModelStore(settings.data_dir)
    saved = st.load()
    phi_on = llm.cloud_phi(settings, st)
    events = store().security_events(5000)
    calls = store().llm_calls()
    chains = runner().models.describe()
    runs = store().runs(limit=200)

    used: dict[str, Counter] = defaultdict(Counter)
    for e in store().tool_events():
        if m := re.search(r"(\w+)\(", e["text"]):
            used[e["agent"]][m.group(1)] += 1
    agents = []
    for key, a in sec.AGENTS.items():
        level = llm.AGENT_LEVEL[key]
        mine = [c for c in calls if c["agent"] == key]
        cloud = [c for c in mine if (c["provider"] or "ollama") != "ollama"]
        agents.append({
            "id": key, "name": a["name"], "data": level, "data_label": sec.DATA_LABEL[level], "tools": a["tools"],
            "tool_uses": dict(used[key]), "unused": [t for t in a["tools"] if not used[key][t]],
            "output": a["output"], "gate": a["gate"], "models": chains.get(level, []), "calls": len(mine),
            "cloud_calls": len(cloud), "blocked": sum(e["agent"] == key and e["outcome"] == "blocked" for e in events),
            "last_active": max((c["at"] for c in mine), default=None)})

    checks = []
    for run in sorted((r for r in runs if r["kind"] == "screen"), key=lambda r: r["created_at"])[-8:]:
        reviews = store().reviews(run["id"])
        answers = [r for rs in reviews.values() for r in rs]
        stopped = sum(_stopped(r) for r in answers)
        accepted = sum(1 for r in answers if r.get("status") != "unknown" and r.get("reviewer") == "approved")
        checks.append({"run_id": run["id"], "nct_id": run["nct_id"], "at": run["created_at"], "patients": len(reviews),
                       "answers": len(answers), "accepted": accepted, "stopped": stopped,
                       "security": sum(e["run_id"] == run["id"] for e in events)})

    pending = []
    for r in runs:
        if r["status"] == "awaiting_approval":
            n = sum(o["status"] == "proposed" for o in store().outreach(r["id"]))
            pending.append({"type": "referrals", "count": n, "nct_id": r["nct_id"], "run_id": r["id"], "at": r["updated_at"]})
    for t in registry().list():
        n = sum(lesson["status"] == "proposed" for lesson in store().lessons(t["nct_id"]))
        if n:
            pending.append({"type": "lessons", "count": n, "nct_id": t["nct_id"]})
    proposed = [m for m in store().mappings() if m["status"] == "proposed"]
    if proposed:
        pending.append({"type": "mappings", "count": len(proposed)})

    keys = []
    for pid, info in llm.PROVIDERS.items():
        key = llm.api_key(settings, st, pid)
        if not key:
            continue
        meta = saved.get("key_meta", {}).get(pid, {})
        reads_notes = phi_on and any(pid in llm.routes(settings, st)[lv] for lv in llm.PHI_LEVELS)
        keys.append({"provider": pid, "name": info["name"], "maker": info["maker"], "hint": f"…{key[-4:]}",
                     "source": "page" if saved.get("keys", {}).get(pid) else "env", "age_days": _age_days(meta.get("saved_at")),
                     "checked_at": meta.get("checked_at"), "check_ok": meta.get("check_ok"), "reads_notes": reads_notes})

    chart_calls = [c for c in calls if llm.AGENT_LEVEL.get(c["agent"]) in llm.PHI_LEVELS]
    injected = sorted({(e["run_id"], e["patient"]) for e in events if e["type"] == "injection" and e["patient"]})
    audit_state = store().verify_audit()
    activity = [{"at": e["at"], "kind": "security", "type": e["type"], "outcome": e["outcome"], "agent": e["agent"],
                 "patient": e["patient"], "run_id": e["run_id"], "text": e["detail"]} for e in events[:12]]
    activity += [{"at": a["at"], "kind": "audit", "type": a["action"], "outcome": "", "agent": a["actor"], "patient": "",
                  "run_id": "", "text": a["detail"]} for a in store().audit_log(12)]
    activity.sort(key=lambda x: x["at"], reverse=True)

    return {
        "agents": agents, "checks": checks, "pending": pending, "keys": keys, "audit": audit_state,
        "cloud_phi": phi_on, "activity": activity[:12],
        "events": {t: sum(e["type"] == t for e in events) for t in ("tool_blocked", "injection", "egress_blocked", "referral_blocked")},
        "injected": [{"run_id": r, "patient": p} for r, p in injected],
        "egress": {"chart_calls": len(chart_calls),
                   "cloud": sum((c["provider"] or "ollama") != "ollama" for c in chart_calls)},
        "totals": {"answers": sum(c["answers"] for c in checks), "stopped": sum(c["stopped"] for c in checks),
                   "accepted": sum(c["accepted"] for c in checks), "blocked": sum(e["outcome"] == "blocked" for e in events)},
    }


@app.get("/api/audit/verify")
def audit_verify():
    return store().verify_audit()


@app.get("/api/evals")
def evals():
    reports = sorted((ROOT / "reports").glob("evals-*.json"))
    return json.loads(reports[-1].read_text()) if reports else None


@app.get("/api/audit")
def audit():
    return store().audit_log()


mimetypes.add_type("application/manifest+json", ".webmanifest")
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        target = (DIST / path).resolve()
        inside = target.is_relative_to(DIST.resolve())  # no "../" out of the web app folder
        return FileResponse(target if path and inside and target.is_file() else DIST / "index.html")
