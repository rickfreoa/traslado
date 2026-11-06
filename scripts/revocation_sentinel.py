#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Sentinela de revogação do Traslado.

Monitora a publicação do documento revoked.json na release fixa 'revocation' e gerencia
issues no repositório com o rótulo 'revogacao' quando o documento estiver prestes a
expirar (< 7 dias) ou quando a última emissão tiver mais de 36 horas. Fecha a issue quando
a condição normaliza.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "rickfreoa/traslado"
ISSUE_LABEL = "revogacao"
ISSUE_TITLE = "Sentinela de revogação: revoked.json requer atenção"
USER_AGENT = "Traslado-Revocation-Sentinel/2.0.1"


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise ValueError(f"Timestamp UTC inválido: {value!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ensure_utc(instant: datetime | str | None = None) -> datetime:
    if instant is None:
        return datetime.now(timezone.utc)
    if isinstance(instant, str):
        return _parse_utc(instant)
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def check_revocation_health(doc: dict, now: datetime | str | None = None) -> tuple[bool, list[str]]:
    now_dt = _ensure_utc(now)
    expires_at_str = doc.get("expires_at")
    issued_at_str = doc.get("issued_at")

    if not expires_at_str or not issued_at_str:
        return True, ["Documento revoked.json não contém campos de data válidos (issued_at/expires_at)."]

    try:
        expires_at = _parse_utc(expires_at_str)
        issued_at = _parse_utc(issued_at_str)
    except ValueError as exc:
        return True, [f"Falha ao interpretar datas do documento revoked.json: {exc}"]

    alerts: list[str] = []

    # Condição 1: faltam menos de 7 dias para expires_at
    time_to_expire = expires_at - now_dt
    if time_to_expire < timedelta(days=7):
        dias_restantes = time_to_expire.total_seconds() / 86400
        alerts.append(
            f"Faltam menos de 7 dias para a expiração ({dias_restantes:.1f} dias restantes, expira em {expires_at_str})."
        )

    # Condição 2: issued_at tem mais de 36 h
    age_since_issued = now_dt - issued_at
    if age_since_issued > timedelta(hours=36):
        horas_desde_emissao = age_since_issued.total_seconds() / 3600
        alerts.append(
            f"Última emissão tem mais de 36 h ({horas_desde_emissao:.1f} horas decorridas desde {issued_at_str})."
        )

    return len(alerts) > 0, alerts


class IssueClient:
    def list_open_issues(self, repo: str, label: str) -> list[dict]:
        raise NotImplementedError

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int:
        raise NotImplementedError

    def update_issue(self, repo: str, issue_number: int, body: str) -> None:
        raise NotImplementedError

    def close_issue(self, repo: str, issue_number: int, comment: str) -> None:
        raise NotImplementedError


class GhCliIssueClient(IssueClient):
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.gh = shutil.which("gh")

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.token:
            env["GH_TOKEN"] = self.token
        return env

    def list_open_issues(self, repo: str, label: str) -> list[dict]:
        if not self.gh:
            raise RuntimeError("GitHub CLI ('gh') não encontrado no PATH.")
        cmd = [
            self.gh, "issue", "list",
            "--repo", repo,
            "--label", label,
            "--state", "open",
            "--json", "number,title,body,labels",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=self._env(), check=True)
        data = json.loads(res.stdout or "[]")
        issues = []
        for item in data:
            labels = [lbl.get("name") if isinstance(lbl, dict) else str(lbl) for lbl in item.get("labels", [])]
            issues.append({
                "number": item["number"],
                "title": item.get("title", ""),
                "body": item.get("body", ""),
                "labels": labels,
                "state": "open",
            })
        return issues

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int:
        if not self.gh:
            raise RuntimeError("GitHub CLI ('gh') não encontrado no PATH.")
        cmd = [
            self.gh, "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body", body,
        ]
        for lbl in labels:
            cmd.extend(["--label", lbl])
        res = subprocess.run(cmd, capture_output=True, text=True, env=self._env(), check=True)
        # Saída do gh issue create é a URL da issue criada, e.g. https://github.com/repo/issues/123
        match = re.search(r"/issues/(\d+)", res.stdout)
        if match:
            return int(match.group(1))
        return 0

    def update_issue(self, repo: str, issue_number: int, body: str) -> None:
        if not self.gh:
            raise RuntimeError("GitHub CLI ('gh') não encontrado no PATH.")
        cmd = [
            self.gh, "issue", "edit", str(issue_number),
            "--repo", repo,
            "--body", body,
        ]
        subprocess.run(cmd, capture_output=True, text=True, env=self._env(), check=True)

    def close_issue(self, repo: str, issue_number: int, comment: str) -> None:
        if not self.gh:
            raise RuntimeError("GitHub CLI ('gh') não encontrado no PATH.")
        cmd = [
            self.gh, "issue", "close", str(issue_number),
            "--repo", repo,
            "--comment", comment,
        ]
        subprocess.run(cmd, capture_output=True, text=True, env=self._env(), check=True)


def build_issue_body(doc: dict | None, alerts: list[str]) -> str:
    if doc and isinstance(doc, dict):
        seq = doc.get("sequence", "?")
        issued = doc.get("issued_at", "?")
        expires = doc.get("expires_at", "?")
        dados_doc = (
            f"- **Sequência atual:** {seq}\n"
            f"- **Data de emissão (issued_at):** `{issued}`\n"
            f"- **Data de expiração (expires_at):** `{expires}`\n\n"
        )
    else:
        dados_doc = "- **Estado do documento:** indisponível ou inválido na release fixa `revocation`.\n\n"

    motivos = "\n".join(f"- {a}" for a in alerts)
    return (
        f"## Alerta da Sentinela de Revogação\n\n"
        f"O documento `revoked.json` da release fixa `revocation` requer atenção operacional:\n\n"
        f"{dados_doc}"
        f"### Motivos do alerta:\n"
        f"{motivos}\n\n"
        f"---\n"
        f"*Esta issue é gerenciada automaticamente pelo workflow de sentinela (`revocation-sentinel.yml`). "
        f"Será fechada automaticamente assim que um novo documento for publicado e a condição for normalizada.*"
    )


def process_sentinel(
    doc: dict | None,
    issue_client: IssueClient,
    repo: str,
    now: datetime | str | None = None,
    error_alert: str | None = None,
) -> dict:
    if error_alert:
        is_alert = True
        alerts = [error_alert]
    else:
        is_alert, alerts = check_revocation_health(doc or {}, now=now)

    open_issues = issue_client.list_open_issues(repo, ISSUE_LABEL)

    if is_alert:
        body = build_issue_body(doc, alerts)
        if open_issues:
            target = open_issues[0]
            issue_client.update_issue(repo, target["number"], body)
            return {"action": "updated", "issue": target["number"], "alerts": alerts}
        else:
            issue_num = issue_client.create_issue(repo, ISSUE_TITLE, body, [ISSUE_LABEL])
            return {"action": "created", "issue": issue_num, "alerts": alerts}
    else:
        if open_issues:
            closed = []
            comment = (
                "Condição de alerta normalizada: documento `revoked.json` atualizado na release `revocation`, "
                "emitido há menos de 36 h e com mais de 7 dias de validade restante. Fechando issue."
            )
            for iss in open_issues:
                issue_client.close_issue(repo, iss["number"], comment)
                closed.append(iss["number"])
            return {"action": "closed", "closed_issues": closed}
        return {"action": "noop", "status": "healthy"}


def fetch_revoked_doc_from_release(repo: str) -> dict:
    url = f"https://github.com/{repo}/releases/download/revocation/revoked.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Falha ao baixar revoked.json de {url}: {exc}") from exc
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"revoked.json recebido não é JSON válido: {exc}") from exc


