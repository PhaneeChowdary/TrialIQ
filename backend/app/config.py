"""Settings from environment variables. All have defaults: the app runs local-only with Ollama. Backend/.env."""

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(f"TRIALIQ_{name}", default).strip()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "backend" / "data"))).resolve())
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    local_model: str = field(default_factory=lambda: _env("LOCAL_MODEL", "qwen3:8b"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "openai/gpt-oss-120b"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.5-flash"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL"))
    xai_api_key: str = field(default_factory=lambda: _env("XAI_API_KEY"))
    xai_model: str = field(default_factory=lambda: _env("XAI_MODEL"))
    # Patient notes go only to the local model unless this is "on". The demo data is synthetic, so a cloud model
    # can be allowed for speed; with real data, this needs a BAA and an approval of the privacy office.
    cloud_phi: bool = field(default_factory=lambda: _env("CLOUD_PHI", "off") == "on")
    screen_date: date = field(default_factory=lambda: date.fromisoformat(_env("SCREEN_DATE", "2026-10-01")))
    concurrency: int = field(default_factory=lambda: int(_env("CONCURRENCY", "1")))
    chart_rounds: int = field(default_factory=lambda: int(_env("CHART_ROUNDS", "4")))
    run_time_limit: int = field(default_factory=lambda: int(_env("RUN_TIME_LIMIT_SECONDS", "3600")))
    dry_run: bool = field(default_factory=lambda: _env("DRY_RUN", "true") != "false")

    @property
    def ehr_path(self) -> Path:
        return self.data_dir / "ehr.db"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "trialiq.db"

    @property
    def checkpoints(self) -> Path:
        return self.data_dir / "checkpoints.db"


settings = Settings()
