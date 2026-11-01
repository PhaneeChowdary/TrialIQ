"""Chat models by task level, with a fallback chain.

Providers: Anthropic, OpenAI, Google, xAI, Groq, and the local Ollama model.
Keys are added on the Models page or in backend/.env. Claude. GPT. Gemini. Grok. Saved encrypted in backend/data/model_settings.json.

Levels:
- protocol: criteria, terminology, LOINC mapping and their reviewers. No patient data: cloud models allowed.
- chart: chart review, evidence reviewer. Patient notes: cloud models only when "cloud models may read notes" is on.
- draft: referral letters. Patient data: same rule as chart.

Each level has a chain of providers, set on the Models page. A failed call goes
to the next link. A rate-limited provider is skipped until the time it gives. The local model is always the last link. Rate limit, quota, timeout, network."""

import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

from ..config import Settings

PROVIDERS: dict[str, dict] = {
    "anthropic": {"name": "Claude", "maker": "Anthropic", "keys_url": "https://console.anthropic.com/settings/keys",
                  "default": "claude-sonnet-5-5"},
    "openai": {"name": "GPT", "maker": "OpenAI", "keys_url": "https://platform.openai.com/api-keys",
               "base_url": "https://api.openai.com/v1", "default": "gpt-5-mini"},
    "gemini": {"name": "Gemini", "maker": "Google", "keys_url": "https://aistudio.google.com/apikey",
               "default": "gemini-3.5-flash"},
    "xai": {"name": "Grok", "maker": "xAI", "keys_url": "https://console.x.ai", "base_url": "https://api.x.ai/v1",
            "default": "grok-4"},
    "groq": {"name": "Groq", "maker": "Groq", "keys_url": "https://console.groq.com/keys",
             "base_url": "https://api.groq.com/openai/v1", "default": "openai/gpt-oss-120b"},
}
OPENAI_STYLE = {"openai", "xai", "groq"}
LEVELS = ("protocol", "chart", "draft")
LEVEL_INFO = {
    "protocol": "criteria · terminology · criteria reviewer · LOINC mapper",
    "chart": "chart agent · evidence reviewer · reflection",
    "draft": "referral letters",
}
PHI_LEVELS = {"chart", "draft"}
AGENT_LEVEL = {
    "criteria": "protocol", "terminology": "protocol", "criteria_reviewer": "protocol", "mapper": "protocol",
    "chart": "chart", "evidence_reviewer": "chart", "single_prompt": "chart", "reflection": "chart",
    "outreach": "draft",
}
DEFAULT_ORDER = ["anthropic", "openai", "gemini", "xai", "groq"]
NOT_CHAT = re.compile(r"embed|tts|audio|image|imagen|vision-preview|whisper|dall-e|transcri|moderation|realtime|guard|"
                      r"search|computer|robotics|davinci|babbage|sora|veo|lyria|live|orpheus|allam", re.I)

# --- Saved settings: keys , model lists, chosen model, chains, PHI switch ------------------------. Encrypted.


class ModelStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "model_settings.json"
        self.lock = threading.Lock()

    def load(self) -> dict:
        from ..crypto import decrypt

        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        data["keys"] = {p: decrypt(k, self.data_dir) for p, k in (data.get("keys") or {}).items() if p in PROVIDERS}
        return data

    def update(self, change) -> dict:
        from ..crypto import encrypt

        with self.lock:
            data = self.load()
            change(data)
            saved = {**data, "keys": {p: encrypt(k, self.data_dir) for p, k in (data.get("keys") or {}).items()}}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, indent=1))
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            return data


def api_key(s: Settings, store: ModelStore, provider: str) -> str:
    saved = store.load().get("keys", {}).get(provider, "")
    return saved or getattr(s, f"{provider}_api_key", "") or ""


def provider_model(s: Settings, store: ModelStore, provider: str) -> str:
    if provider == "ollama":
        return s.local_model
    chosen = store.load().get("selected", {}).get(provider)
    return chosen or getattr(s, f"{provider}_model", "") or PROVIDERS[provider]["default"]


def cloud_phi(s: Settings, store: ModelStore) -> bool:
    saved = store.load().get("cloud_phi")
    return s.cloud_phi if saved is None else bool(saved)


def routes(s: Settings, store: ModelStore) -> dict[str, list[str]]:
    """The saved chain of each level, else all providers with a key in the default order."""
    saved = store.load().get("routes") or {}
    keyed = [p for p in DEFAULT_ORDER if api_key(s, store, p)]
    return {level: [p for p in saved.get(level, keyed) if p in PROVIDERS] for level in LEVELS}


