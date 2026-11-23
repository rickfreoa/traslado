# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Constantes, erro de atalho e normalização de parâmetros do ciclo de vida."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re

DEFAULT_LANGUAGES = ("por", "eng", "spa", "ita", "fra", "deu")
REPOSITORY_URL = "https://github.com/rickfreoa/traslado"
POWERSHELL_WIN = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


class ShortcutError(RuntimeError):
    """Promotion succeeded, desktop integration needs retry (exit 4)."""


def validate_version(version: str) -> str:
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?", version):
        raise ValueError("Versao invalida")
    return version


def _utc_now(now: datetime | str | None = None) -> str:
    if isinstance(now, str):
        return now
    instant = now or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_languages(languages: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if languages is None:
        return list(DEFAULT_LANGUAGES)
    if isinstance(languages, str):
        items = [part.strip().lower() for part in re.split(r"[,+]", languages) if part.strip()]
    else:
        items = [part.strip().lower() for item in languages for part in re.split(r"[,+]", str(item)) if part.strip()]
    if not items or len(set(items)) != len(items) or not set(items) <= set(DEFAULT_LANGUAGES):
        raise ValueError("Idiomas inválidos; use por, eng, spa, ita, fra, deu sem duplicatas")
    return items


def normalize_extras(extras: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if not extras:
        return []
    if isinstance(extras, str):
        items = [part.strip().lower() for part in re.split(r"[,+]", extras) if part.strip()]
    else:
        items = [part.strip().lower() for item in extras for part in re.split(r"[,+]", str(item)) if part.strip()]
    allowed = {"legacy"}
    if not set(items) <= allowed:
        raise ValueError(f"Extras inválidos: {items}; permitido: legacy")
    return sorted(set(items))


def compute_release_id(version: str, profile: str, extras: list[str], languages: list[str],
                       seed: str = "") -> str:
    payload = f"{version}|{profile}|{','.join(sorted(extras))}|{','.join(languages)}|{seed}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{version}_{digest}"


def _shortcut_dirs_from_env(
    env: dict[str, str],
    desktop_dir: Path | None = None,
    start_menu_dir: Path | None = None,
    applications_dir: Path | None = None,
    icons_dir: Path | None = None,
) -> dict[str, Path | None]:
    return {
        "desktop_dir": Path(desktop_dir or env["TRASLADO_SHORTCUT_DESKTOP_DIR"])
        if (desktop_dir or env.get("TRASLADO_SHORTCUT_DESKTOP_DIR")) else None,
        "start_menu_dir": Path(start_menu_dir or env["TRASLADO_SHORTCUT_START_MENU_DIR"])
        if (start_menu_dir or env.get("TRASLADO_SHORTCUT_START_MENU_DIR")) else None,
        "applications_dir": Path(applications_dir or env["TRASLADO_SHORTCUT_APPLICATIONS_DIR"])
        if (applications_dir or env.get("TRASLADO_SHORTCUT_APPLICATIONS_DIR")) else None,
        "icons_dir": Path(icons_dir or env["TRASLADO_SHORTCUT_ICONS_DIR"])
        if (icons_dir or env.get("TRASLADO_SHORTCUT_ICONS_DIR")) else None,
    }
