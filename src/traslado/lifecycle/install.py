# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Instalação de uma release: candidata, gates, revogação, promoção e atalhos (comando setup)."""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import platform as host
import sys
import uuid
from typing import Callable

from .. import __version__
from .. import doctor
from .. import platform as plat
from .. import revocation
from . import gates, legacy_windows, ocr_setup, releases
from ._common import (REPOSITORY_URL, ShortcutError, _shortcut_dirs_from_env, _utc_now, compute_release_id,
                      normalize_extras, normalize_languages)

def setup_release(
    *,
    root: Path | None = None,
    release_id: str | None = None,
    profile: str = "Full",
    extras: list[str] | str | None = None,
    languages: list[str] | str | None = None,
    no_shortcut: bool = False,
    keep_failed: bool = False,
    promote: bool = False,
    allow_offline_promotion: bool = False,
    skip_ocr: bool = False,
    from_dir: Path | None = None,
    ocr_cache: Path | None = None,
    data_root: Path | None = None,
    platform_name: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    desktop_dir: Path | None = None,
    start_menu_dir: Path | None = None,
    applications_dir: Path | None = None,
    icons_dir: Path | None = None,
    expected_digests: dict[str, str] | None = None,
    doctor_fn: Callable[..., dict] | None = None,
    fixture_fn: Callable[..., bool] | None = None,
    revocation_fn: Callable[..., dict] | None = None,
    now: datetime | str | None = None,
) -> dict:
    platform_name = platform_name or sys.platform
    env = os.environ if env is None else env
    home_path = Path(home) if home else Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    managed = Path(root) if root is not None else plat.managed_root(platform=platform_name, env=env)
    releases.prune_releases(managed, pending_only=True)
    norm_profile = "Core" if str(profile).lower() == "core" else "Full"
    norm_extras = normalize_extras(extras)
    norm_langs = normalize_languages(languages)
    shortcut_dirs = _shortcut_dirs_from_env(env, desktop_dir, start_menu_dir, applications_dir, icons_dir)

    if release_id is None and promote and releases.active_release_matches(managed, __version__, norm_profile, norm_extras, norm_langs):
        return plat.read_current(managed)

    if release_id is None:
        # Derive from this candidate interpreter, otherwise allocate a fresh id.
        try:
            rel_parts = Path(sys.prefix).resolve().relative_to(managed.resolve() / "releases").parts
            if rel_parts:
                release_id = rel_parts[0]
        except Exception:
            pass
        if not release_id:
            release_id = compute_release_id(__version__, norm_profile, norm_extras, norm_langs, uuid.uuid4().hex)

    # Idempotency check: if current.json already has the exact same active release, do not mutate current.json.
    current = plat.read_current(managed)
    if (
        promote
        and isinstance(current, dict)
        and current.get("product") == "traslado"
        and current.get("release_id") == release_id
        and current.get("version") == __version__
        and current.get("profile") == norm_profile
        and sorted(current.get("extras") or []) == norm_extras
        and (current.get("languages") or []) == norm_langs
        and releases.active_release_matches(managed, __version__, norm_profile, norm_extras, norm_langs)
    ):
        legacy_windows.handle_legacy_windows(
            managed,
            version=__version__,
            platform_name=platform_name,
            env=env,
            home=home_path,
            now=now,
            **shortcut_dirs,
        )
        print(f"Licença: AGPL-3.0-or-later | Fonte: {REPOSITORY_URL}/tree/v{__version__}")
        return current

    if isinstance(current, dict) and release_id in {current.get("release_id"), current.get("previous_release_id")}:
        raise ValueError("Release referenciada nao pode ser reconstruida; use novo release_id")
    release_dir = plat._release(managed, release_id)
    release_dir.mkdir(parents=True, exist_ok=True)
    expected_py = plat.release_python(managed, release_id, platform=platform_name)
    python_exe = str(expected_py.resolve()) if expected_py.is_file() else str(Path(sys.executable).resolve())

    default_managed = plat.managed_root(platform=platform_name, env=env)
    is_custom_root = root is not None and Path(root).resolve() != default_managed.resolve()
    resolved_data_root = (
        Path(data_root)
        if data_root
        else Path(env["TRASLADO_DATA_ROOT"])
        if env.get("TRASLADO_DATA_ROOT")
        else Path(current["data_root"])
        if (isinstance(current, dict) and current.get("data_root"))
        else (managed / "data")
        if is_custom_root
        else (home_path / "Documents" / "Traslado")
    )
    resolved_data_root.mkdir(parents=True, exist_ok=True)

    # (a) OCR
    ocr_info = ocr_setup.setup_ocr(
        release_dir,
        root=managed,
        profile=norm_profile,
        languages=norm_langs,
        skip_ocr=skip_ocr,
        from_dir=from_dir,
        ocr_cache=ocr_cache,
        platform_name=platform_name,
        env=env,
    )

    runtime_cfg = {
        "schema_version": 1,
        "languages": norm_langs,
        "data_root": str(resolved_data_root),
        "profile": norm_profile,
        "ocr_installed": ocr_info["ocr_installed"],
    }
    plat._atomic(release_dir / "freoa-runtime.json", runtime_cfg)

    # (c) Doctor
    inspect_call = doctor_fn or doctor.inspect
    try:
        doctor_report = inspect_call(ocr_info["ocr"]["tesseract"], ocr_info["tessdata"])
        plat._atomic(release_dir / "installation-doctor.json", doctor_report)
        doctor_ok = gates.evaluate_doctor_gate(
            doctor_report,
            profile=norm_profile,
            languages=norm_langs,
            ocr_installed=ocr_info["ocr_installed"],
        )
    except Exception as exc:
        print(f"DOCTOR_GATE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        doctor_ok = False

    # (d) Conversão de fixture embutida
    try:
        if fixture_fn is not None:
            fixture_ok = bool(fixture_fn())
        else:
            fixture_ok = gates.run_fixture_gate(
                profile=norm_profile,
                tesseract=ocr_info["ocr"]["tesseract"],
                tessdata=ocr_info["tessdata"],
            )
    except Exception as exc:
        print(f"FIXTURE_GATE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        fixture_ok = False

    # (e) / (f) Revogação e candidate.json
    offline_reason = env.get("TRASLADO_PROMOCAO_OFFLINE")
    offline_allowed = allow_offline_promotion and offline_reason in {"rascunho", "teste"}
    revocation_status = "blocked"
    revocation_error: str | None = None
    if doctor_ok and fixture_ok:
        if offline_allowed:
            revocation_status = "offline"
        elif promote:
            rev_call = revocation_fn or revocation.verify_promotion
            try:
                if allow_offline_promotion:
                    raise ValueError("Excecao offline exige TRASLADO_PROMOCAO_OFFLINE=rascunho ou teste")
                digests = gates.validate_bootstrap_digests(expected_digests, __version__, norm_profile, norm_extras)
                rev_res = rev_call(managed, __version__, digests, now=now)
                revocation_status = "ok" if rev_res.get("status") == "ok" else "blocked"
            except Exception as exc:
                revocation_status = "blocked"
                revocation_error = str(exc)
        else:
            revocation_status = "offline"

    install_id = (
        current.get("install_id")
        if (isinstance(current, dict) and isinstance(current.get("install_id"), str) and len(current["install_id"]) == 32)
        else hashlib.sha256(str(managed).encode("utf-8")).hexdigest()[:32]
    )

    candidate_data = {
        "schema_version": 2,
        "product": "traslado",
        "install_id": install_id,
        "release_id": release_id,
        "version": __version__,
        "profile": norm_profile,
        "extras": norm_extras,
        "python": python_exe,
        "module": "traslado",
        "legacy_module": "freoa_v6",
        "platform": platform_name,
        "arch": host.machine(),
        "data_root": str(resolved_data_root),
        "languages": norm_langs,
        "ocr_installed": ocr_info["ocr_installed"],
        "ocr": ocr_info["ocr"],
        "source": {"tag": f"v{__version__}", "repository": REPOSITORY_URL},
        "created_at": _utc_now(now),
        "shortcuts": {"enabled": not no_shortcut, "home": str(home_path),
                      "dirs": {k: str(v) if v else None for k, v in shortcut_dirs.items()}},
        "gates": {
            "doctor": bool(doctor_ok),
            "fixture": bool(fixture_ok),
            "revocation": revocation_status,
            **({"offline_reason": offline_reason} if offline_allowed else {}),
        },
    }
    plat.write_candidate(managed, candidate_data)
    print(f"Licença: AGPL-3.0-or-later | Fonte: {REPOSITORY_URL}/tree/v{__version__}")

    if promote:
        if not doctor_ok or not fixture_ok or revocation_status == "blocked":
            reasons = []
            if not doctor_ok:
                reasons.append("doctor reprovado")
            if not fixture_ok:
                reasons.append("fixture reprovada")
            if revocation_status == "blocked":
                reasons.append(f"revogação bloqueada ({revocation_error or 'sem confirmação dos dois canais'})")
            if not keep_failed:
                releases.remove_unreferenced_release(managed, release_dir, candidate_reprovada=True)
            raise ValueError("Promoção recusada: " + "; ".join(reasons))
        plat._atomic(release_dir / "release-integrity.json", releases.release_integrity(release_dir))
        promoted = plat.promote(managed, now=now, allow_offline=offline_allowed, offline_reason=offline_reason)
        releases.prune_releases(managed)
        try:
            legacy_windows.handle_legacy_windows(
                managed,
                version=__version__,
                platform_name=platform_name,
                env=env,
                home=home_path,
                now=now,
                **shortcut_dirs,
            )
            plat.refresh_shortcuts(managed, promoted)
        except Exception as exc:
            raise ShortcutError(f"Instalacao promovida; somente os atalhos falharam: {exc}. Recrie-os com: \"{python_exe}\" -I -m traslado shortcuts --root \"{managed}\"") from exc
        return promoted

    return candidate_data
