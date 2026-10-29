# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Ciclo de vida da instalação gerenciada: setup, where, update, uninstall, rollback e migração."""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform as host
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Callable

from . import __version__
from . import doctor
from . import ocr
from . import platform as plat
from . import revocation
from . import runner
from .model import Options
from .pathguard import unsafe_link

DEFAULT_LANGUAGES = ("por", "eng", "spa", "ita", "fra", "deu")
REPOSITORY_URL = "https://github.com/rickfreoa/traslado"
POWERSHELL_WIN = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


class ShortcutError(RuntimeError):
    """Promotion succeeded, desktop integration needs retry (exit 4)."""


_TESSERACT_LINUX = (
    ({"debian", "ubuntu", "linuxmint", "pop"}, "sudo apt install tesseract-ocr"),
    ({"fedora", "rhel", "centos", "rocky", "almalinux"}, "sudo dnf install tesseract"),
    ({"opensuse", "suse", "opensuse-leap", "opensuse-tumbleweed", "sles"}, "sudo zypper install tesseract-ocr"),
    ({"arch", "manjaro", "endeavouros"}, "sudo pacman -S tesseract"),
    ({"alpine"}, "sudo apk add tesseract-ocr"),
)


def linux_tesseract_hint(os_release: str = "/etc/os-release") -> str:
    """Comando de instalação do Tesseract para a distribuição em /etc/os-release (ID e ID_LIKE)."""
    campos: dict[str, list[str]] = {"ID": [], "ID_LIKE": []}
    try:
        with open(os_release, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                if key in campos:
                    campos[key] = value.strip().strip("\"'").lower().split()
    except OSError:
        pass
    for ident in campos["ID"] + campos["ID_LIKE"]:  # ID decide antes de ID_LIKE
        for family, command in _TESSERACT_LINUX:
            if ident in family:
                return "Para instalar o Tesseract nesta distribuição, execute: " + command
    return "Instale o pacote do Tesseract OCR pelo gerenciador de pacotes da sua distribuição."


def validate_version(version: str) -> str:
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?", version):
        raise ValueError("Versao invalida")
    return version


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
    if not candidate_reprovada:
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


def _find_ocr_lock(release_dir: Path, from_dir: Path | None = None) -> Path:
    candidate = Path(__file__).resolve().parent / "assets/ocr-assets.lock.json"
    if not candidate.is_file():
        raise FileNotFoundError("Lock OCR empacotado ausente")
    return candidate


def _find_bootstrap_ps1(release_dir: Path, from_dir: Path | None = None) -> Path:
    candidate = Path(__file__).resolve().parent / "assets/bootstrap_ocr.ps1"
    if not candidate.is_file():
        raise FileNotFoundError("Bootstrap OCR empacotado ausente")
    return candidate


def _install_tessdata_from_lock(
    destination_ocr: Path,
    languages: list[str],
    lock_path: Path,
    *,
    cache_dir: Path | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
) -> Path:
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    assets_by_name = {a["name"]: a for a in lock.get("assets", []) if isinstance(a, dict) and "name" in a}
    tessdata_dir = destination_ocr / "tessdata"
    doc_dir = destination_ocr / "doc"
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)

    wanted = [*(f"{lang}.traineddata" for lang in languages), "TESSDATA-LICENSE.txt"]
    for name in wanted:
        spec = assets_by_name.get(name)
        if not spec:
            raise ValueError(f"Asset OCR ausente no lock: {name}")
        data: bytes | None = None
        if cache_dir and (cache_dir / name).is_file():
            data = (cache_dir / name).read_bytes()
        else:
            declared = int(spec["bytes"])
            if declared <= 0 or declared > 64 * 1024 * 1024:
                raise ValueError(f"Tamanho OCR fora do limite: {name}")
            limit = min(64 * 1024 * 1024, (declared * 101 + 99) // 100)
            data = fetch_bytes(spec["url"]) if fetch_bytes else revocation.default_fetch_bytes(spec["url"], max_bytes=limit)
            if cache_dir:
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / name).write_bytes(data)
        if len(data) != int(spec["bytes"]):
            raise ValueError(f"Tamanho divergente para {name}: {len(data)} != {spec['bytes']}")
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != spec["sha256"].lower():
            raise ValueError(f"SHA-256 divergente para {name}: {actual_sha} != {spec['sha256']}")
        target = (doc_dir / name) if name == "TESSDATA-LICENSE.txt" else (tessdata_dir / name)
        target.write_bytes(data)
    return tessdata_dir


