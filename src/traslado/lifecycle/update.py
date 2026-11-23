# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Atualização da instalação gerenciada, por diretório local ou pela release publicada no GitHub."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

from .. import platform as plat
from .. import revocation
from . import releases
from ._common import DEFAULT_LANGUAGES, POWERSHELL_WIN, ShortcutError, validate_version

def update_release(
    *,
    version: str | None = None,
    yes: bool = False,
    root: Path | None = None,
    from_dir: Path | None = None,
    allow_offline_promotion: bool = False,
    repo: str = revocation.DEFAULT_REPO,
    platform_name: str | None = None,
    env: dict[str, str] | None = None,
    fetch_json: Callable[[str], dict] | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
    gh_cmd: str | bool | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    platform_name = platform_name or sys.platform
    env = os.environ if env is None else env
    managed = Path(root) if root is not None else plat.managed_root(platform=platform_name, env=env)
    script_name = "install.ps1" if platform_name == "win32" else "install.sh"
    runner_fn = run_cmd or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=600, env=dict(env)))

    local_dir_val = from_dir
    if local_dir_val is not None:
        src_dir = Path(local_dir_val).resolve()
        sums_path = src_dir / "SHA256SUMS"
        script_path = src_dir / script_name
        if not sums_path.is_file() or not script_path.is_file():
            raise ValueError(f"Artefatos obrigatórios (SHA256SUMS, {script_name}) ausentes em {src_dir}; nada foi alterado")
        sums_map_local: dict[str, str] = {}
        for line in sums_path.read_text(encoding="utf-8-sig").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                sums_map_local[parts[-1].lstrip("*")] = parts[0].lower()
        expected_script_sha = sums_map_local.get(script_name)
        actual_script_sha = hashlib.sha256(script_path.read_bytes()).hexdigest()
        if not expected_script_sha or actual_script_sha != expected_script_sha:
            raise ValueError(f"Hash de {script_name} não confere com SHA256SUMS em {src_dir}; nada foi alterado")

        target_version = version.removeprefix("v") if version else ""
        candidates = [m.group(1) for k in sums_map_local
                      if (m := re.fullmatch(r"traslado-([0-9][0-9A-Za-z.+-]*)-py3-none-any\.whl", k))]
        if not target_version:
            if len(candidates) != 1:
                raise ValueError(f"Diretório local deve ter um único wheel: {candidates}; nada foi alterado")
            target_version = candidates[0]
        if target_version not in candidates:
            raise ValueError(f"Wheel da versão solicitada ausente: {target_version}; nada foi alterado")

        current_manifest = plat.read_current(managed)
        active_profile = (current_manifest.get("profile") if isinstance(current_manifest, dict) else None) or "Full"
        active_extras = (current_manifest.get("extras") if isinstance(current_manifest, dict) else None) or []
        active_langs = (current_manifest.get("languages") if isinstance(current_manifest, dict) else None) or list(DEFAULT_LANGUAGES)
        sc_config = current_manifest.get("shortcuts") if isinstance(current_manifest, dict) else None
        # Esquema 2: {"enabled": bool, ...}; manifestos anteriores guardavam a lista de caminhos.
        had_shortcuts = bool(sc_config.get("enabled")) if isinstance(sc_config, dict) else bool(sc_config)

        if platform_name == "win32":
            ps = str(POWERSHELL_WIN if POWERSHELL_WIN.is_file() else "powershell.exe")
            argv = [
                ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path),
                "-FromDir", str(src_dir),
                "-Version", target_version,
                "-Profile", active_profile,
                "-Languages", ",".join(active_langs),
                "-Root", str(managed),
            ]
            if active_extras:
                argv.extend(["-Extras", ",".join(active_extras)])
            if not had_shortcuts:
                argv.append("-NoShortcut")
            if yes:
                argv.append("-Yes")
            if allow_offline_promotion:
                argv.append("-AllowOfflinePromotion")
        else:
            argv = [
                "sh", str(script_path),
                "--from-dir", str(src_dir),
                "--version", target_version,
                "--profile", active_profile.lower(),
                "--languages", ",".join(active_langs),
                "--root", str(managed),
            ]
            if active_extras:
                argv.extend(["--extras", ",".join(active_extras)])
            if not had_shortcuts:
                argv.append("--no-shortcut")
            if yes:
                argv.append("--yes")
            if allow_offline_promotion:
                argv.append("--allow-offline-promotion")

        proc = runner_fn(argv)
        if proc.returncode != 0:
            detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
            raise ValueError(f"Falha ao executar {script_name} para v{target_version}: {detail}")
        return {"status": "updated", "version": target_version, "root": str(managed)}

    json_fetcher = fetch_json or revocation.default_fetch_json
    bytes_fetcher = fetch_bytes or revocation.default_fetch_bytes

    config = plat.read_current(managed)
    if not config:
        raise ValueError("current.json ilegivel ou ausente; update requer configuracao atual")
    try:
        if version:
            clean_ver = validate_version(version)
            rel_url = f"https://api.github.com/repos/{repo}/releases/tags/v{clean_ver}"
        else:
            rel_url = f"https://api.github.com/repos/{repo}/releases/latest"
        release_data = json_fetcher(rel_url)
    except Exception as exc:
        raise ValueError(f"Sem conexão com o GitHub ou falha ao consultar atualizações; nada foi alterado ({exc})") from exc

    tag_name = str(release_data.get("tag_name") or "")
    target_version = validate_version(tag_name.removeprefix("v"))
    if not target_version:
        raise ValueError("Resposta da API de releases não contém tag_name válida; nada foi alterado")

    script_name = "install.ps1" if platform_name == "win32" else "install.sh"
    base_download = f"https://github.com/{repo}/releases/download/v{target_version}"

    try:
        sums_bytes = bytes_fetcher(f"{base_download}/SHA256SUMS")
        script_bytes = bytes_fetcher(f"{base_download}/{script_name}")
    except Exception as exc:
        raise ValueError(f"Sem conexão ao baixar instalador da versão v{target_version}; nada foi alterado ({exc})") from exc

    sums_map: dict[str, str] = {}
    for line in sums_bytes.decode("utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            name = parts[-1].lstrip("*")
            if name in sums_map:
                raise ValueError("Nome duplicado em SHA256SUMS")
            sums_map[name] = parts[0].lower()

    expected_script_sha = sums_map.get(script_name)
    actual_script_sha = hashlib.sha256(script_bytes).hexdigest()
    if not expected_script_sha or actual_script_sha != expected_script_sha:
        raise ValueError(f"Hash de {script_name} não confere com SHA256SUMS da tag v{target_version}; nada foi alterado")

    expected_digests = {
        "SHA256SUMS": hashlib.sha256(sums_bytes).hexdigest(),
        script_name: actual_script_sha,
    }
    revocation.verify_promotion(
        managed,
        target_version,
        expected_digests,
        repo=repo,
        fetch_bytes=bytes_fetcher,
        fetch_json=json_fetcher,
        gh_cmd=gh_cmd,
        run_cmd=run_cmd,
    )

    gh_exe = None if gh_cmd is False else (gh_cmd if isinstance(gh_cmd, str) else shutil.which("gh"))
    runner_fn = run_cmd or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=300))

    with tempfile.TemporaryDirectory(prefix="traslado-update-") as tmp:
        tmp_dir = Path(tmp)
        script_path = tmp_dir / script_name
        script_path.write_bytes(script_bytes)
        if gh_exe:
            att = runner_fn([
                gh_exe,
                "attestation",
                "verify",
                str(script_path),
                "--repo",
                repo,
                "--signer-workflow",
                f"{repo}/.github/workflows/release.yml",
                "--source-ref", f"refs/tags/v{target_version}",
            ])
            if att.returncode != 0:
                raise ValueError(f"Attestation de {script_name} reprovada pelo gh; nada foi alterado")

        if platform_name == "win32":
            ps = str(POWERSHELL_WIN if POWERSHELL_WIN.is_file() else "powershell.exe")
            argv = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path),
                    "-Version", target_version, "-Root", str(managed)]
            if yes:
                argv.append("-Yes")
        else:
            argv = ["sh", str(script_path), "--version", target_version, "--root", str(managed)]
            if yes:
                argv.append("--yes")

        flag = "-" if platform_name == "win32" else "--"
        names = ("Profile", "Extras", "Languages", "DataRoot") if platform_name == "win32" else ("profile", "extras", "languages", "data-root")
        for name, value in zip(names, (config["profile"], ",".join(config.get("extras", [])), ",".join(config["languages"]), config["data_root"])):
            argv += [flag + name, value]
        shortcuts = config.get("shortcuts", {})
        if not shortcuts.get("enabled", True):
            argv.append("-NoShortcut" if platform_name == "win32" else "--no-shortcut")
        install_env = dict(env)
        if shortcuts.get("home"):
            install_env.update(HOME=shortcuts["home"], USERPROFILE=shortcuts["home"])
        for key in ("desktop_dir", "start_menu_dir", "applications_dir", "icons_dir"):
            value = shortcuts.get("dirs", {}).get(key)
            var = "TRASLADO_SHORTCUT_" + key.upper()
            install_env.pop(var, None)
            if value:
                install_env[var] = value
        proc = run_cmd(argv, env=install_env) if run_cmd else subprocess.run(argv, env=install_env, capture_output=True, text=True, timeout=300)
        if proc.returncode == 4:
            novo = releases._managed_python_exe(managed)
            raise ShortcutError("Atualizacao promovida; somente os atalhos falharam. Recrie com: "
                                + (f"\"{novo}\" -I -m traslado shortcuts --root \"{managed}\"" if novo else
                                   f"<python da release ativa> -I -m traslado shortcuts --root \"{managed}\""))
        if proc.returncode != 0:
            detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
            raise ValueError(f"Falha ao executar {script_name} para v{target_version}: {detail}")

    return {"status": "updated", "version": target_version, "root": str(managed)}
