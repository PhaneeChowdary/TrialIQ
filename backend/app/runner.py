"""Runs the agent jobs in background threads.

Screening runs are LangGraph threads in a SQLite checkpointer: a run that waits for approval, or that a server stop
interrupted, continues from its last step. Criteria and mapping runs are short and start again from the beginning.
"""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .agent.guards import Deadline
from .agent.llm import Models
from .agent.protocol import CriteriaBuilder, LoincMapper
from .agent.screening import ScreenContext, build_screening
from .agent.telemetry import UsageRecorder
from .agent.tools import TerminologyCache
from .config import Settings
from .ehr.store import EHR
from .store import Store
from .trials.registry import Registry


class RunError(RuntimeError):
    pass


class Runner:
    def __init__(self, settings: Settings, store: Store, ehr: EHR, registry: Registry) -> None:
        self.s, self.store, self.ehr, self.registry = settings, store, ehr, registry
        self.models = Models(settings)
        self.cache = TerminologyCache(settings.data_dir / "terminology_cache.json")
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")
        self.checkpointer = SqliteSaver(sqlite3.connect(settings.checkpoints, check_same_thread=False))
        self.lock = threading.Lock()
        self.busy: set[str] = set()

    def _callbacks(self, run_id: str) -> list:
        return [UsageRecorder(lambda call: self.store.llm_call(run_id, call))]

    def _event(self, run_id: str):
        return lambda kind, agent="", patient="", text="", data=None: self.store.event(run_id, kind, agent, patient, text, data)

    def _guarded(self, run_id: str, work) -> None:
        with self.lock:
            self.busy.add(run_id)
        try:
            work()
        except Exception as exc:
            self.store.update_run(run_id, status="failed", error=str(exc)[:500])
            self.store.event(run_id, "error", "runner", text=str(exc)[:500])
        finally:
            with self.lock:
                self.busy.discard(run_id)

    # --- screening ------------------------------------------------------------------------------------------

    def start_screen(self, nct_id: str, actor: str) -> str:
        trial, cset = self.registry.get(nct_id), self.registry.criteria(nct_id)
        if trial is None:
            raise RunError(f"unknown trial {nct_id}")
        if cset is None:
            raise RunError("approve the criteria of this trial first")
        run_id = self.store.create_run("screen", nct_id, actor)
        # freeze what this run decides with: later edits to criteria or mappings apply only to new runs
        self.store.set_snapshot(run_id, {"criteria": cset.model_dump(), "mappings": self.store.approved_mappings()})
        self.store.audit(actor, "screen.start", f"{nct_id} run {run_id}")
        self.pool.submit(self._guarded, run_id, lambda: self._screen(run_id, {"nct_id": nct_id}))
        return run_id

    def _context(self, run_id: str) -> ScreenContext:
        from .criteria import CriteriaSet

        run = self.store.run(run_id)
        snap = self.store.snapshot(run_id)
        trial = self.registry.get(run["nct_id"])
        cset = CriteriaSet.model_validate(snap["criteria"]) if snap.get("criteria") else self.registry.criteria(run["nct_id"])
        mappings = snap.get("mappings") if snap else self.store.approved_mappings()
        return ScreenContext(run_id, trial, cset, self.ehr, self.store, self.models, self.s, self._callbacks(run_id),
                             Deadline(self.s.run_time_limit), mappings)

    def _screen(self, run_id: str, graph_input) -> None:
        ctx = self._context(run_id)
        graph = build_screening(ctx, self.checkpointer)
        config = {"configurable": {"thread_id": run_id}, "callbacks": ctx.callbacks, "max_concurrency": self.s.concurrency,
                  "recursion_limit": 1000}
        if graph_input is None and not graph.get_state(config).values:  # never started: start from the beginning
            graph_input = {"nct_id": ctx.trial["nct_id"]}
        if self.store.run(run_id)["status"] == "queued":
            self.store.update_run(run_id, status="running", summary={"started_at": datetime.now(UTC).isoformat()})
        # sync durability: each finished chart review is saved at once, so a crash or a restart redoes only the
        # patients that were not finished
        graph.invoke(graph_input, config, durability="sync")
        state = graph.get_state(config)
        if state.next:  # paused at the approval interrupt
            self.store.update_run(run_id, status="awaiting_approval", stage="approval")
        else:
            self.store.update_run(run_id, status="completed", stage="done",
                                  summary={"finished_at": datetime.now(UTC).isoformat()})

    def decide(self, run_id: str, decisions: list[dict], actor: str) -> None:
        run = self.store.run(run_id)
        if run is None or run["status"] != "awaiting_approval":
            raise RunError("this run is not waiting for approval")
        drafts = {o["person_id"] for o in self.store.outreach(run_id) if o["status"] == "proposed"}
        given = [d.get("person_id") for d in decisions]
        if any(d.get("decision") not in ("approve", "reject") for d in decisions):
            raise RunError("each decision must be approve or reject")
        if len(set(given)) != len(given) or set(given) != drafts:
            raise RunError(f"decide every draft of this run once: {len(drafts)} draft(s), {len(set(given) & drafts)} decided")
        for d in decisions:
            d["actor"] = actor
            self.store.audit(actor, f"outreach.{d.get('decision')}", f"run {run_id} patient {d.get('person_id')}")
            if d.get("decision") == "reject" and (d.get("reason") or "").strip():
                self.store.add_feedback(run["nct_id"], run_id, d["person_id"], "verdict", "", "eligible", "ineligible",
                                        d["reason"].strip()[:500], actor)
        self.store.update_run(run_id, status="running")
        self.pool.submit(self._guarded, run_id, lambda: self._screen(run_id, Command(resume=decisions)))

    # --- protocol jobs --------------------------------------------------------------------------------------

    def start_criteria(self, nct_id: str, actor: str) -> str:
        trial = self.registry.get(nct_id)
        if trial is None:
            raise RunError(f"unknown trial {nct_id}")
        run_id = self.store.create_run("criteria", nct_id, actor)

        def work():
            self.store.update_run(run_id, status="running", stage="criteria")
            builder = CriteriaBuilder(self.models, self.ehr, self.cache, self._callbacks(run_id), self._event(run_id),
                                      Deadline(self.s.run_time_limit))
            cset, warnings = builder.run(trial)
            draft_id = self.store.save_draft(nct_id, run_id, cset.model_dump(), warnings)
            self.store.update_run(run_id, status="completed", stage="done",
                                  summary={"draft_id": draft_id, "criteria": len(cset.criteria), "warnings": len(warnings)})
        self.pool.submit(self._guarded, run_id, work)
        return run_id

    def start_mapping(self, actor: str) -> str:
        run_id = self.store.create_run("mapping", "", actor)

        def work():
            self.store.update_run(run_id, status="running", stage="mapping")
            mapper = LoincMapper(self.models, self.ehr, self.cache, self._callbacks(run_id), self._event(run_id))
            todo = [c for c in self.ehr.unmapped_codes() if (self.store.mapping(c["source_code"]) or {}).get("status") != "approved"]
            for proposal, problems in mapper.run(todo):
                reason = proposal.reason + (f" Checks: {'; '.join(problems)}" if problems else "")
                self.store.propose_mapping(proposal.source_code, proposal.loinc, proposal.confidence, reason, "mapper agent")
            self.store.update_run(run_id, status="completed", stage="done", summary={"proposals": len(todo)})
        self.pool.submit(self._guarded, run_id, work)
        return run_id

    def start_learning(self, nct_id: str, actor: str) -> str:
        """Reflection on the open feedback of a trial: lessons, each checked and previewed, for a person to approve."""
        import uuid

        from .agent.reflection import Reflector
        from .learning import CHANGES, apply_lesson, check_lesson, impact

        cset = self.registry.criteria(nct_id)
        items = self.store.feedback(nct_id, open_only=True)
        if cset is None:
            raise RunError("approve the criteria of this trial first")
        if not items:
            raise RunError("no open feedback for this trial")
        run_id = self.store.create_run("learning", nct_id, actor)

        def work():
            self.store.update_run(run_id, status="running", stage="reflection")
            results = {(i["run_id"], i["person_id"]): self.store.result(i["run_id"], i["person_id"]) for i in items}
            proposals = Reflector(self.models, self._callbacks(run_id), self._event(run_id)).propose(items, results, cset, self.ehr)
            by_id = {i["id"]: i for i in items}
            # the same lesson from several feedback items is one lesson that cites them all
            merged: dict[tuple, dict] = {}
            for lp in proposals:
                key = (lp.kind, lp.criterion_id, lp.atom_index if lp.kind in ("add_keyword", "remove_keyword", "add_code") else None,
                       " ".join(lp.value.lower().split()))
                if key in merged:
                    merged[key]["feedback_ids"].append(lp.feedback_id)
                else:
                    merged[key] = lp.model_dump() | {"feedback_ids": [lp.feedback_id]}
            saved = 0
            for lesson in merged.values():
                lesson |= {"id": uuid.uuid4().hex[:8], "nct_id": nct_id, "run_id": run_id}
                patients = sorted({by_id[f]["person_id"] for f in lesson["feedback_ids"]})
                fb = by_id[lesson["feedback_ids"][0]]
                lesson["problems"] = check_lesson(lesson, cset, self.ehr, patients)
                if lesson["kind"] in CHANGES and not lesson["problems"]:
                    lesson["impact"] = impact(self.ehr, self.s.screen_date, self.store.approved_mappings(), cset,
                                              apply_lesson(cset, lesson), patients)
                lesson["status"] = "proposed" if not lesson["problems"] else "blocked"
                self.store.save_lesson(lesson)
                self.store.mark_feedback(lesson["feedback_ids"], lesson["id"])
                self.store.event(run_id, "guard" if lesson["problems"] else "stage", "reflection", fb["person_id"],
                                 f"{lesson['kind']} {lesson.get('criterion_id', '')}: {lesson['value'][:80]}"
                                 + (f" — blocked: {'; '.join(lesson['problems'])}" if lesson["problems"] else ""))
                saved += 1
            self.store.update_run(run_id, status="completed", stage="done",
                                  summary={"feedback": len(items), "lessons": saved})
        self.pool.submit(self._guarded, run_id, work)
        return run_id

    def decide_lesson(self, lesson_id: str, approve: bool, actor: str):
        """Approve: re-check the lesson against the current criteria, apply it, save a new criteria version."""
        from .learning import CHANGES, apply_lesson, check_lesson

        lesson = self.store.lesson(lesson_id)
        if lesson is None or lesson["status"] != "proposed":
            raise RunError("this lesson is not waiting for a decision")
        if not approve:
            self.store.decide_lesson(lesson_id, "rejected", actor)
            self.store.audit(actor, "lesson.reject", f"{lesson['nct_id']} {lesson['kind']} {lesson['value'][:60]}")
            return None
        cset = self.registry.criteria(lesson["nct_id"])
        fb_patients = [f["person_id"] for f in self.store.feedback(lesson["nct_id"]) if f["id"] in lesson["feedback_ids"]]
        if problems := check_lesson(lesson, cset, self.ehr, fb_patients):
            raise RunError("the lesson no longer fits the criteria: " + "; ".join(problems))
        if lesson["kind"] in CHANGES:
            new = apply_lesson(cset, lesson)
            new.approved_by, new.source = actor, "approved"
            new.changes = [*cset.changes, f"v{cset.version + 1}: {lesson['kind']} {lesson['criterion_id']} "
                                          f"'{lesson['value']}' (lesson {lesson_id}, {actor})"]
            self.registry.approve(new)
        self.store.decide_lesson(lesson_id, "approved", actor)
        self.store.audit(actor, "lesson.approve", f"{lesson['nct_id']} {lesson['kind']} {lesson['criterion_id']} {lesson['value'][:60]}")
        return self.registry.criteria(lesson["nct_id"])

    # --- recovery -------------------------------------------------------------------------------------------

    def recover(self) -> int:
        """After a restart: continue screening runs that were running. Paused runs keep waiting for approval."""
        count = 0
        for run in self.store.runs(limit=200):
            if run["status"] in ("running", "queued") and run["id"] not in self.busy:
                if run["kind"] == "screen":
                    self.store.event(run["id"], "stage", "runner", text="continued after a restart")
                    self.pool.submit(self._guarded, run["id"], lambda rid=run["id"]: self._screen(rid, None))
                    count += 1
                else:
                    self.store.update_run(run["id"], status="failed", error="stopped by a restart; start it again")
        return count
