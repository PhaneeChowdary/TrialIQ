"""App database: runs, their events and model calls, patient results, LOINC mappings, criteria drafts,
referral drafts, security events and the audit log. The EHR is a separate read-only database. SQLite. A hash chain."""

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .security import GENESIS, entry_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT, nct_id TEXT, status TEXT, stage TEXT, actor TEXT, created_at TEXT, updated_at TEXT,
  summary TEXT DEFAULT '{}', error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT, seq INTEGER, at TEXT, kind TEXT, agent TEXT, patient TEXT, text TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_events ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS llm_calls (
  run_id TEXT, at TEXT, agent TEXT, patient TEXT, model TEXT, provider TEXT, ms INTEGER, tokens_in INTEGER,
  tokens_out INTEGER, ok INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls ON llm_calls(run_id);
CREATE TABLE IF NOT EXISTS patient_results (
  run_id TEXT, person_id TEXT, stage TEXT, verdict TEXT, data TEXT, PRIMARY KEY (run_id, person_id)
);
CREATE TABLE IF NOT EXISTS mappings (
  source_code TEXT PRIMARY KEY, loinc TEXT, status TEXT, confidence TEXT, reason TEXT, proposed_by TEXT,
  proposed_at TEXT, decided_by TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS criteria_drafts (
  id TEXT PRIMARY KEY, nct_id TEXT, run_id TEXT, created_at TEXT, status TEXT, data TEXT, warnings TEXT
);
CREATE TABLE IF NOT EXISTS outreach (
  run_id TEXT, person_id TEXT, status TEXT, subject TEXT, body TEXT, to_name TEXT, decided_by TEXT, decided_at TEXT,
  PRIMARY KEY (run_id, person_id)
);
CREATE TABLE IF NOT EXISTS audit (at TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, nct_id TEXT, run_id TEXT, person_id TEXT, kind TEXT, criterion_id TEXT,
  system_value TEXT, correct_value TEXT, reason TEXT, actor TEXT, at TEXT, lesson_id TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, nct_id TEXT, run_id TEXT, created_at TEXT, status TEXT, kind TEXT, criterion_id TEXT,
  atom_index INTEGER, value TEXT, rationale TEXT, feedback_ids TEXT, problems TEXT, impact TEXT, decided_by TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS security_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, run_id TEXT, type TEXT, agent TEXT, patient TEXT, outcome TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS chart_reviews (
  run_id TEXT, person_id TEXT, at TEXT, data TEXT, PRIMARY KEY (run_id, person_id)
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        columns = {r[1] for r in self.con.execute("PRAGMA table_info(runs)")}
        if "snapshot" not in columns:  # older databases
            self.con.execute("ALTER TABLE runs ADD COLUMN snapshot TEXT DEFAULT ''")
            self.con.commit()
        if "hash" not in {r[1] for r in self.con.execute("PRAGMA table_info(audit)")}:
            self.con.execute("ALTER TABLE audit ADD COLUMN prev TEXT DEFAULT ''")
            self.con.execute("ALTER TABLE audit ADD COLUMN hash TEXT DEFAULT ''")
            prev = GENESIS  # chain the entries written before the chain existed, in their order
            for r in self.con.execute("SELECT rowid, * FROM audit ORDER BY rowid").fetchall():
                h = entry_hash(prev, r["at"], r["actor"], r["action"], r["detail"])
                self.con.execute("UPDATE audit SET prev = ?, hash = ? WHERE rowid = ?", (prev, h, r["rowid"]))
                prev = h
            self.con.commit()
        self.seq: dict[str, int] = {}

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            cur = self.con.execute(sql, args)
            rows = cur.fetchall()
            self.con.commit()
            return rows

    # --- runs -----------------------------------------------------------------------------------------------

    def create_run(self, kind: str, nct_id: str, actor: str) -> str:
        run_id = uuid.uuid4().hex[:10]
        self._exec("INSERT INTO runs (id, kind, nct_id, status, stage, actor, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   (run_id, kind, nct_id, "queued", "queued", actor, now(), now()))
        return run_id

    def update_run(self, run_id: str, **fields) -> None:
        with self.lock:  # read, merge and write the summary as one step, so parallel updates do not lose keys
            if "summary" in fields:
                current = self.run(run_id)
                fields["summary"] = json.dumps({**(current["summary"] if current else {}), **fields["summary"]})
            sets = ", ".join(f"{k} = ?" for k in fields)
            self._exec(f"UPDATE runs SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now(), run_id))

    def set_snapshot(self, run_id: str, snapshot: dict) -> None:
        self._exec("UPDATE runs SET snapshot = ? WHERE id = ?", (json.dumps(snapshot), run_id))

    def snapshot(self, run_id: str) -> dict:
        rows = self._exec("SELECT snapshot FROM runs WHERE id = ?", (run_id,))
        return json.loads(rows[0]["snapshot"]) if rows and rows[0]["snapshot"] else {}

    def run(self, run_id: str) -> dict | None:
        rows = self._exec("SELECT * FROM runs WHERE id = ?", (run_id,))
        return self._run(rows[0]) if rows else None

    def runs(self, nct_id: str | None = None, limit: int = 50) -> list[dict]:
        if nct_id:
            rows = self._exec("SELECT * FROM runs WHERE nct_id = ? ORDER BY created_at DESC LIMIT ?", (nct_id, limit))
        else:
            rows = self._exec("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._run(r) for r in rows]

    @staticmethod
    def _run(row: sqlite3.Row) -> dict:
        d = dict(row)
        d.pop("snapshot", None)
        d["summary"] = json.loads(d["summary"] or "{}")
        return d

    def event(self, run_id: str, kind: str, agent: str = "", patient: str = "", text: str = "", data: dict | None = None) -> None:
        with self.lock:
            if run_id not in self.seq:
                rows = self.con.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (run_id,)).fetchone()
                self.seq[run_id] = rows[0]
            self.seq[run_id] += 1
            self.con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                             (run_id, self.seq[run_id], now(), kind, agent, patient, text, json.dumps(data or {})))
            if kind == "security":
                self.con.execute("INSERT INTO security_events (at, run_id, type, agent, patient, outcome, detail) "
                                 "VALUES (?,?,?,?,?,?,?)", (now(), run_id, (data or {}).get("type", ""), agent, patient,
                                                            (data or {}).get("outcome", "blocked"), text))
            self.con.commit()

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        rows = self._exec("SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after))
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def llm_call(self, run_id: str, call: dict) -> None:
        self._exec("INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, call["started_at"], call["agent"], call["patient"], call["model"], call["provider"], call["ms"],
                    call["tokens_in"], call["tokens_out"], int(call["ok"]), call["error"]))

    def llm_calls(self, run_id: str | None = None) -> list[dict]:
        rows = self._exec("SELECT * FROM llm_calls WHERE run_id = ?", (run_id,)) if run_id else self._exec("SELECT * FROM llm_calls")
        return [dict(r) for r in rows]

    # --- results ----------------------------------------------------------------------------------------------

    def save_result(self, run_id: str, person_id: str, stage: str, verdict: str, data: dict) -> None:
        self._exec("INSERT OR REPLACE INTO patient_results VALUES (?,?,?,?,?)",
                   (run_id, person_id, stage, verdict, json.dumps(data)))

    def results(self, run_id: str, verdict: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM patient_results WHERE run_id = ?", (run_id,)
        if verdict:
            sql, args = sql + " AND verdict = ?", (run_id, verdict)
        return [dict(r) | {"data": json.loads(r["data"])} for r in self._exec(sql + " ORDER BY person_id", args)]

    def result(self, run_id: str, person_id: str) -> dict | None:
        rows = self._exec("SELECT * FROM patient_results WHERE run_id = ? AND person_id = ?", (run_id, person_id))
        return dict(rows[0]) | {"data": json.loads(rows[0]["data"])} if rows else None

    def save_review(self, run_id: str, person_id: str, resolutions: list[dict]) -> None:
        """One patient's finished chart review, saved at once: a resumed run reuses it and does not ask again."""
        self._exec("INSERT OR REPLACE INTO chart_reviews VALUES (?,?,?,?)", (run_id, person_id, now(), json.dumps(resolutions)))

    def reviews_done(self, run_id: str) -> int:
        return self._exec("SELECT COUNT(*) AS n FROM chart_reviews WHERE run_id = ?", (run_id,))[0]["n"]

    def reviews(self, run_id: str) -> dict[str, list[dict]]:
        return {r["person_id"]: json.loads(r["data"]) for r in self._exec("SELECT person_id, data FROM chart_reviews WHERE run_id = ?", (run_id,))}

    def tool_events(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT run_id, at, agent, text FROM events WHERE kind = 'tool'")]

    def review(self, run_id: str, person_id: str) -> list[dict] | None:
        rows = self._exec("SELECT data FROM chart_reviews WHERE run_id = ? AND person_id = ?", (run_id, person_id))
        return json.loads(rows[0]["data"]) if rows else None

    # --- feedback and lessons ---------------------------------------------------------------------------------

    def add_feedback(self, nct_id: str, run_id: str, person_id: str, kind: str, criterion_id: str, system_value: str,
                     correct_value: str, reason: str, actor: str) -> int:
        with self.lock:
            cur = self.con.execute("INSERT INTO feedback (nct_id, run_id, person_id, kind, criterion_id, system_value, "
                                   "correct_value, reason, actor, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (nct_id, run_id, person_id, kind, criterion_id, system_value, correct_value, reason, actor, now()))
            self.con.commit()
            return cur.lastrowid

    def feedback(self, nct_id: str, open_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM feedback WHERE nct_id = ?" + (" AND lesson_id = ''" if open_only else "") + " ORDER BY id DESC"
        return [dict(r) for r in self._exec(sql, (nct_id,))]

    def mark_feedback(self, ids: list[int], lesson_id: str) -> None:
        for i in ids:
            self._exec("UPDATE feedback SET lesson_id = ? WHERE id = ? AND lesson_id = ''", (lesson_id, i))

    def save_lesson(self, lesson: dict) -> None:
        self._exec("INSERT OR REPLACE INTO lessons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            lesson["id"], lesson["nct_id"], lesson.get("run_id", ""), now(), lesson.get("status", "proposed"), lesson["kind"],
            lesson.get("criterion_id", ""), lesson.get("atom_index"), lesson.get("value", ""), lesson.get("rationale", ""),
            json.dumps(lesson.get("feedback_ids", [])), json.dumps(lesson.get("problems", [])), json.dumps(lesson.get("impact")),
            None, None))

    def lessons(self, nct_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM lessons WHERE nct_id = ? ORDER BY created_at DESC", (nct_id,))
        return [dict(r) | {"feedback_ids": json.loads(r["feedback_ids"]), "problems": json.loads(r["problems"]),
                           "impact": json.loads(r["impact"] or "null")} for r in rows]

    def lesson(self, lesson_id: str) -> dict | None:
        rows = self._exec("SELECT nct_id FROM lessons WHERE id = ?", (lesson_id,))
        return next((x for x in self.lessons(rows[0]["nct_id"]) if x["id"] == lesson_id), None) if rows else None

    def decide_lesson(self, lesson_id: str, status: str, actor: str) -> None:
        self._exec("UPDATE lessons SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?", (status, actor, now(), lesson_id))

    # --- LOINC mappings --------------------------------------------------------------------------------------

    def propose_mapping(self, source_code: str, loinc: str, confidence: str, reason: str, by: str) -> None:
        current = self.mapping(source_code)
        if current and current["status"] == "approved":
            return  # a person decided; a new proposal does not replace it
        self._exec("INSERT OR REPLACE INTO mappings (source_code, loinc, status, confidence, reason, proposed_by, proposed_at) "
                   "VALUES (?,?,?,?,?,?,?)", (source_code, loinc, "proposed", confidence, reason, by, now()))

    def decide_mapping(self, source_code: str, approve: bool, actor: str, loinc: str | None = None) -> None:
        if loinc:
            self._exec("UPDATE mappings SET loinc = ? WHERE source_code = ?", (loinc, source_code))
        self._exec("UPDATE mappings SET status = ?, decided_by = ?, decided_at = ? WHERE source_code = ?",
                   ("approved" if approve else "rejected", actor, now(), source_code))
        self.audit(actor, "mapping." + ("approve" if approve else "reject"), f"{source_code} -> {loinc or ''}")

    def mapping(self, source_code: str) -> dict | None:
        rows = self._exec("SELECT * FROM mappings WHERE source_code = ?", (source_code,))
        return dict(rows[0]) if rows else None

    def mappings(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM mappings ORDER BY source_code")]

    def approved_mappings(self) -> dict[str, str]:
        return {r["source_code"]: r["loinc"] for r in self._exec("SELECT * FROM mappings WHERE status = 'approved' AND loinc != ''")}

    # --- criteria drafts and outreach --------------------------------------------------------------------------

    def save_draft(self, nct_id: str, run_id: str, data: dict, warnings: list[str]) -> str:
        draft_id = uuid.uuid4().hex[:8]
        self._exec("INSERT INTO criteria_drafts VALUES (?,?,?,?,?,?,?)",
                   (draft_id, nct_id, run_id, now(), "draft", json.dumps(data), json.dumps(warnings)))
        return draft_id

    def drafts(self, nct_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM criteria_drafts WHERE nct_id = ? ORDER BY created_at DESC", (nct_id,))
        return [dict(r) | {"data": json.loads(r["data"]), "warnings": json.loads(r["warnings"])} for r in rows]

    def save_outreach(self, run_id: str, person_id: str, subject: str, body: str, to_name: str) -> None:
        self._exec("INSERT OR REPLACE INTO outreach (run_id, person_id, status, subject, body, to_name) VALUES (?,?,?,?,?,?)",
                   (run_id, person_id, "proposed", subject, body, to_name))

    def decide_outreach(self, run_id: str, person_id: str, status: str, actor: str, subject: str | None = None,
                        body: str | None = None) -> None:
        if subject is not None or body is not None:
            self._exec("UPDATE outreach SET subject = COALESCE(?, subject), body = COALESCE(?, body) WHERE run_id = ? AND person_id = ?",
                       (subject, body, run_id, person_id))
        self._exec("UPDATE outreach SET status = ?, decided_by = ?, decided_at = ? WHERE run_id = ? AND person_id = ?",
                   (status, actor, now(), run_id, person_id))

    def outreach(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM outreach WHERE run_id = ? ORDER BY person_id", (run_id,))]

    def audit(self, actor: str, action: str, detail: str) -> None:
        with self.lock:  # read the last hash and insert as one step, so two writers cannot fork the chain
            last = self.con.execute("SELECT hash FROM audit ORDER BY rowid DESC LIMIT 1").fetchone()
            prev, at = (last["hash"] if last else GENESIS), now()
            self.con.execute("INSERT INTO audit (at, actor, action, detail, prev, hash) VALUES (?,?,?,?,?,?)",
                             (at, actor, action, detail, prev, entry_hash(prev, at, actor, action, detail)))
            self.con.commit()

    def audit_log(self, limit: int = 500) -> list[dict]:
        rows = self._exec("SELECT rowid AS seq, at, actor, action, detail, prev, hash FROM audit ORDER BY rowid DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def verify_audit(self) -> dict:
        """Recomputes the chain. An edited, deleted or inserted entry shows as the first entry whose hash fails."""
        prev, n = GENESIS, 0
        for r in self._exec("SELECT rowid, * FROM audit ORDER BY rowid"):
            n += 1
            if r["prev"] != prev or r["hash"] != entry_hash(prev, r["at"], r["actor"], r["action"], r["detail"]):
                return {"ok": False, "entries": n, "broken_at": {"at": r["at"], "action": r["action"], "position": n}}
            prev = r["hash"]
        return {"ok": True, "entries": n, "broken_at": None, "head": prev}

    def security_event(self, type_: str, agent: str, detail: str, outcome: str = "blocked", run_id: str = "",
                       patient: str = "") -> None:
        """A security event outside a run. A run's events go through event(kind="security")."""
        self._exec("INSERT INTO security_events (at, run_id, type, agent, patient, outcome, detail) VALUES (?,?,?,?,?,?,?)",
                   (now(), run_id, type_, agent, patient, outcome, detail))

    def security_events(self, limit: int = 500) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,))]
