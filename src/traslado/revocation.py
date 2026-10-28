# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Verificação de revogação em dois canais e emissão de revoked.json (stdlib + urllib)."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Callable
import urllib.error
import urllib.request

from . import platform as plat

SCHEMA_REVOKED = "traslado-revoked-1"
SCHEMA_STATE = "traslado-revoked-state-1"
DEFAULT_REPO = "rickfreoa/traslado"
REVOCATION_TAG = "revocation"
REVOCATION_WORKFLOW = ".github/workflows/revocation.yml"
REVOCATION_REF = "refs/heads/main"
USER_AGENT = "Traslado-Revocation/2.0.0"


class RevocationError(ValueError):
    """Falha na verificação de revogação ou divergência entre canais."""


def _ensure_utc(instant: datetime | str | None = None) -> datetime:
    if instant is None:
        return datetime.now(timezone.utc)
    if isinstance(instant, str):
        return _parse_utc(instant)
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def _format_utc(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise RevocationError(f"Timestamp UTC inválido: {value!r}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise RevocationError(f"Timestamp UTC inválido: {value!r}") from exc


def default_fetch_bytes(url: str, timeout: int = 30) -> bytes:
    if not url.startswith("https://"):
        raise RevocationError(f"URL não HTTPS recusada: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream, application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise RevocationError("Resposta HTTP excede 4 MiB")
            return data
    except (urllib.error.URLError, OSError) as exc:
        raise RevocationError(f"Falha de rede ao consultar {url}: {exc}") from exc


def default_fetch_json(url: str, timeout: int = 30) -> dict:
    if not url.startswith("https://"):
        raise RevocationError(f"URL não HTTPS recusada: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RevocationError("Resposta JSON excede 8 MiB")
    except (urllib.error.URLError, OSError) as exc:
        raise RevocationError(f"Falha de rede ao consultar {url}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevocationError(f"JSON inválido de {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RevocationError(f"Resposta de {url} não é um objeto JSON")
    return data


def state_path(root: Path) -> Path:
    return Path(root) / "state" / "revocation.json"


def read_revocation_state(root: Path) -> dict | None:
    path = state_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevocationError(f"Estado local de revogação ilegível: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("sequence")) is not int or data["sequence"] < 1:
        raise RevocationError("Estado local de revogação inválido")
    return data


def write_revocation_state(root: Path, data: dict) -> None:
    plat._atomic(state_path(root), data)


def validate_revoked_document(doc: object, *, previous_sequence: int | None = None,
                              now: datetime | str | None = None) -> dict:
    if not isinstance(doc, dict):
        raise RevocationError("revoked.json deve ser um objeto JSON")
    if doc.get("schema") != SCHEMA_REVOKED:
        raise RevocationError(f"Schema inválido em revoked.json: {doc.get('schema')!r}")
    seq = doc.get("sequence")
    if type(seq) is not int or seq < 1:
        raise RevocationError(f"Sequência inválida em revoked.json: {seq!r}")
    issued_at = _parse_utc(doc.get("issued_at"))
    expires_at = _parse_utc(doc.get("expires_at"))
    now_dt = _ensure_utc(now)
    if issued_at > expires_at:
        raise RevocationError("issued_at posterior a expires_at em revoked.json")
    if expires_at <= now_dt:
        raise RevocationError(f"Documento revoked.json vencido em {doc.get('expires_at')}")
    if previous_sequence is not None and seq < previous_sequence:
        raise RevocationError(f"Sequência regressiva em revoked.json: {seq} < {previous_sequence}")
    revoked = doc.get("revoked")
    if not isinstance(revoked, list):
        raise RevocationError("Campo 'revoked' deve ser uma lista")
    for item in revoked:
        if not isinstance(item, dict):
            raise RevocationError("Entrada inválida em 'revoked'")
        version = item.get("version")
        hashes = item.get("sha256")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise RevocationError("Versão inválida em entrada de 'revoked'")
        if hashes is not None:
            if not isinstance(hashes, list) or any(not isinstance(h, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", h) for h in hashes):
                raise RevocationError("Lista sha256 inválida em entrada de 'revoked'")
        if version is None and not hashes:
            raise RevocationError("Entrada de 'revoked' deve especificar version ou sha256")
    return doc


def _bundle_matches_identity(entry: dict, repo: str, workflow: str = REVOCATION_WORKFLOW,
                             ref: str = REVOCATION_REF) -> bool:
    if not isinstance(entry, dict):
        return False
    expected_wf = f"{repo}/{workflow.lstrip('/')}"
    bundle = entry.get("bundle") if isinstance(entry.get("bundle"), dict) else entry
    # 1. Explicit fields in bundle or entry (structured representation)
    for container in (entry, bundle):
        wf = container.get("signer_workflow") or container.get("workflow")
        r = container.get("ref") or container.get("source_ref")
        if isinstance(wf, str) and isinstance(r, str):
            if (wf == expected_wf or wf.endswith(f"/{expected_wf}")) and r == ref:
                return True
    # 2. X.509 certificate rawBytes in verificationMaterial
    vm = bundle.get("verificationMaterial")
    if isinstance(vm, dict):
        certs = []
        if isinstance(vm.get("certificate"), dict) and isinstance(vm["certificate"].get("rawBytes"), str):
            certs.append(vm["certificate"]["rawBytes"])
        chain = vm.get("x509CertificateChain")
        if isinstance(chain, dict) and isinstance(chain.get("certificates"), list):
            for c in chain["certificates"]:
                if isinstance(c, dict) and isinstance(c.get("rawBytes"), str):
                    certs.append(c["rawBytes"])
        for raw_b64 in certs:
            try:
                der = base64.b64decode(raw_b64)
            except Exception:
                continue
            text = der.decode("latin-1")
            if expected_wf in text and ref in text:
                return True
    # 3. DSSE payload (in-toto statement)
    dsse = bundle.get("dsseEnvelope")
    if isinstance(dsse, dict) and isinstance(dsse.get("payload"), str):
        try:
            payload_raw = base64.b64decode(dsse["payload"]).decode("utf-8")
            payload = json.loads(payload_raw)
            serialized = json.dumps(payload)
            if expected_wf in serialized and ref in serialized:
                return True
        except Exception:
            pass
    return False


def verify_revoked_attestation(
    raw_bytes: bytes,
    *,
    repo: str = DEFAULT_REPO,
    gh_cmd: str | bool | None = None,
    fetch_json: Callable[[str], dict] | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None = None,
) -> str:
    digest = hashlib.sha256(raw_bytes).hexdigest()
    signer_workflow = f"{repo}/{REVOCATION_WORKFLOW}"
    if gh_cmd is False:
        gh_exe = None
    elif isinstance(gh_cmd, str):
        gh_exe = gh_cmd
    else:
        gh_exe = shutil.which("gh")

    if gh_exe:
        runner = run_cmd or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60))
        with tempfile.TemporaryDirectory(prefix="traslado-rev-") as tmp:
            target = Path(tmp) / "revoked.json"
            target.write_bytes(raw_bytes)
            proc = runner([
                gh_exe,
                "attestation",
                "verify",
                str(target),
                "--repo",
                repo,
                "--signer-workflow",
                signer_workflow,
            ])
            if proc.returncode != 0:
                detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
                raise RevocationError(f"Attestation de revoked.json reprovada pelo gh: {detail}")
        return "gh"

    fetcher = fetch_json or default_fetch_json
    url = f"https://api.github.com/repos/{repo}/attestations/sha256:{digest}"
    data = fetcher(url)
    attestations = data.get("attestations")
    if not isinstance(attestations, list) or not attestations:
        raise RevocationError("Nenhuma attestation encontrada para revoked.json na API do GitHub")
    if not any(_bundle_matches_identity(item, repo, REVOCATION_WORKFLOW, REVOCATION_REF) for item in attestations):
        raise RevocationError("Nenhum bundle de attestation declara o workflow e a ref esperados")
    return "api-presence"


def check_not_revoked(doc: dict, version: str, expected_digests: dict[str, str] | None = None) -> None:
    clean_version = version.removeprefix("v")
    wanted_hashes = {
        v.lower().removeprefix("sha256:")
        for v in (expected_digests or {}).values()
        if isinstance(v, str)
    }
    for entry in doc.get("revoked", []):
        entry_version = (entry.get("version") or "").removeprefix("v")
        entry_hashes = {
            h.lower().removeprefix("sha256:")
            for h in entry.get("sha256", [])
            if isinstance(h, str)
        }
        if (entry_version and entry_version == clean_version) or (wanted_hashes and (wanted_hashes & entry_hashes)):
            reason = entry.get("reason") or "revogada pelo titular"
            raise RevocationError(f"Versão ou artefato revogado ({clean_version}): {reason}")


def verify_release_channel(release_data: dict, version: str,
                           expected_digests: dict[str, str] | None = None) -> None:
    if not isinstance(release_data, dict):
        raise RevocationError("Resposta inválida da API de releases")
    if release_data.get("draft") is not False:
        raise RevocationError("Release na API está em rascunho (draft)")
    if release_data.get("prerelease") is not False:
        raise RevocationError("Release na API é pré-release")
    expected_tag = f"v{version.removeprefix('v')}"
    if release_data.get("tag_name") != expected_tag:
        raise RevocationError(f"tag_name na API ({release_data.get('tag_name')!r}) diverge de {expected_tag!r}")
    assets_list = release_data.get("assets")
    if not isinstance(assets_list, list):
        raise RevocationError("Lista de assets ausente na API de releases")
    by_name = {a.get("name"): a for a in assets_list if isinstance(a, dict) and isinstance(a.get("name"), str)}
    if expected_digests:
        for name, expected_sha in expected_digests.items():
            norm_expected = expected_sha.lower().removeprefix("sha256:")
            asset = by_name.get(name)
            if asset is None:
                raise RevocationError(f"Asset {name!r} ausente na release publicada {expected_tag}")
            actual_digest = (asset.get("digest") or "").lower()
            if actual_digest != f"sha256:{norm_expected}":
                raise RevocationError(
                    f"Digest de {name!r} na API ({actual_digest!r}) diverge do esperado ('sha256:{norm_expected}')"
                )


def verify_promotion(
    root: Path,
    version: str,
    expected_digests: dict[str, str] | None = None,
    *,
    repo: str = DEFAULT_REPO,
    now: datetime | str | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
    fetch_json: Callable[[str], dict] | None = None,
    gh_cmd: str | bool | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    root = Path(root)
    bytes_fetcher = fetch_bytes or default_fetch_bytes
    json_fetcher = fetch_json or default_fetch_json
    now_dt = _ensure_utc(now)

    prev_state = read_revocation_state(root)
    prev_seq = prev_state["sequence"] if prev_state else None

    revoked_url = f"https://github.com/{repo}/releases/download/{REVOCATION_TAG}/revoked.json"
    raw_revoked = bytes_fetcher(revoked_url)
    mode = verify_revoked_attestation(
        raw_revoked,
        repo=repo,
        gh_cmd=gh_cmd,
        fetch_json=json_fetcher,
        run_cmd=run_cmd,
    )
    try:
        doc = json.loads(raw_revoked.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevocationError(f"revoked.json não é JSON válido: {exc}") from exc

    validate_revoked_document(doc, previous_sequence=prev_seq, now=now_dt)

    # Persist monotonic sequence state as soon as revoked.json is authenticated and valid.
    new_state = {
        "schema": SCHEMA_STATE,
        "sequence": doc["sequence"],
        "issued_at": doc["issued_at"],
        "expires_at": doc["expires_at"],
        "verified_at": _format_utc(now_dt),
        "mode": mode,
        "attestation_mode": mode,
        "revoked_sha256": hashlib.sha256(raw_revoked).hexdigest(),
    }
    write_revocation_state(root, new_state)

    check_not_revoked(doc, version, expected_digests)

    clean_version = version.removeprefix("v")
    release_url = f"https://api.github.com/repos/{repo}/releases/tags/v{clean_version}"
    release_data = json_fetcher(release_url)
    verify_release_channel(release_data, clean_version, expected_digests)

    return {
        "status": "ok",
        "sequence": doc["sequence"],
        "mode": mode,
        "state": new_state,
    }


def issue_revoked_document(
    source_path: Path,
    *,
    previous_sequence: int = 0,
    now: datetime | str | None = None,
    ttl_days: int = 14,
) -> dict:
    raw = json.loads(Path(source_path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA_REVOKED:
        raise RevocationError("Arquivo base de revogação inválido")
    base_seq = raw.get("sequence", 0)
    if type(base_seq) is not int or base_seq < 0:
        raise RevocationError("Sequência base inválida")
    next_seq = max(base_seq, previous_sequence) + 1
    now_dt = _ensure_utc(now)
    expires_dt = now_dt + timedelta(days=ttl_days)
    doc = {
        "schema": SCHEMA_REVOKED,
        "sequence": next_seq,
        "issued_at": _format_utc(now_dt),
        "expires_at": _format_utc(expires_dt),
        "revoked": list(raw.get("revoked", [])),
    }
    validate_revoked_document(doc, previous_sequence=previous_sequence if previous_sequence >= 1 else None, now=now_dt)
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emite ou valida revoked.json")
    parser.add_argument("--input", type=Path, required=True, help="Caminho de revocation/revoked-list.json")
    parser.add_argument("--output", type=Path, required=True, help="Caminho de saída para revoked.json")
    parser.add_argument("--previous-sequence", type=int, default=0)
    parser.add_argument("--ttl-days", type=int, default=14)
    args = parser.parse_args(argv)
    doc = issue_revoked_document(args.input, previous_sequence=args.previous_sequence, ttl_days=args.ttl_days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