def _purge_seven_zip(root: Path) -> None:
    """Garante que nenhum executável, DLL ou instalador do 7-Zip permaneça na instalação."""
    if not root.exists():
        return
    forbidden_names = {"7z.exe", "7z.dll", "7zr.exe", "7z2603-x64.exe"}
    for item in list(root.rglob("*")):
        try:
            if item.is_dir() and item.name in {".bootstrap-tools", ".ocr-download-cache"}:
                shutil.rmtree(item, ignore_errors=True)
            elif item.is_file() and item.name.lower() in forbidden_names:
                item.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_tesseract_version(raw_text: str) -> str | None:
    match = re.search(r"tesseract\s+v?([0-9]+(?:\.[0-9]+)*)", raw_text, re.I)
    return match.group(1) if match else None


def setup_ocr(
    release_dir: Path,
    *,
    root: Path | None = None,
    profile: str = "Full",
    languages: list[str] | None = None,
    skip_ocr: bool = False,
    from_dir: Path | None = None,
    ocr_cache: Path | None = None,
    platform_name: str | None = None,
    env: dict[str, str] | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
) -> dict:
    platform_name = platform_name or sys.platform
    env = os.environ if env is None else env
    langs = normalize_languages(languages)
    if profile != "Full" or skip_ocr or env.get("TRASLADO_SKIP_OCR") == "1":
        return {
            "ocr_installed": False,
            "ocr": {"tesseract": None, "origin": "none", "version": None},
            "tessdata": None,
        }

    lock_path = _find_ocr_lock(release_dir, from_dir)
    cache = (
        Path(ocr_cache)
        if ocr_cache
        else Path(env["FREOA_OCR_TEST_CACHE"])
        if env.get("FREOA_OCR_TEST_CACHE")
        else (from_dir / "ocr-cache")
        if (from_dir and (from_dir / "ocr-cache").is_dir())
        else None
    )

    dest_ocr = release_dir / "ocr"
    if platform_name == "win32":
        if (dest_ocr / "tesseract.exe").is_file() and (dest_ocr / "tessdata").is_dir():
            exe = str(dest_ocr / "tesseract.exe")
            version = "5.5.3"
            receipt_file = dest_ocr / "ocr-runtime.json"
            if receipt_file.is_file():
                try:
                    v = _parse_tesseract_version(json.loads(receipt_file.read_text(encoding="utf-8-sig")).get("version", ""))
                    if v:
                        version = v
                except Exception:
                    pass
            if root:
                _purge_seven_zip(root)
            _purge_seven_zip(release_dir)
            return {
                "ocr_installed": True,
                "ocr": {"tesseract": exe, "origin": "lock", "version": version},
                "tessdata": str(dest_ocr / "tessdata"),
            }

        ps1 = _find_bootstrap_ps1(release_dir, from_dir)
        temp_cache_ctx = tempfile.TemporaryDirectory(prefix="traslado-ocr-cache-") if cache is None else None
        try:
            effective_cache = cache if cache is not None else Path(temp_cache_ctx.name)
            cmd = [
                str(POWERSHELL_WIN if POWERSHELL_WIN.is_file() else "powershell.exe"),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
                "-Destination",
                str(dest_ocr),
                "-CacheDirectory",
                str(effective_cache),
                "-LockPath",
                str(lock_path),
                "-Languages",
                ",".join(langs),
            ]
            if env.get("TRASLADO_OCR_OFFLINE") == "1":
                cmd.append("-Offline")
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            if temp_cache_ctx is not None:
                temp_cache_ctx.cleanup()
            if root:
                _purge_seven_zip(root)
            _purge_seven_zip(release_dir)

        exe = str(dest_ocr / "tesseract.exe")
        version = "5.5.3"
        receipt_file = dest_ocr / "ocr-runtime.json"
        if receipt_file.is_file():
            try:
                v = _parse_tesseract_version(json.loads(receipt_file.read_text(encoding="utf-8-sig")).get("version", ""))
                if v:
                    version = v
            except Exception:
                pass
        return {
            "ocr_installed": True,
            "ocr": {"tesseract": exe, "origin": "lock", "version": version},
            "tessdata": str(dest_ocr / "tessdata"),
        }

    # macOS (darwin) and Linux
    exe_path: str | None = None
    origin = "none"
    search_path = env.get("PATH", os.environ.get("PATH", ""))

    if platform_name == "darwin":
        brew = shutil.which("brew", path=search_path)
        if brew:
            try:
                proc = subprocess.run([brew, "--prefix", "tesseract"], capture_output=True, text=True, timeout=15)
                if proc.returncode == 0 and proc.stdout.strip():
                    candidate = Path(proc.stdout.strip()) / "bin/tesseract"
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        exe_path = str(candidate.resolve())
                        origin = "homebrew"
            except (OSError, subprocess.SubprocessError):
                pass
        if not exe_path:
            found = shutil.which("tesseract", path=search_path)
            if found:
                exe_path = str(Path(found).resolve())
                origin = "system"
        if not exe_path:
            print("OCR indisponível. Para instalar o Tesseract no macOS, execute: brew install tesseract")
            return {
                "ocr_installed": False,
                "ocr": {"tesseract": None, "origin": "none", "version": None},
                "tessdata": None,
            }
    else:
        found = shutil.which("tesseract", path=search_path)
        if found:
            exe_path = str(Path(found).resolve())
            origin = "system"
        else:
            print("OCR indisponível. " + linux_tesseract_hint())
            return {
                "ocr_installed": False,
                "ocr": {"tesseract": None, "origin": "none", "version": None},
                "tessdata": None,
            }

    tessdata_dir = _install_tessdata_from_lock(
        dest_ocr,
        langs,
        lock_path,
        cache_dir=cache,
        fetch_bytes=fetch_bytes,
    )
    version_str = None
    try:
        proc = subprocess.run([exe_path, "--version"], capture_output=True, text=True, timeout=10)
        version_str = _parse_tesseract_version((proc.stdout or "") + "\n" + (proc.stderr or ""))
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "ocr_installed": True,
        "ocr": {"tesseract": exe_path, "origin": origin, "version": version_str},
        "tessdata": str(tessdata_dir),
    }


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


