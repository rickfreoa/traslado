# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Instalação legada do Freoa Markdown Converter no Windows: atalho removido e marcador de substituição."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys

from .. import __version__
from .. import platform as plat
from ._common import _utc_now

def handle_legacy_windows(
    new_root: Path,
    *,
    version: str = __version__,
    platform_name: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    desktop_dir: Path | None = None,
    start_menu_dir: Path | None = None,
    applications_dir: Path | None = None,
    icons_dir: Path | None = None,
    now: datetime | str | None = None,
) -> list[dict]:
    platform_name = platform_name or sys.platform
    env = os.environ if env is None else env
    if platform_name != "win32":
        return []
    if env.get("TRASLADO_LEGACY_ROOT"):
        candidates = [Path(env["TRASLADO_LEGACY_ROOT"])]
    else:
        candidates = [Path(new_root).parent / "Freoa/MarkdownConverter"]

    superseded: list[dict] = []
    seen: set[Path] = set()
    for leg_root in candidates:
        try:
            resolved = leg_root.resolve()
        except OSError:
            continue
        if resolved in seen or not leg_root.is_dir():
            continue
        seen.add(resolved)
        current = plat.read_current(leg_root)
        if not isinstance(current, dict) or current.get("product") != "freoa-md-converter":
            continue
        removed_shortcuts = plat.remove_shortcuts(
            ["Freoa Markdown Converter"],
            platform=platform_name,
            home=home,
            desktop_dir=desktop_dir,
            start_menu_dir=start_menu_dir,
            applications_dir=applications_dir,
            icons_dir=icons_dir,
        )
        timestamp = _utc_now(now)
        marker = {
            "schema": "traslado-superseded-1",
            "product": "traslado",
            "version": version,
            "date": timestamp,
            "superseded_at": timestamp,
            "new_root": str(Path(new_root).resolve()),
            "legacy_release_id": current.get("release_id"),
            "removed_shortcuts": [str(p) for p in removed_shortcuts],
        }
        plat._atomic(leg_root / "superseded-by-traslado.json", marker)
        superseded.append({"root": str(leg_root), "marker": marker})
    return superseded
