# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

from dataclasses import asdict
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from typing import Callable

from .errors import ConversionError, classify

from . import __version__
from .registry import SUPPORTED, lookup
from .detect import sniff
from .model import Options
from .safety import Cancelled, SnapshotLock, is_link, run_bounded, sha256


def _cleanup(path: Path, root: Path) -> None:
    if path.resolve().parent != root.resolve() or not path.name.startswith(".freoa-work-") or is_link(path):
        raise ConversionError('io', "Limpeza fora do diretório de trabalho")
    shutil.rmtree(path)


def output_name(source: Path) -> str:
    """Readable Windows-safe basename; no absolute paths or opaque content keys."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', source.stem).strip(' .')
    if not name:
        name = "Documento"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.I):
        name = "Documento_" + name
    if len(name) > 80:
        # Preserve the tail: editions/years/volume numbers often live there.
        name = name[:39] + " ... " + name[-36:]
    return name.rstrip(' .')


def _verify_previous(target: Path, source: Path, expected_manifest: str | None = None) -> str:
    """Refuse unknown, edited or supplemented outputs. Output root remains trusted."""
    def refuse() -> None:
        raise ConversionError('io', "Sobrescrita recusada: resultado anterior desconhecido, alterado ou de outra entrada; desmarque Sobrescrever para criar outra pasta")
    if is_link(target) or not target.is_dir():
        refuse()
    manifest_path = target / "manifest.json"
    if is_link(manifest_path) or not manifest_path.is_file() or manifest_path.stat().st_size > 16 * 1024 * 1024:
        refuse()
    payload = manifest_path.read_bytes()
    fingerprint = hashlib.sha256(payload).hexdigest()
    if expected_manifest is not None and fingerprint != expected_manifest:
        refuse()
    try:
        manifest = json.loads(payload)
        if (manifest.get("schema_version") != 1 or not re.fullmatch(r"6\.0\.0rc\d+(?:\+p\d+)?|[12]\.\d+\.\d+", manifest.get("converter", ""))
                or manifest.get("source_name") != source.name
                or manifest.get("status") not in {"complete", "partial"}
                or (manifest.get("source_path") is not None and manifest["source_path"] != str(source))):
            refuse()
        entries = manifest["files"]
        if not isinstance(entries, list) or not entries:
            refuse()
        expected_files = {"manifest.json"}
        expected_dirs = set()
        for entry in entries:
            relative = entry["path"]
            if (not isinstance(relative, str) or "\\" in relative or ":" in relative
                    or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/"))
                    or relative in expected_files or type(entry["bytes"]) is not int or entry["bytes"] < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                refuse()
            expected_files.add(relative)
            expected_dirs.update(p.as_posix() for p in Path(relative).parents if p != Path("."))
            file = target / relative
            if any(is_link(p) for p in [file, *file.parents] if p == target or target in p.parents):
                refuse()
            if not file.is_file() or file.stat().st_size != entry["bytes"] or sha256(file) != entry["sha256"]:
                refuse()
        if not {"document.md", "document.json"} <= expected_files:
            refuse()
        directories = manifest.get("directories")
        if directories is not None:
            if not isinstance(directories, list) or len(directories) != len(set(directories)):
                refuse()
            for relative in directories:
                if (not isinstance(relative, str) or "\\" in relative or ":" in relative
                        or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/"))):
                    refuse()
            if not expected_dirs <= set(directories):
                refuse()
            expected_dirs = set(directories)
        elif (target / "assets").is_dir():
            # rc6–rc8 always create assets, including for plain text.
            expected_dirs.add("assets")
        paths = list(target.rglob("*"))
        if (any(is_link(p) for p in paths)
                or {p.relative_to(target).as_posix() for p in paths if p.is_file()} != expected_files
                or {p.relative_to(target).as_posix() for p in paths if p.is_dir()} != expected_dirs
                or any(not p.is_file() and not p.is_dir() for p in paths)
                or sha256(manifest_path) != fingerprint):
            refuse()
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        refuse()
    return fingerprint


def _publish(package: Path, target: Path, source: Path, overwrite: bool,
             cancel: Callable[[], bool] | None = None) -> str | None:
    if not overwrite or not target.exists():
        if is_link(target):
            raise ConversionError('io', "Link de saída não permitido")
        package.rename(target)
        return None
    fingerprint = _verify_previous(target, source)
    if cancel and cancel():
        raise Cancelled("Conversão cancelada")
    backup = target.parent / (".freoa-anterior-" + uuid.uuid4().hex)
    target.rename(backup)
    try:
        if target.exists() or is_link(target):
            raise ConversionError('io', "Destino ocupado durante a publicação")
        package.rename(target)
    except Exception as publication_error:
        try:
            if target.exists() or is_link(target):
                raise ConversionError('io', "Destino ocupado durante a recuperação")
            backup.rename(target)
        except Exception as recovery_error:
            raise ConversionError('io', f"Publicação falhou ({publication_error}); saída anterior preservada em {backup}; recuperação falhou ({recovery_error})") from publication_error
        raise
    try:
        _verify_previous(backup, source, fingerprint)
        shutil.rmtree(backup)
    except (OSError, ValueError):
        return "Não foi possível remover toda a saída anterior; confira " + str(backup)
    return None


def execute(source: Path, output_root: Path, options: Options | None = None,
            cancel: Callable[[], bool] | None = None,
            progress: Callable[[dict], None] | None = None) -> dict:
    options = options or Options()
    started = time.monotonic()
    staging = None
    snapshot_lock = None
    result = {"source": str(source), "status": "failed", "output": None, "error": None}
    try:
        if is_link(source):
            raise ConversionError('io', "Links de entrada não são permitidos")
        source = source.expanduser().resolve(strict=True)
        if not source.is_file():
            raise ConversionError('unsupported', "Formato não suportado; use PDF, DOCX, XLSX, PPTX, texto, CSV, HTML ou imagem")
        if source.stat().st_size > options.max_input_bytes:
            raise ConversionError('resource_limit', "Entrada excede limite de bytes")
        if is_link(output_root):
            raise ConversionError('io', "Links de saída não são permitidos")
        if output_root.exists() and not output_root.is_dir():
            raise ConversionError('io', "A saída deve ser uma pasta, nunca o próprio arquivo de entrada")
        output_root = output_root.expanduser().resolve()
        if output_root == source:
            raise ConversionError('io', "Entrada e saída coincidem")
        if cancel and cancel():
            raise Cancelled("Conversão cancelada")
        output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".freoa-work-", dir=output_root))
        private = staging / "private"
        package = staging / "package"
        private.mkdir()
        package.mkdir()
        snapshot = private / ("input" + source.suffix.lower())
        before = source.stat()
        h = hashlib.sha256()
        with source.open("rb") as reader, snapshot.open("xb") as writer:
            total = 0
            for chunk in iter(lambda: reader.read(1024*1024), b""):
                if cancel and cancel():
                    raise Cancelled("Conversão cancelada")
                total += len(chunk)
                if total > options.max_input_bytes:
                    raise ConversionError('resource_limit', "Entrada excede limite de bytes")
                writer.write(chunk)
                h.update(chunk)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ConversionError('io', "Entrada mudou durante a leitura; tente novamente")
        digest = h.hexdigest()
        # Inspect the immutable private copy, so the dispatched signature and
        # the hashed bytes cannot come from different versions of the source.
        detected = sniff(snapshot, legacy=options.legacy)
        suffix = detected or source.suffix.lower()
        if suffix not in SUPPORTED:
            if suffix == "ole2" and not options.legacy:
                raise ConversionError("unsupported", "Formato detectado ole2; use --engine legacy para DOC/PPT (XLS não suportado)")
            if detected is None:
                raise ConversionError("unsupported", "Formato não suportado; use PDF, DOCX, XLSX, PPTX, texto, CSV, HTML ou imagem")
            raise ConversionError("unsupported", "Formato detectado não suportado: " + detected)
        if lookup(suffix).id == "legacy" and not options.legacy:
            raise ConversionError("unsupported", "Formato legado " + suffix + "; use --engine legacy")
        mismatch = detected is not None and (lookup(source.suffix.lower()) != lookup(detected)
                    or (lookup(detected).id == "legacy" and source.suffix.lower() != detected)
                    or (lookup(detected).id == "image" and source.suffix.lower() not in
                        {detected, {".jpg": ".jpeg", ".tif": ".tiff"}.get(detected)}))
        canonical_snapshot = private / ("input" + suffix)
        if snapshot != canonical_snapshot:
            snapshot.rename(canonical_snapshot)
            snapshot = canonical_snapshot
        if is_link(snapshot):
            raise ConversionError('io', "Snapshot inseguro")
        snapshot_lock = SnapshotLock(snapshot)
        if snapshot.stat().st_size > options.max_input_bytes or sha256(snapshot) != digest:
            raise ConversionError('io', "Snapshot mudou antes da extração")
        request = {"source_name": source.name, "sha256": digest, "snapshot": str(snapshot),
                   "workspace": str(package), "options": {k: v for k, v in asdict(options).items() if k != "legacy" or v}}
        if mismatch:
            request["warnings"] = [f"format_extension_mismatch: extensão {source.suffix.lower()} com conteúdo {detected}"]
        request_file = private / "request.json"
        request_file.write_text(json.dumps(request), encoding="utf-8")
        last_progress = None
        def notify_progress() -> None:
            nonlocal last_progress
            if progress is None:
                return
            try:
                with (package / "progress.json").open("rb") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    return
                payload = raw.decode("utf-8")
                if payload == last_progress:
                    return
                event = json.loads(payload)
                if isinstance(event, dict) and type(event.get("page")) is int and type(event.get("total")) is int:
                    progress(event)
                    last_progress = payload
            except (OSError, ValueError):
                pass
        env = os.environ.copy()
        # Use only the installed/source package location for child imports.
        env.pop("PYTHONHOME", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        measurements = {}
        code = run_bounded([sys.executable, "-P", "-m", "traslado.worker", str(request_file)],
                           private, options.job_timeout, private / "worker.log", cancel, env,
                           max_memory_bytes=options.max_memory_mb * 1024 * 1024,
                           budget_root=package, max_directory_bytes=options.max_output_bytes,
                           on_poll=notify_progress, measurements=measurements)
        if code != 0:
            error = package / "error.json"
            # A process killed at its memory quota may not be able to write JSON.
            exhausted = (code & 0xffffffff) in {0xc0000017, 0xc000009a, 0xc000012d}
            if not error.is_file() and (private / "worker.log").is_file():
                with (private / "worker.log").open("rb") as log:
                    diagnostics = log.read(4 * 1024 * 1024).lower()
                    exhausted |= any(token in diagnostics for token in (
                        b"memoryerror", b"out of memory", b"bad_alloc", b"cannot allocate memory"))
            if code in {-9, -11, -6}:
                exhausted |= (measurements.get("detected_after_s") is not None or
                              (measurements.get("rlimit_applied", False) and
                               measurements.get("peak_bytes", 0) >= .9 * measurements.get("limit_bytes", float("inf"))))
            info = json.loads(error.read_text(encoding="utf-8")) if error.is_file() else {
                "error": "Processo de extração falhou", "code": "resource_limit" if exhausted else "internal"}
            raise ConversionError(info.get("code", "internal"), info["error"], info.get("data"))
        partial_info = private / "partial-error.json"
        if partial_info.is_file():
            info = json.loads(partial_info.read_text(encoding="utf-8"))
            result["error_code"] = info["code"]
            if info.get("data", {}).get("error_pages"):
                result["error_pages"] = info["data"]["error_pages"]
            partial_info.unlink()
        json_path = package / "document.json"
        if not json_path.is_file() or json_path.stat().st_size > options.max_output_bytes:
            raise ConversionError('resource_limit', "Resultado inválido ou excessivo")
        report = json.loads(json_path.read_text(encoding="utf-8"))
        if report.get("source_sha256") != digest or report.get("status") not in {"complete", "partial"}:
            raise ConversionError('io', "Contrato de resultado inválido")
        if cancel and cancel():
            raise Cancelled("Conversão cancelada")
        members = []
        total = 0
        for file in sorted(package.rglob("*")):
            if is_link(file):
                raise ConversionError('io', "Link de saída não permitido")
            if file.is_file():
                total += file.stat().st_size
                if total > options.max_output_bytes:
                    raise ConversionError('resource_limit', "Saída excede limite de bytes")
                members.append({"path": file.relative_to(package).as_posix(), "sha256": sha256(file), "bytes": file.stat().st_size})
        manifest = {"schema_version": 1, "converter": __version__, "source_sha256": digest,
                    "source_name": source.name, "source_path": str(source), "options": {k: v for k, v in asdict(options).items() if k != "legacy" or v}, "files": members,
                    "directories": sorted(p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_dir()),
                    "status": report["status"], "fidelity": "unverified"}
        if report.get("submotor") == "anydoc":
            manifest.update({key: report[key] for key in ("submotor", "submotor_version", "experimental")})
        (package / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if total + (package / "manifest.json").stat().st_size > options.max_output_bytes:
            raise ConversionError('resource_limit', "Saída e manifesto excedem limite de bytes")
        manifest_file = package / "manifest.json"
        members.append({"path": "manifest.json", "sha256": sha256(manifest_file), "bytes": manifest_file.stat().st_size})
        basename = output_name(source)
        # Reserve a name cooperatively, including across independent app windows.
        # User-controlled output root is still trusted; this is not an OS sandbox.
        reservation = None
        for serial in range(1, 2 if options.overwrite else 10001):
            target = output_root / (basename if serial == 1 else f"{basename} ({serial})")
            if not options.overwrite and (target.exists() or is_link(target)):
                continue
            candidate = output_root / (".freoa-reserva-" + target.name)
            try:
                handle = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                continue
            os.close(handle)
            if not options.overwrite and (target.exists() or is_link(target)):
                candidate.unlink()
                continue
            reservation = candidate
            break
        if reservation is None:
            raise ConversionError('io', "Não foi possível reservar uma pasta para o resultado; outro processo pode estar publicando nessa pasta")
        replacing = options.overwrite and (target.exists() or is_link(target))
        # Detect changes since the manifest walk. Output-root access is still
        # trusted: this is a validation boundary, not an OS filesystem sandbox.
        # Unique package + same-filesystem rename makes all user files visible together.
        for attempt in range(4):
            if sha256(snapshot) != digest:
                raise ConversionError('io', "Snapshot mudou durante a extração")
            current_paths = list(package.rglob("*"))
            if any(is_link(file) for file in current_paths) or {
                    file.relative_to(package).as_posix() for file in current_paths if file.is_file()
                    } != {member["path"] for member in members}:
                raise ConversionError('io', "Conjunto de arquivos mudou antes da publicação")
            for member in members:
                file = package / member["path"]
                if is_link(file) or file.stat().st_size != member["bytes"] or sha256(file) != member["sha256"]:
                    raise ConversionError('io', "Resultado mudou antes da publicação")
            if cancel and cancel():
                raise Cancelled("Conversão cancelada")
            try:
                warning = _publish(package, target, source, options.overwrite, cancel)
                if warning:
                    result["cleanup_warning"] = warning
                break
            except OSError as exc:
                if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32} or attempt == 3:
                    raise
                time.sleep(.1 * (2 ** attempt))
        result.update(status=report["status"], output=str(target), sha256=digest, overwritten=bool(replacing),
                      pages=len(report["pages"]), warnings=len(report.get("warnings", [])) + sum(len(p["warnings"]) for p in report["pages"]),
                      extraction_seconds=report["duration_seconds"])
        result["warning_summary"] = dict(Counter(w.split(":", 1)[0] for w in
            report.get("warnings", []) + [w for p in report["pages"] for w in p["warnings"]]))
        result["routes"] = dict(Counter(p["route"] for p in report["pages"]))
        result["full_page_images_in_original"] = sum(bool(p.get("metrics", {}).get("scan_page_image_in_source_pdf"))
            and not p.get("metrics", {}).get("scan_previews_exported") for p in report["pages"])
    except Cancelled as exc:
        result.update(status="cancelled", error=str(exc), error_code="cancelled")
    except TimeoutError as exc:
        result.update(status="timeout", error=str(exc), error_code="timeout")
    except Exception as exc:
        issue = classify(exc)
        status = issue.code if issue.code in {"cancelled", "timeout"} else "failed"
        result.update(status=status, error=str(exc)[:2000], error_code=issue.code)
        result.pop("error_pages", None)
        if issue.data and "error_pages" in issue.data:
            result["error_pages"] = issue.data["error_pages"]
    finally:
        if 'reservation' in locals() and reservation is not None:
            try:
                reservation.unlink(missing_ok=True)
            except OSError:
                result["cleanup_warning"] = "Reserva de nome não removida: " + str(reservation)
        if snapshot_lock:
            snapshot_lock.close()
        if staging and staging.exists():
            try:
                _cleanup(staging, output_root)
            except (OSError, ValueError):
                result["cleanup_warning"] = "Temporários privados não removidos: " + str(staging)
        result["duration_seconds"] = round(time.monotonic() - started, 6)
    return result


def discover(source: Path, output: Path, recursive: bool = False) -> list[Path]:
    if is_link(source):
        raise ConversionError('io', "Links de entrada não são permitidos")
    source = source.resolve(strict=True)
    output = output.resolve()
    if source.is_file():
        return [source]
    if output == source or source in output.parents:
        raise ConversionError('io', "Escolha uma saída fora da pasta de entrada")
    files = []
    for parent, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = [x for x in sorted(dirs) if not x.startswith(".") and not is_link(Path(parent)/x)]
        for name in sorted(names):
            path = Path(parent) / name
            if not name.startswith(".") and not is_link(path) and path.suffix.lower() in SUPPORTED:
                files.append(path)
                if len(files) > 10000:
                    raise ConversionError('resource_limit', "Pasta excede limite de arquivos")
        if not recursive:
            break
    return files