def evaluate_doctor_gate(
    report: dict,
    *,
    profile: str = "Full",
    languages: list[str] | None = None,
    ocr_installed: bool = False,
) -> bool:
    if not isinstance(report, dict):
        return False
    caps = report.get("capabilities")
    if not isinstance(caps, dict):
        return False
    if profile == "Full":
        for mod in ("fitz", "PIL", "docx", "openpyxl", "pptx"):
            if not isinstance(caps.get(mod), dict) or caps[mod].get("available") is not True:
                return False
    if ocr_installed:
        langs = normalize_languages(languages)
        avail = set(report.get("languages") or [])
        if not set(langs) <= avail:
            return False
    return True


def run_fixture_gate(
    *,
    profile: str = "Full",
    tesseract: str | None = None,
    tessdata: str | None = None,
    execute_fn: Callable[..., dict] | None = None,
    chunks: bool = False,
) -> bool:
    assets_dir = Path(__file__).resolve().parent / "assets"
    docx_fixture = assets_dir / "fixture.docx"
    pdf_fixture = assets_dir / "fixture.pdf"
    exec_call = execute_fn or runner.execute

    with tempfile.TemporaryDirectory(prefix="traslado-fixture-") as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"
        targets: list[Path] = []
        if profile == "Full":
            if not docx_fixture.is_file() or not pdf_fixture.is_file():
                print("FIXTURE_GATE_FAILED: fixture DOCX/PDF ausente no pacote instalado", file=sys.stderr)
                return False
            targets = [docx_fixture, pdf_fixture]
        else:
            if docx_fixture.is_file() and importlib.util.find_spec("docx") is not None:
                targets = [docx_fixture]
            else:
                txt_fixture = tmp_path / "fixture.txt"
                txt_fixture.write_text("Traslado verificacao de instalacao Core OK.\n", encoding="utf-8")
                targets = [txt_fixture]

        opts = Options(mode="native", chunks=chunks, tesseract=tesseract, tessdata=tessdata)
        for fixture in targets:
            try:
                res = exec_call(fixture, out_dir, opts)
            except Exception as exc:
                print(f"FIXTURE_GATE_FAILED {fixture.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return False
            if (not isinstance(res, dict) or res.get("status") not in {"complete", "partial"}
                    or "error_code" in res or not res.get("output")):
                resumo = {k: res.get(k) for k in ("status", "error_code", "error", "warning_summary")} if isinstance(res, dict) else res
                print(f"FIXTURE_GATE_FAILED {fixture.name}: {resumo!r}", file=sys.stderr)
                return False
            out_path = Path(res["output"])
            required = ["document.md", "document.json", "manifest.json", "LEIA-AVISOS.txt"]
            if chunks:
                required.append("chunks/chunks.jsonl")
            ausentes = [name for name in required if not (out_path / name).is_file()]
            if ausentes:
                print(f"FIXTURE_GATE_FAILED {fixture.name}: arquivos ausentes {ausentes}", file=sys.stderr)
                return False
    return True


def release_integrity(release_dir: Path) -> dict[str, str]:
    return {p.relative_to(release_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(release_dir.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.name != "release-integrity.json"}


def active_release_matches(root: Path, version: str, profile: str, extras=None, languages=None) -> bool:
    current = plat.read_current(root)
    if not current or (current.get("version"), current.get("profile"), current.get("extras"), current.get("languages")) != (
            version, profile, normalize_extras(extras), normalize_languages(languages)):
        return False
    try:
        release = plat._release(root, current["release_id"])
        inventory = json.loads((release / "release-integrity.json").read_text(encoding="utf-8"))
        return bool(inventory) and plat.release_python(root, current["release_id"]).is_file() and inventory == release_integrity(release)
    except (OSError, ValueError, KeyError):
        return False


def validate_bootstrap_digests(digests: dict | None, version: str, profile: str, extras: list[str]) -> dict:
    required = {f"traslado-{version}-py3-none-any.whl", f"requirements-{profile.lower()}.hashes.lock"}
    if "legacy" in extras:
        required.add("requirements-legacy.hashes.lock")
    if not isinstance(digests, dict) or not required <= digests.keys():
        raise ValueError("Mapa de digests vazio ou incompleto do bootstrap")
    if any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
           for k, v in digests.items()):
        raise ValueError("Mapa de digests invalido do bootstrap")
    return digests


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
    prune_releases(managed, pending_only=True)
    norm_profile = "Core" if str(profile).lower() == "core" else "Full"
    norm_extras = normalize_extras(extras)
    norm_langs = normalize_languages(languages)
    shortcut_dirs = _shortcut_dirs_from_env(env, desktop_dir, start_menu_dir, applications_dir, icons_dir)

    if release_id is None and promote and active_release_matches(managed, __version__, norm_profile, norm_extras, norm_langs):
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
        and active_release_matches(managed, __version__, norm_profile, norm_extras, norm_langs)
    ):
        handle_legacy_windows(
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
    ocr_info = setup_ocr(
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
        doctor_ok = evaluate_doctor_gate(
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
            fixture_ok = run_fixture_gate(
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
                digests = validate_bootstrap_digests(expected_digests, __version__, norm_profile, norm_extras)
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
                remove_unreferenced_release(managed, release_dir, candidate_reprovada=True)
            raise ValueError("Promoção recusada: " + "; ".join(reasons))
        plat._atomic(release_dir / "release-integrity.json", release_integrity(release_dir))
        promoted = plat.promote(managed, now=now, allow_offline=offline_allowed, offline_reason=offline_reason)
        prune_releases(managed)
        try:
            handle_legacy_windows(
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
            remove_unreferenced_release(managed, release)
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
        deferred = [p.resolve() for p in (managed / "releases").iterdir() if p.is_dir() and release_in_use(p)]
    runtime_dir = managed / "python"
    if platform_name == "win32" and runtime_dir.is_dir() and (deferred or release_in_use(runtime_dir)):
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
            novo = _managed_python_exe(managed)
            raise ShortcutError("Atualizacao promovida; somente os atalhos falharam. Recrie com: "
                                + (f"\"{novo}\" -I -m traslado shortcuts --root \"{managed}\"" if novo else
                                   f"<python da release ativa> -I -m traslado shortcuts --root \"{managed}\""))
        if proc.returncode != 0:
            detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
            raise ValueError(f"Falha ao executar {script_name} para v{target_version}: {detail}")

    return {"status": "updated", "version": target_version, "root": str(managed)}
