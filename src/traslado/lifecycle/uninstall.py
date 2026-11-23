# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Desinstalação gerenciada, remoção diferida no Windows e descarte de desinstalação pendente."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

from .. import platform as plat
from ..pathguard import unsafe_link
from . import releases
from ._common import POWERSHELL_WIN, _shortcut_dirs_from_env

def _defer_self_removal(managed: Path, paths: list[Path], preserved: list[Path]) -> None:
    """Windows não apaga o interpretador em execução: um processo destacado espera este PID terminar e remove
    os caminhos adiados e o marcador. Defesa em profundidade no processo destacado: cada alvo revalidado como
    interno à raiz, nunca reparse point, nunca contendo dado preservado; remoção por "rmdir /s /q" (não
    atravessa junctions) e poda só de state, releases e da raiz, não recursiva e apenas se vazios."""
    root = managed.resolve()
    alvos = [Path(p).resolve() for p in paths]
    keep = [Path(p).resolve() for p in preserved]
    if unsafe_link(root) or not alvos or any(
            not a.is_relative_to(root) or a == root or unsafe_link(a) or any(k.is_relative_to(a) for k in keep)
            for a in alvos):
        raise ValueError("Remoção diferida exige caminhos internos à raiz, sem links e sem dados preservados")
    code = (
        "$ErrorActionPreference='Continue'; "
        "$root=[IO.Path]::GetFullPath($env:TRASLADO_REMOVE_ROOT).TrimEnd('\\'); "
        "if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\\')) { exit 2 }; "
        "$keep=@($env:TRASLADO_REMOVE_KEEP -split [char]10 | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\\') }); "
        "Wait-Process -Id ([int]$env:TRASLADO_REMOVE_PID) -ErrorAction SilentlyContinue; "
        "$alvos=@($env:TRASLADO_REMOVE_PATHS -split [char]10 | Where-Object { $_ }); "
        "for ($attempt=0; $attempt -lt 30; $attempt++) { $pendente=$false; "
        "foreach ($a in $alvos) { $full=[IO.Path]::GetFullPath($a).TrimEnd('\\'); "
        "if (-not $full.StartsWith($root + '\\', [StringComparison]::OrdinalIgnoreCase)) { continue }; "
        "if (@($keep | Where-Object { $_.StartsWith($full + '\\', [StringComparison]::OrdinalIgnoreCase) -or $_ -ieq $full }).Count) { continue }; "
        "if (-not (Test-Path -LiteralPath $full)) { continue }; "
        "if ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }; "
        "& $env:ComSpec /d /c rmdir /s /q \"$full\"; if (Test-Path -LiteralPath $full) { $pendente=$true } }; "
        "if (-not $pendente) { break }; Start-Sleep -Milliseconds 500 }; "
        "Remove-Item -LiteralPath (Join-Path $root 'state\\pending-uninstall.json') -Force -ErrorAction SilentlyContinue; "
        "foreach ($d in @((Join-Path $root 'state'), (Join-Path $root 'releases'), $root)) { "
        "try { [IO.Directory]::Delete($d, $false) } catch { } }; exit 0"
    )
    child_env = {**os.environ, "TRASLADO_REMOVE_ROOT": str(root), "TRASLADO_REMOVE_PID": str(os.getpid()),
                 "TRASLADO_REMOVE_PATHS": "\n".join(str(a) for a in alvos),
                 "TRASLADO_REMOVE_KEEP": "\n".join(str(k) for k in keep)}
    subprocess.Popen([str(POWERSHELL_WIN), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                      "-EncodedCommand", base64.b64encode(code.encode("utf-16le")).decode("ascii")],
                     env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def discard_pending_uninstall(managed: Path) -> None:
    """Nova instalação na mesma raiz: o uninstall pendente deixa de valer. Remove o que já não estiver em uso
    entre as releases adiadas e apaga o marcador; nunca toca em data_root."""
    managed = Path(managed)
    marker = managed / "state/pending-uninstall.json"
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    for release_id in saved.get("release_ids", []) if isinstance(saved, dict) else []:
        release = managed / "releases" / str(release_id)
        if release.parent == managed / "releases" and release.is_dir():
            releases.remove_unreferenced_release(managed, release)
    marker.unlink(missing_ok=True)


def uninstall_managed(
    *,
    root: Path | None = None,
    yes: bool = False,
    keep_data: bool = False,
    platform_name: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    desktop_dir: Path | None = None,
    start_menu_dir: Path | None = None,
    applications_dir: Path | None = None,
    icons_dir: Path | None = None,
    input_fn: Callable[[str], str] = input,
) -> dict:
    platform_name = platform_name or sys.platform
    env = os.environ if env is None else env
    home_path = Path(home) if home else Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    managed = Path(root) if root is not None else plat.managed_root(platform=platform_name, env=env)
    shortcut_dirs = _shortcut_dirs_from_env(env, desktop_dir, start_menu_dir, applications_dir, icons_dir)

    if not yes:
        try:
            answer = input_fn(f"Remover a instalação gerenciada do Traslado em {managed}? [s/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"s", "sim", "y", "yes"}:
            raise ValueError("Desinstalação cancelada pelo usuário")

    if unsafe_link(managed):
        raise ValueError(f"Raiz gerenciada contém link/reparse point não permitido: {managed}")

    current = plat.read_current(managed)
    candidate = plat.read_candidate(managed)
    valid = [m for m in (current, candidate) if isinstance(m, dict) and isinstance(m.get("data_root"), str) and m["data_root"]]
    pending = managed / "state/pending-uninstall.json"
    if not valid and pending.is_file():
        saved = json.loads(pending.read_text(encoding="utf-8"))
        valid = [{"data_root": value} for value in saved["data_roots"]]
    if not valid:
        raise ValueError("Desinstalacao recusada: current.json e candidate.json ilegiveis ou sem data_root; nao e seguro adivinhar os dados")
    data_root = valid[0]["data_root"]
    preserved = [Path(m["data_root"]).resolve() for m in valid]
    data_roots = list(preserved)
    if any(p == managed.resolve() or p in managed.resolve().parents for p in preserved):
        raise ValueError("Desinstalacao recusada: data_root e ancestral da raiz gerenciada")
    deferred = []
    if platform_name == "win32" and (managed / "releases").is_dir():
        deferred = [p.resolve() for p in (managed / "releases").iterdir() if p.is_dir() and releases.release_in_use(p)]
    runtime_dir = managed / "python"
    if platform_name == "win32" and runtime_dir.is_dir() and (deferred or releases.release_in_use(runtime_dir)):
        deferred.append(runtime_dir.resolve())
    if deferred:
        plat._atomic(managed / "state/pending-uninstall.json", {"release_ids": [p.name for p in deferred], "data_roots": [str(p) for p in preserved]})


    removed_shortcuts = plat.remove_shortcuts(
        ["Traslado"],
        platform=platform_name,
        home=home_path,
        **shortcut_dirs,
    )

    if managed.exists():
        preserved += deferred
        if deferred:
            preserved.append((managed / "state/pending-uninstall.json").resolve())
        # Selective deletion preserves even a deeply nested data_root at its original path.
        def remove_except_data(path: Path) -> None:
            resolved = path.resolve()
            if any(resolved == data or data in resolved.parents for data in preserved):
                return
            if any(resolved in data.parents for data in preserved) and path.is_dir() and not unsafe_link(path):
                for child in path.iterdir():
                    remove_except_data(child)
            elif path.is_dir() and not unsafe_link(path):
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        if keep_data:
            for sub in ("current.json", "candidate.json", "releases", "python", "tools"):
                target = managed / sub
                if target.exists():
                    remove_except_data(target)
        else:
            remove_except_data(managed)

    # O que só este processo prende sai quando ele terminar; o que outro processo prende fica marcado em
    # state/pending-uninstall.json para a próxima execução.
    self_held = bool(deferred) and os.name == "nt" and all(
        Path(sys.executable).resolve().is_relative_to(p) or Path(sys.prefix).resolve().is_relative_to(p)
        or p == runtime_dir.resolve() for p in deferred) and not any(
        d.is_relative_to(p) for d in data_roots for p in deferred)
    if self_held:
        _defer_self_removal(managed, deferred, data_roots)

    return {
        "status": "uninstall_pending" if deferred else "uninstalled",
        "message": ("Remocao concluida quando este processo terminar" if self_held else
                    "Remocao da release em uso adiada ate a proxima execucao") if deferred else "Instalacao removida",
        "deferred_until_process_exit": self_held,
        "root": str(managed),
        "removed_shortcuts": [str(p) for p in removed_shortcuts],
        "data_root_preserved": data_root,
        "keep_data": bool(keep_data),
    }
