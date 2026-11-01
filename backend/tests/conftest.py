import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend" / "data"


class FakeRunnable:
    def __init__(self, fn):
        self.fn = fn

    def invoke(self, messages, config=None, **kwargs):
        return self.fn(messages)


class FakeChat:
    """A chat model that answers from scripts. `answers` is a list of callables or objects; each structured call
    takes the next one for its schema name."""

    def __init__(self, scripts: dict[str, list]) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[str] = []

    def bind_tools(self, tools, **kwargs):
        return FakeRunnable(lambda m: AIMessage(content="DONE"))

    def with_structured_output(self, schema, **kwargs):
        def answer(messages):
            self.calls.append(schema.__name__)
            queue = self.scripts.get(schema.__name__, [])
            item = queue.pop(0) if queue else None
            item = item(messages) if callable(item) else item
            return schema.model_validate(item) if isinstance(item, dict) else item
        return FakeRunnable(answer)

    def invoke(self, messages, config=None, **kwargs):
        return AIMessage(content="ok")


class FakeModels:
    def __init__(self, chat: FakeChat) -> None:
        self.chat = chat

    def for_agent(self, agent: str):
        return self.chat

    def describe(self):
        return {"protocol": ["fake"], "chart": ["fake"], "draft": ["fake"]}


@pytest.fixture(scope="session")
def ehr():
    from backend.app.ehr.store import EHR

    if not (DATA / "ehr.db").exists():
        from backend.app.ehr.generate import main
        main()
    return EHR(DATA / "ehr.db")


@pytest.fixture(scope="session")
def gold():
    return json.loads((DATA / "gold.json").read_text())
