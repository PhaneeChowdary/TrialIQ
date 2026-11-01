"""Model call telemetry. Each model call has the metadata {"agent": name}; this callback records the agent, the
model that answered, the tokens, the time and the error of each call."""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


class UsageRecorder(BaseCallbackHandler):
    def __init__(self, sink: Callable[[dict], None]) -> None:
        self.sink = sink
        self._open: dict[UUID, tuple] = {}
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, metadata=None, **kwargs) -> None:
        meta = metadata or {}
        with self._lock:
            self._open[run_id] = (meta.get("agent", "other"), meta.get("patient", ""), meta.get("ls_model_name", ""),
                                  meta.get("ls_provider", ""), datetime.now(UTC).isoformat(), time.perf_counter())

    def on_llm_end(self, response, *, run_id: UUID, **kwargs) -> None:
        usage = {}
        if response.generations and response.generations[0]:
            usage = getattr(getattr(response.generations[0][0], "message", None), "usage_metadata", None) or {}
        self._close(run_id, True, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    def on_llm_error(self, error, *, run_id: UUID, **kwargs) -> None:
        text = str(error)
        short = "rate limit or quota" if "429" in text or "RESOURCE_EXHAUSTED" in text else text.split("\n")[0][:160]
        self._close(run_id, False, error=short)

    def _close(self, run_id: UUID, ok: bool, tokens_in: int = 0, tokens_out: int = 0, error: str = "") -> None:
        with self._lock:
            opened = self._open.pop(run_id, None)
        if opened is None:
            return
        agent, patient, model, provider, started_at, started = opened
        self.sink({"agent": agent, "patient": patient, "model": model, "provider": provider, "started_at": started_at,
                   "ms": round((time.perf_counter() - started) * 1000), "tokens_in": tokens_in, "tokens_out": tokens_out,
                   "ok": ok, "error": error})


def tagged(agent: str, patient: str = "", callbacks: list | None = None) -> dict:
    return {"metadata": {"agent": agent, "patient": patient}, "callbacks": callbacks or []}