def main(argv: list[str] | None = None, client: IssueClient | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentinela de revogação do Traslado")
    parser.add_argument("--input", type=Path, default=None, help="Caminho local para revoked.json (se omitido, baixa da release)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO), help="Repositório GitHub")
    parser.add_argument("--now", default=None, help="Timestamp UTC simulado")
    parser.add_argument("--dry-run", action="store_true", help="Não altera issues no GitHub")
    args = parser.parse_args(argv)

    doc: dict | None = None
    fetch_error: str | None = None

    if args.input:
        if not args.input.is_file():
            fetch_error = f"Arquivo local informado não existe: {args.input}"
        else:
            try:
                doc = json.loads(args.input.read_text(encoding="utf-8-sig"))
                if not isinstance(doc, dict):
                    fetch_error = f"Documento em {args.input} não é um objeto JSON válido."
            except Exception as exc:
                fetch_error = f"Falha ao interpretar documento JSON em {args.input}: {exc}"
    else:
        try:
            doc = fetch_revoked_doc_from_release(args.repo)
            if not isinstance(doc, dict):
                fetch_error = "Documento baixado da release 'revocation' não é um objeto JSON válido."
        except Exception as exc:
            fetch_error = f"Indisponibilidade ou falha no download de revoked.json: {exc}"

    if args.dry_run:
        if fetch_error:
            print(f"Alerta ativo (erro de obtenção): {fetch_error}")
            return 1
        is_alert, alerts = check_revocation_health(doc or {}, now=args.now)
        print(f"Alerta ativo: {is_alert}")
        for a in alerts:
            print(f"- {a}")
        return 1 if is_alert else 0

    if client is None:
        client = GhCliIssueClient()

    if fetch_error:
        result = process_sentinel(None, client, args.repo, now=args.now, error_alert=fetch_error)
        print(f"Sentinela registrou alerta operacional de indisponibilidade/invalidez: {result}")
        return 1

    result = process_sentinel(doc, client, args.repo, now=args.now)
    print(f"Sentinela executada com sucesso: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
