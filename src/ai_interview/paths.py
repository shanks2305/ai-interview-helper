from __future__ import annotations

import os
from pathlib import Path


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def project_root() -> Path:
    explicit = os.getenv("AI_INTERVIEW_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    pkg = package_dir()
    repo = pkg.parent.parent
    if (repo / "pyproject.toml").is_file() or (repo / "web" / "index.html").is_file():
        return repo
    return Path.cwd()


def env_file() -> Path:
    return project_root() / ".env"


def web_dir() -> Path:
    bundled = package_dir() / "web"
    if (bundled / "index.html").is_file():
        return bundled
    return project_root() / "web"


def renderer_dir() -> Path:
    bundled = package_dir() / "desktop_renderer"
    if (bundled / "index.html").is_file():
        return bundled
    return project_root() / "desktop" / "renderer"


def desktop_dir() -> Path:
    return project_root() / "desktop"


def data_dir() -> Path:
    explicit = os.getenv("AI_INTERVIEW_DATA", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return project_root() / "data"