def chain_for(s: Settings, store: ModelStore, level: str) -> list[tuple[str, str, str]]:
    """for each link. Usable links have an empty reason. Provider, model, reason-if-skipped."""
    out = []
    phi_ok = level not in PHI_LEVELS or cloud_phi(s, store)
    for p in routes(s, store)[level]:
        reason = ""
        if not api_key(s, store, p):
            reason = "no key"
        elif not phi_ok:
            reason = "patient data stays local"
        elif paused(p):
            reason = "rate limited"
        out.append((p, provider_model(s, store, p), reason))
    out.append(("ollama", s.local_model, ""))
    return out


# --- Rate-limit pause --------------------------------------------------------------------------------------

_paused: dict[str, float] = {}
_lock = threading.Lock()


def _wait_seconds(text: str) -> float:
    if found := re.search(r"retryDelay'?:\s*'?(\d+(?:\.\d+)?)s", text):
        return float(found.group(1))
    if found := re.search(r"try again in\s+(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", text, re.I):
        m, sec = found.groups()
        return int(m or 0) * 60 + float(sec or 0) or 60
    return 6 * 3600 if "per day" in text.lower() or "PerDay" in text else 60


def note_failure(provider: str, error: BaseException) -> None:
    text = str(error)
    if any(x in text for x in ("429", "RESOURCE_EXHAUSTED", "rate limit", "Rate limit", "quota", "overloaded")):
        with _lock:
            _paused[provider] = time.time() + min(max(_wait_seconds(text), 20), 24 * 3600)


def paused(provider: str) -> bool:
    with _lock:
        return _paused.get(provider, 0) > time.time()


class ModelSkipped(RuntimeError):
    pass


# --- Chat models -------------------------------------------------------------------------------------------

_JSON_CALL = re.compile(r"^\s*(?:```(?:json)?\s*)?(\{.*\})\s*(?:```)?\s*$", re.S)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_TAGGED = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def rescue_tool_calls(message, tool_names: set[str]):
    """Some local models write a tool call as JSON text. Turn it into a real call, for bound tools only."""
    from langchain_core.messages import AIMessage

    if not isinstance(message, AIMessage) or message.tool_calls or not isinstance(message.content, str):
        return message
    found = _JSON_CALL.match(message.content)
    blocks = [found.group(1)] if found else (_TAGGED.findall(message.content) or _FENCED.findall(message.content))
    calls = []
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        calls += data if isinstance(data, list) else [data]
    calls = [c for c in calls if isinstance(c, dict) and c.get("name") in tool_names][:3]
    if not calls:
        return message
    out = []
    for i, call in enumerate(calls):
        args = call.get("arguments", call.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return message
        out.append({"name": call["name"], "args": args if isinstance(args, dict) else {}, "id": f"rescued-{i}", "type": "tool_call"})
    return message.model_copy(update={"content": "", "tool_calls": out})


def _watch(provider: str):
    from langchain_core.callbacks import BaseCallbackHandler

    class Watch(BaseCallbackHandler):
        def on_llm_error(self, error, **kwargs) -> None:
            note_failure(provider, error)
    return Watch()


def ollama_chat(s: Settings, model: str | None = None):
    from langchain_core.runnables import RunnableLambda
    from langchain_ollama import ChatOllama

    class LocalChat(ChatOllama):
        def bind_tools(self, tools, **kwargs):
            names = {getattr(t, "name", "") for t in tools}
            return super().bind_tools(tools, **kwargs) | RunnableLambda(lambda m: rescue_tool_calls(m, names), name="rescue_tool_calls")

    return LocalChat(model=model or s.local_model, base_url=s.ollama_url, temperature=0, reasoning=False, num_ctx=16384,
                     num_predict=3072, client_kwargs={"timeout": 240})


def provider_chat(provider: str, model: str, key: str):
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=key, max_tokens=4096, timeout=90, max_retries=1, callbacks=[_watch(provider)])
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=0, max_retries=1, timeout=90,
                                      callbacks=[_watch(provider)])
    if provider in OPENAI_STYLE:
        from langchain_openai import ChatOpenAI

        # no temperature: some OpenAI reasoning models do not accept it
        return ChatOpenAI(model=model, api_key=key, base_url=PROVIDERS[provider]["base_url"], timeout=90, max_retries=1,
                          callbacks=[_watch(provider)])
    raise ValueError(f"unknown provider {provider}")


def structured_args(provider: str) -> dict:
    # strict JSON schema mode of the OpenAI format refuses some schemas; tool calls accept all of them
    return {"method": "function_calling"} if provider in OPENAI_STYLE else {}


