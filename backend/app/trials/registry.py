"""Trials known to the app: the library in the code and trials imported at run time. With approved criteria."""

import json
from pathlib import Path

from ..criteria import LIBRARY, CriteriaSet
from . import ctgov


class Registry:
    def __init__(self, data_dir: Path) -> None:
        self.trials_dir = Path(data_dir) / "trials"
        self.criteria_dir = Path(data_dir) / "criteria"
        self.trials_dir.mkdir(parents=True, exist_ok=True)
        self.criteria_dir.mkdir(parents=True, exist_ok=True)

    def _trial_files(self) -> dict[str, Path]:
        files = {p.stem: p for p in LIBRARY.glob("NCT*.json") if not p.name.endswith(".criteria.json")}
        files.update({p.stem: p for p in self.trials_dir.glob("NCT*.json")})
        return files

    def list(self) -> list[dict]:
        out = []
        for nct, path in sorted(self._trial_files().items()):
            t = json.loads(path.read_text())
            out.append({k: t.get(k) for k in ("nct_id", "title", "status", "phases", "conditions", "sponsor", "min_age",
                                              "max_age", "indiana_sites", "last_update", "fetched")}
                       | {"criteria": self.criteria_source(nct)})
        return out

    def get(self, nct_id: str) -> dict | None:
        path = self._trial_files().get(nct_id)
        return json.loads(path.read_text()) if path else None

    def add(self, nct_id: str) -> dict:
        trial = ctgov.fetch(nct_id)
        (self.trials_dir / f"{trial['nct_id']}.json").write_text(json.dumps(trial, indent=1))
        return trial

    def criteria_source(self, nct_id: str) -> str | None:
        if (self.criteria_dir / f"{nct_id}.json").exists():
            return "approved"
        if (LIBRARY / f"{nct_id}.criteria.json").exists():
            return "approved (library)"
        return None

    def criteria(self, nct_id: str) -> CriteriaSet | None:
        for path in (self.criteria_dir / f"{nct_id}.json", LIBRARY / f"{nct_id}.criteria.json"):
            if path.exists():
                return CriteriaSet.model_validate(json.loads(path.read_text()))
        return None

    def approve(self, cset: CriteriaSet) -> None:
        """Save a new approved version. The version before it stays in criteria/history for the audit trail."""
        current = self.criteria(cset.nct_id)
        if current is not None:
            history = self.criteria_dir / "history"
            history.mkdir(exist_ok=True)
            (history / f"{cset.nct_id}.v{current.version}.json").write_text(current.model_dump_json(indent=1, exclude_defaults=True))
            cset.version = max(cset.version, current.version + 1)
        (self.criteria_dir / f"{cset.nct_id}.json").write_text(cset.model_dump_json(indent=1, exclude_defaults=True))
