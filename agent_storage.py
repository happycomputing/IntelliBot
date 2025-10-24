"""Agent-scoped storage helpers for configuration and data paths."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


class AgentStorage:
    """Centralises access to agent-specific filesystem resources."""

    def __init__(self) -> None:
        self.agent_id = os.getenv("AGENT_ID", "default")
        self.agent_name = os.getenv("AGENT_NAME", self.agent_id)
        self._agents_root = Path("/agents")
        self.data_dir = self._agents_root / self.agent_id / "data"
        self.config_dir = self._agents_root / self.agent_name / "config"

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Unable to prepare agent directories under {self._agents_root}: {exc}"
            ) from exc

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "chat.sqlite"

    @property
    def sqlite_url(self) -> str:
        resolved = self.sqlite_path.resolve()
        return f"sqlite:///{resolved.as_posix()}"

    def migrate_legacy_app_config(self, legacy_path: Path) -> None:
        """Copy an existing config.json into the agent config folder."""

        legacy_path = Path(legacy_path)

        if not legacy_path.exists():
            return

        destination = self.config_dir / "settings.json"
        if destination.exists():
            return

        try:
            shutil.copy2(legacy_path, destination)
            print(
                f"Migrated legacy app configuration from {legacy_path} to {destination}."
            )
        except OSError as exc:
            print(
                f"Warning: Failed to migrate legacy configuration file {legacy_path}: {exc}"
            )

    def load_app_settings(self) -> Dict[str, Any]:
        return self._load_structured_file(self.config_dir / "settings.json") or {}

    def save_app_settings(self, data: Dict[str, Any]) -> None:
        self._write_json(self.config_dir / "settings.json", data)

    def load_pipeline_config(self) -> Dict[str, Any]:
        return self._load_named_config("pipeline")

    def save_pipeline_config(self, data: Dict[str, Any], fmt: str = "yaml") -> Path:
        return self._save_named_config("pipeline", data, fmt)

    def load_domain_config(self) -> Dict[str, Any]:
        return self._load_named_config("domain")

    def save_domain_config(self, data: Dict[str, Any], fmt: str = "yaml") -> Path:
        return self._save_named_config("domain", data, fmt)

    def load_training_data(self) -> Dict[str, Any]:
        return self._load_named_config("training_data")

    def save_training_data(self, data: Dict[str, Any], fmt: str = "yaml") -> Path:
        return self._save_named_config("training_data", data, fmt)

    def _load_named_config(self, stem: str) -> Dict[str, Any]:
        for candidate in self._candidate_files(stem):
            payload = self._load_structured_file(candidate)
            if payload is not None:
                return payload
        return {}

    def _save_named_config(self, stem: str, data: Dict[str, Any], fmt: str) -> Path:
        fmt_normalized = fmt.lower()
        if fmt_normalized not in {"yaml", "json"}:
            raise ValueError("fmt must be either 'yaml' or 'json'")

        path = self.config_dir / f"{stem}.{'yml' if fmt_normalized == 'yaml' else 'json'}"
        if fmt_normalized == "yaml":
            self._write_yaml(path, data)
        else:
            self._write_json(path, data)
        return path

    def _candidate_files(self, stem: str) -> Iterable[Path]:
        return (
            self.config_dir / f"{stem}.yml",
            self.config_dir / f"{stem}.yaml",
            self.config_dir / f"{stem}.json",
        )

    def _load_structured_file(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Warning: Failed to read {path}: {exc}")
            return None

        if not text.strip():
            return {}

        try:
            if path.suffix in {".yml", ".yaml"}:
                return yaml.safe_load(text) or {}
            if path.suffix == ".json":
                return json.loads(text)
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            print(f"Warning: Failed to parse {path}: {exc}")
            return None

        print(f"Warning: Unsupported file format for {path}")
        return None

    def _write_yaml(self, path: Path, data: Dict[str, Any]) -> None:
        try:
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        except OSError as exc:
            print(f"Warning: Failed to write YAML file {path}: {exc}")

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        try:
            path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            print(f"Warning: Failed to write JSON file {path}: {exc}")
