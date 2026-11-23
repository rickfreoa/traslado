# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Releases instaladas: uso por processo, remoção e poda sob o lock de promoção, integridade e release ativa."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .. import platform as plat
from ..pathguard import unsafe_link
from ._common import normalize_extras, normalize_languages

def release_in_use(release: Path) -> bool:
    release = release.resolve()
    if Path(sys.executable).resolve().is_relative_to(release) or Path(sys.prefix).resolve().is_relative_to(release):
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        pids = (wintypes.DWORD * 65536)()
        used = wintypes.DWORD()
        if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
            return True
        for pid in pids[:used.value // ctypes.sizeof(wintypes.DWORD)]:
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                continue
            try:
                size = wintypes.DWORD(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) and Path(buf.value).resolve().is_relative_to(release):
                    return True
            finally:
                kernel.CloseHandle(handle)
    elif Path("/proc").is_dir():
        for exe in Path("/proc").glob("[0-9]*/exe"):
            try:
                if exe.resolve(strict=True).is_relative_to(release):
                    return True
            except OSError:
                continue
    else:
        # macOS process executable inventory; failure keeps releases conservatively.
        try:
            proc = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, check=True)
            if any(Path(line.strip()).is_absolute() and Path(line.strip()).resolve().is_relative_to(release) for line in proc.stdout.splitlines()):
                return True
        except (OSError, subprocess.SubprocessError):
            return True
    return False


def remove_unreferenced_release(root: Path, release: Path, *, candidate_reprovada: bool = False) -> bool:
    if unsafe_link(release):
        return False
    root, release = Path(root).resolve(), Path(release).resolve()
    if release.parent != root / "releases":
        raise ValueError("Caminho de remocao de release recusado")
    # A poda decide e remove sob o lock de promoção: uma promoção concorrente não troca current/candidate
    # entre a leitura dos manifestos e a remoção. Lock ocupado: adia pelo marcador de remoção pendente.
    try:
        with plat._promotion_lock(root):
            return _remove_unreferenced_release_locked(root, release, candidate_reprovada)
    except ValueError as exc:
        if "lock" not in str(exc).lower():
            raise
        plat._atomic(root / "state/pending-removals" / (release.name + ".json"), {"release_id": release.name})
        return False


def _managed_python_exe(managed: Path) -> str | None:
    current = plat.read_current(managed) or {}
    python = current.get("python")
    return str(python) if python and Path(python).is_file() else None


def _remove_unreferenced_release_locked(root: Path, release: Path, candidate_reprovada: bool = False) -> bool:
    current = plat.read_current(root)
    if (root / "current.json").exists() and current is None:
        return False
    current = current or {}
    candidate = plat.read_candidate(root) or {}
    # A candidata em instalação (de outro processo) é protegida; só o próprio setup remove a sua reprovada.
    protegidas = {current.get("release_id"), current.get("previous_release_id")}
    gates = candidate.get("gates") if isinstance(candidate.get("gates"), dict) else {}
    reprovada = (candidate_reprovada or gates.get("doctor") is False or gates.get("fixture") is False
                 or gates.get("revocation") == "blocked")
    if not reprovada:
        protegidas.add(candidate.get("release_id"))
    if release.name in protegidas:
        return False
    for manifest in (current, candidate):
        if manifest.get("data_root") and Path(manifest["data_root"]).resolve().is_relative_to(release):
            return False
    marker = root / "state/pending-removals" / (release.name + ".json")
    if release_in_use(release):
        plat._atomic(marker, {"release_id": release.name})
        return False
    try:
        if release.exists():
            shutil.rmtree(release)
    except OSError:
        plat._atomic(marker, {"release_id": release.name})
        return False
    marker.unlink(missing_ok=True)
    return True


def prune_releases(root: Path, *, pending_only: bool = False) -> None:
    releases = Path(root) / "releases"
    if not releases.is_dir():
        return
    for release in releases.iterdir():
        if release.is_dir() and (not pending_only or (Path(root) / "state/pending-removals" / (release.name + ".json")).is_file()):
            remove_unreferenced_release(root, release)


def release_integrity(release_dir: Path) -> dict[str, str]:
    return {p.relative_to(release_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(release_dir.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.name != "release-integrity.json"}


def active_release_matches(root: Path, version: str, profile: str, extras=None, languages=None,
                           *, require_online: bool = False) -> bool:
    current = plat.read_current(root)
    if not current or (current.get("version"), current.get("profile"), current.get("extras"), current.get("languages")) != (
            version, profile, normalize_extras(extras), normalize_languages(languages)):
        return False
    gates = current.get("gates")
    if require_online and (not isinstance(gates, dict) or gates.get("revocation") != "ok"):
        return False
    try:
        release = plat._release(root, current["release_id"])
        inventory = json.loads((release / "release-integrity.json").read_text(encoding="utf-8"))
        return bool(inventory) and plat.release_python(root, current["release_id"]).is_file() and inventory == release_integrity(release)
    except (OSError, ValueError, KeyError):
        return False
