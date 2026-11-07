# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Perfil por teste e verificacao dos destinos antes de escrever/remover atalhos."""
import os
from pathlib import Path
from unittest.mock import patch

from traslado import platform


def profile_environment(base):
    base = Path(base).resolve()
    return {"HOME": str(base), "USERPROFILE": str(base),
            "APPDATA": str(base / "AppData/Roaming"),
            "LOCALAPPDATA": str(base / "AppData/Local")}


def guard_shortcut_locations(base):
    base = Path(base).resolve()
    original = platform._locations

    def checked(*args, **kwargs):
        destinations = original(*args, **kwargs)
        for destination in destinations:
            if not destination.resolve().is_relative_to(base):
                raise AssertionError(f"Atalho fora do temporario do teste: {destination}")
        return destinations

    return patch.object(platform, "_locations", side_effect=checked)


def isolate_profile(test, base):
    for protection in (patch.dict(os.environ, profile_environment(base)),
                       guard_shortcut_locations(base)):
        protection.start()
        test.addCleanup(protection.stop)
