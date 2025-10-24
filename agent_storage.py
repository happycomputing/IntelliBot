"""Agent-scoped storage helpers for configuration and data paths."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


class AgentStorage:
    """Centralises access to agent-specific filesystem resources."""

    def __init__(self) -> None:
        default_agent_id = os.getenv("AGENT_ID", "default")
        default_agent_name = os.getenv("AGENT_NAME")

        self._agents_root = Path("/agents")
        self._agents_root.mkdir(parents=True, exist_ok=True)

        metadata = self.switch_agent(default_agent_id, default_agent_name)
        self.agent_id = metadata["id"]
        self.agent_name = metadata["name"]

    def list_agents(self) -> List[Dict[str, str]]:
        """Return available agent descriptors."""

        agents: List[Dict[str, str]] = []
        if not self._agents_root.exists():
            return agents

        for agent_dir in sorted(self._agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue

            data_dir = agent_dir / "data"
            config_dir = agent_dir / "config"
            if not data_dir.exists() and not config_dir.exists():
                continue

            metadata = self._load_structured_file(config_dir / "profile.json") or {}
            agent_id = agent_dir.name
            agents.append(
                {
                    "id": agent_id,
                    "name": metadata.get("name") or agent_id,
                }
            )

        return agents

    def create_agent(self, identifier: str, display_name: Optional[str] = None) -> Dict[str, str]:
        """Create a new agent directory structure."""

        normalised_id = self._normalise_identifier(identifier)
        if not normalised_id:
            raise ValueError("Agent identifier must contain letters or numbers")

        agent_dir = self._agents_root / normalised_id
        if agent_dir.exists():
            raise FileExistsError(f"Agent '{normalised_id}' already exists")

        data_dir = agent_dir / "data"
        config_dir = agent_dir / "config"
        data_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)

        fallback_name = display_name.strip() if display_name else (identifier.strip() if identifier else "")
        metadata = {
            "id": normalised_id,
            "name": fallback_name or normalised_id,
        }
        self._write_json(config_dir / "profile.json", metadata)
        return metadata

    def switch_agent(self, agent_id: str, display_name: Optional[str] = None) -> Dict[str, str]:
        """Switch internal pointers to a different agent."""

        normalised_id = self._normalise_identifier(agent_id)
        if not normalised_id:
            raise ValueError("Agent identifier must contain letters or numbers")

        agent_dir = self._agents_root / normalised_id
        if not agent_dir.exists():
            legacy_dir = self._find_agent_dir_by_id(normalised_id)
            if legacy_dir is not None:
                agent_dir = legacy_dir
        data_dir = agent_dir / "data"
        config_dir = agent_dir / "config"

        data_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)

        profile_path = config_dir / "profile.json"
        metadata = self._load_structured_file(profile_path) or {}
        needs_write = False

        if "id" not in metadata:
            metadata["id"] = normalised_id
            needs_write = True
        if "name" not in metadata:
            metadata["name"] = display_name.strip() if display_name else normalised_id
            needs_write = True

        if display_name and metadata.get("name") != display_name.strip():
            metadata["name"] = display_name.strip()
            metadata["id"] = normalised_id
            needs_write = True

        if needs_write or not profile_path.exists():
            self._write_json(profile_path, metadata)

        self.agent_id = metadata.get("id", normalised_id)
        self.agent_name = metadata.get("name", normalised_id)
        self.data_dir = data_dir
        self.config_dir = config_dir
        return {"id": self.agent_id, "name": self.agent_name}

    def current_agent(self) -> Dict[str, str]:
        return {"id": self.agent_id, "name": self.agent_name}

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

    def _find_agent_dir_by_id(self, agent_id: str) -> Optional[Path]:
        if not self._agents_root.exists():
            return None

        for candidate in self._agents_root.iterdir():
            if not candidate.is_dir():
                continue

            profile_path = candidate / "config" / "profile.json"
            metadata = self._load_structured_file(profile_path)
            if metadata and metadata.get("id") == agent_id:
                return candidate

        return None

    def _normalise_identifier(self, identifier: str) -> str:
        value = (identifier or "").strip().lower()
        value = value.replace(" ", "-")
        cleaned = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
        return cleaned.strip("-_")

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
