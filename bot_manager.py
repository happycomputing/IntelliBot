"""Utility helpers for managing local Rasa bot projects."""

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BOTS_DIR = os.path.join(BASE_DIR, 'bots_store')
BOTS_DIR = os.environ.get('INTELLIBOT_BOTS_DIR', DEFAULT_BOTS_DIR)

if not os.path.isabs(BOTS_DIR):
    BOTS_DIR = os.path.join(BASE_DIR, BOTS_DIR)

BOTS_DIR = os.path.abspath(BOTS_DIR)
RASA_VENV_PATH = os.path.join(BASE_DIR, '.venv-rasa')
RASA_BIN = os.path.join(RASA_VENV_PATH, 'bin', 'rasa')
RASA_PYTHON = os.path.join(RASA_VENV_PATH, 'bin', 'python')

try:
    os.makedirs(BOTS_DIR, exist_ok=True)
except OSError as exc:
    raise RuntimeError(f"Unable to prepare bots directory '{BOTS_DIR}': {exc}") from exc


def rasa_available() -> bool:
    """Return True when the dedicated Rasa virtualenv is present."""
    python_ok = os.path.exists(RASA_PYTHON) and os.access(RASA_PYTHON, os.X_OK)
    bin_ok = os.path.exists(RASA_BIN) and os.access(RASA_BIN, os.X_OK)
    return python_ok and bin_ok


def slugify_name(value: str, default: str = 'bot') -> str:
    """Convert a display name into a filesystem-safe slug."""
    value = (value or '').strip()
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', value).strip('-').lower()
    return slug or default


def unique_slug(base_slug: str, exists: Callable[[str], bool]) -> str:
    """Ensure the slug is unique by appending a numeric suffix when required."""
    slug = base_slug
    suffix = 2
    while exists(slug):
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def project_path_for(slug: str) -> str:
    """Return the absolute path for a bot project given its slug."""
    return os.path.join(BOTS_DIR, slug)


def ensure_absolute_project_path(project_path: str) -> str:
    """Convert a stored project path into an absolute filesystem path."""
    if not project_path:
        return ''
    if os.path.isabs(project_path):
        return project_path
    return os.path.abspath(os.path.join(BASE_DIR, project_path))


def to_relative_project_path(project_path: str) -> str:
    """Store project paths relative to the repo root for portability."""
    if not project_path:
        return ''
    return os.path.relpath(project_path, BASE_DIR)


def latest_model_path(project_path: str) -> Optional[str]:
    """Return the most recently trained Rasa model for the project."""
    abs_path = ensure_absolute_project_path(project_path)
    models_dir = Path(abs_path) / 'models'
    if not models_dir.exists():
        return None
    candidates = sorted(models_dir.glob('*.tar.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return str(candidates[0])
    return None


def init_rasa_project(project_path: str) -> None:
    """Initialise a new Rasa project inside ``project_path``."""
    if not rasa_available():
        raise RuntimeError(f'Rasa environment not available at {RASA_BIN}')

    project_path = ensure_absolute_project_path(project_path)
    os.makedirs(project_path, exist_ok=True)
    env = os.environ.copy()
    env.setdefault('RASA_TELEMETRY_ENABLED', 'false')

    result = subprocess.run(
        [RASA_PYTHON, '-m', 'rasa', 'init', '--no-prompt'],
        cwd=project_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or 'Failed to initialise Rasa project')


def train_rasa_project(project_path: str) -> tuple[bool, Optional[str]]:
    """Run ``rasa train`` inside ``project_path``."""
    if not rasa_available():
        return False, 'Rasa environment not available'

    abs_path = ensure_absolute_project_path(project_path)

    env = os.environ.copy()
    env.setdefault('RASA_TELEMETRY_ENABLED', 'false')

    result = subprocess.run(
        [RASA_PYTHON, '-m', 'rasa', 'train', '--quiet'],
        cwd=abs_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, None
    return False, result.stderr or result.stdout or 'Training failed'
