"""Model keys are stored encrypted, and patient-data levels use cloud models only with the PHI switch on."""

import json

from backend.app.agent import llm
from backend.app.config import Settings


def test_keys_encrypted_and_phi_routing(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIALIQ_SECRET_KEY", raising=False)
    s = Settings(data_dir=tmp_path, groq_api_key="", gemini_api_key="")
    store = llm.ModelStore(tmp_path)
    store.update(lambda d: d.setdefault("keys", {}).update({"anthropic": "sk-ant-test-1234", "groq": "gsk-test-5678"}))

    raw = json.loads((tmp_path / "model_settings.json").read_text())
    assert all(v.startswith("enc:v1:") for v in raw["keys"].values())
    assert "sk-ant-test-1234" not in (tmp_path / "model_settings.json").read_text()
    assert llm.api_key(s, store, "anthropic") == "sk-ant-test-1234"

    protocol = llm.chain_for(s, store, "protocol")
    assert [p for p, _, r in protocol if not r] == ["anthropic", "groq", "ollama"]
    chart = llm.chain_for(s, store, "chart")
    assert [p for p, _, r in chart if not r] == ["ollama"]  # notes stay local by default
    assert {r for p, _, r in chart if p != "ollama"} == {"patient data stays local"}

    store.update(lambda d: d.__setitem__("cloud_phi", True))
    store.update(lambda d: d.__setitem__("routes", {"protocol": ["groq"], "chart": ["anthropic"], "draft": []}))
    assert [p for p, _, r in llm.chain_for(s, store, "chart") if not r] == ["anthropic", "ollama"]
    assert [p for p, _, r in llm.chain_for(s, store, "protocol") if not r] == ["groq", "ollama"]
    assert [p for p, _, r in llm.chain_for(s, store, "draft") if not r] == ["ollama"]