class ChainChat:
    """Tries each usable link in order for each call. Same interface as a chat model."""

    def __init__(self, s: Settings, store: ModelStore, level: str) -> None:
        from ..security import check_egress

        chain = chain_for(s, store, level)
        self.links = [(p, m) for p, m, reason in chain if not reason]
        self.skipped = [(p, reason) for p, _, reason in chain if reason]
        check_egress(level, self.links, cloud_phi(s, store), PHI_LEVELS)  # a second check, so a bug in routing fails closed
        self.models = [ollama_chat(s, m) if p == "ollama" else provider_chat(p, m, api_key(s, store, p)) for p, m in self.links]
        self.label = " > ".join(m for _, m in self.links)

    def _chain(self, make):
        from langchain_core.runnables import RunnableLambda

        runnables = []
        for (provider, _), chat in zip(self.links, self.models):
            r = make(chat, provider)
            if provider != "ollama":
                def check(value, provider=provider):
                    if paused(provider):
                        raise ModelSkipped(f"{provider} paused for a rate limit")
                    return value
                r = RunnableLambda(check, name="skip_if_paused") | r
            runnables.append(r)
        return runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]

    def invoke(self, input, config=None, **kwargs):
        return self._chain(lambda chat, _: chat).invoke(input, config, **kwargs)

    def bind_tools(self, tools, **kwargs):
        return self._chain(lambda chat, _: chat.bind_tools(tools, **kwargs))

    def with_structured_output(self, schema, **kwargs):
        return self._chain(lambda chat, provider: chat.with_structured_output(schema, **{**structured_args(provider), **kwargs}))


class Models:
    """Gives each agent the chain of its level. It reads the saved settings each time, so a change on the Models
    page applies to the next agent call."""

    def __init__(self, s: Settings) -> None:
        self.s = s
        self.store = ModelStore(s.data_dir)

    def for_agent(self, agent: str) -> ChainChat:
        return ChainChat(self.s, self.store, AGENT_LEVEL.get(agent, "chart"))

    def describe(self) -> dict[str, list[str]]:
        return {level: [f"{p}:{m}" for p, m, reason in chain_for(self.s, self.store, level) if not reason] for level in LEVELS}


# --- Provider API calls for the Models page ----------------------------------------------------------------

class ProviderError(RuntimeError):
    pass


def list_models(provider: str, key: str) -> list[str]:
    """The chat models that this key can use. A wrong key gives ProviderError."""
    try:
        if provider == "gemini":
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 200}, timeout=20)
            r.raise_for_status()
            names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"]]
        elif provider == "anthropic":
            r = httpx.get("https://api.anthropic.com/v1/models", params={"limit": 100}, timeout=20,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            names = [m["id"] for m in r.json().get("data", [])]
        elif provider in OPENAI_STYLE:
            r = httpx.get(f"{PROVIDERS[provider]['base_url']}/models", timeout=20, headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            names = [m["id"] for m in r.json().get("data", [])]
        else:
            raise ProviderError(f"unknown provider {provider}")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        raise ProviderError("The provider rejected this key." if code in (400, 401, 403)
                            else f"The provider answered with error {code}.") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("Cannot reach the provider. Check the network.") from exc
    return sorted({n for n in names if not NOT_CHAT.search(n)}, reverse=True)


def check_model(provider: str, model: str, key: str) -> list[dict]:
    """The three kinds of call that the agents make, with a test prompt. No patient data is sent."""
    from langchain_core.tools import tool

    from .schemas import CriteriaReview

    @tool
    def search_notes(query: str) -> str:
        """Search the patient's notes."""
        return "LVEF 27%"

    chat = provider_chat(provider, model, key)
    checks = []

    def run(name: str, call) -> None:
        started = time.perf_counter()
        try:
            detail = call()
            checks.append({"name": name, "ok": True, "detail": detail, "ms": round((time.perf_counter() - started) * 1000)})
        except Exception as exc:
            text = str(exc)
            if "429" in text or "RESOURCE_EXHAUSTED" in text:
                text = "Rate limit or quota reached."
            checks.append({"name": name, "ok": False, "detail": text[:220], "ms": round((time.perf_counter() - started) * 1000)})

    run("Plain reply", lambda: str(chat.invoke("Reply with the single word OK.").text).strip()[:40])

    def tool_call():
        msg = chat.bind_tools([search_notes]).invoke("Find the ejection fraction in the notes. Use the tool.")
        if not msg.tool_calls:
            raise RuntimeError("The model answered without calling the tool.")
        return f"{msg.tool_calls[0]['name']}({json.dumps(msg.tool_calls[0]['args'])})"

    run("Tool call", tool_call)
    run("Structured reply", lambda: "approved=" + str(chat.with_structured_output(CriteriaReview, **structured_args(provider)).invoke(
        "Review: criterion I1 'Age 18-75' was encoded as age between 18 and 75. Is it right?").approved))
    return checks
