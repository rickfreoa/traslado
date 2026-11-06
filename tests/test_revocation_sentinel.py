# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Testes unitários da sentinela de revogação sobre datas simuladas e ciclo de vida de issues."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts.revocation_sentinel import (
    check_revocation_health,
    process_sentinel,
    IssueClient,
    ISSUE_LABEL,
)


class FakeIssueClient(IssueClient):
    def __init__(self, initial_issues=None):
        self.issues = list(initial_issues or [])
        self.created = []
        self.updated = []
        self.closed = []
        self._next_id = 100

    def list_open_issues(self, repo: str, label: str) -> list[dict]:
        return [i for i in self.issues if i.get("state") == "open" and label in i.get("labels", [])]

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int:
        self._next_id += 1
        issue = {
            "number": self._next_id,
            "title": title,
            "body": body,
            "labels": list(labels),
            "state": "open",
        }
        self.issues.append(issue)
        self.created.append(issue)
        return self._next_id

    def update_issue(self, repo: str, issue_number: int, body: str) -> None:
        for i in self.issues:
            if i["number"] == issue_number:
                i["body"] = body
                self.updated.append((issue_number, body))
                return
        raise KeyError(f"Issue {issue_number} não encontrada")

    def close_issue(self, repo: str, issue_number: int, comment: str) -> None:
        for i in self.issues:
            if i["number"] == issue_number:
                i["state"] = "closed"
                self.closed.append((issue_number, comment))
                return
        raise KeyError(f"Issue {issue_number} não encontrada")


class RevocationSentinelTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

    def _make_doc(self, issued_offset_hours: float, expires_offset_days: float) -> dict:
        issued_at = self.now + timedelta(hours=issued_offset_hours)
        expires_at = self.now + timedelta(days=expires_offset_days)
        return {
            "schema": "traslado-revoked-1",
            "sequence": 42,
            "issued_at": issued_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "revoked": [],
        }

    def test_documento_saudavel_nao_dispara_alerta(self):
        # Emitido há 12 h, expira em 13.5 dias -> saudável
        doc = self._make_doc(issued_offset_hours=-12, expires_offset_days=13.5)
        is_alert, alerts = check_revocation_health(doc, now=self.now)
        self.assertFalse(is_alert)
        self.assertEqual(alerts, [])

        client = FakeIssueClient()
        res = process_sentinel(doc, client, "fake/repo", now=self.now)
        self.assertEqual(res["action"], "noop")
        self.assertEqual(len(client.created), 0)

    def test_alerta_quando_faltam_menos_de_7_dias_para_expirar(self):
        # Emitido há 12 h, mas expira em 6.9 dias (< 7 dias)
        doc = self._make_doc(issued_offset_hours=-12, expires_offset_days=6.9)
        is_alert, alerts = check_revocation_health(doc, now=self.now)
        self.assertTrue(is_alert)
        self.assertTrue(any("7 dias" in a for a in alerts))

        client = FakeIssueClient()
        res = process_sentinel(doc, client, "fake/repo", now=self.now)
        self.assertEqual(res["action"], "created")
        self.assertEqual(len(client.created), 1)
        self.assertIn(ISSUE_LABEL, client.created[0]["labels"])
        self.assertIn("7 dias", client.created[0]["body"])

    def test_alerta_quando_issued_at_tem_mais_de_36_horas(self):
        # Emitido há 36.5 h (> 36 h), expira em 12 dias
        doc = self._make_doc(issued_offset_hours=-36.5, expires_offset_days=12.0)
        is_alert, alerts = check_revocation_health(doc, now=self.now)
        self.assertTrue(is_alert)
        self.assertTrue(any("36" in a for a in alerts))

        client = FakeIssueClient()
        res = process_sentinel(doc, client, "fake/repo", now=self.now)
        self.assertEqual(res["action"], "created")
        self.assertEqual(len(client.created), 1)
        self.assertIn("36", client.created[0]["body"])

    def test_ambas_as_condicoes_acionadas_reportam_ambos_os_motivos(self):
        # Emitido há 48 h e expira em 5 dias
        doc = self._make_doc(issued_offset_hours=-48, expires_offset_days=5.0)
        is_alert, alerts = check_revocation_health(doc, now=self.now)
        self.assertTrue(is_alert)
        self.assertEqual(len(alerts), 2)

        client = FakeIssueClient()
        res = process_sentinel(doc, client, "fake/repo", now=self.now)
        self.assertEqual(res["action"], "created")
        self.assertIn("36", client.created[0]["body"])
        self.assertIn("7 dias", client.created[0]["body"])

    def test_atualiza_issue_existente_sem_duplicar(self):
        doc = self._make_doc(issued_offset_hours=-40, expires_offset_days=10.0)
        client = FakeIssueClient(initial_issues=[{
            "number": 88,
            "title": "Alerta anterior",
            "body": "corpo antigo",
            "labels": [ISSUE_LABEL],
            "state": "open",
        }])

        res = process_sentinel(doc, client, "fake/repo", now=self.now)
        self.assertEqual(res["action"], "updated")
        self.assertEqual(res["issue"], 88)
        self.assertEqual(len(client.created), 0)
        self.assertEqual(len(client.updated), 1)
        self.assertEqual(client.updated[0][0], 88)

    def test_fecha_issue_quando_condicao_desaparece(self):
        # Documento saudável
        doc = self._make_doc(issued_offset_hours=-6, expires_offset_days=13.0)
        client = FakeIssueClient(initial_issues=[{
            "number": 88,
            "title": "Alerta anterior",
            "body": "corpo antigo",
            "labels": [ISSUE_LABEL],
            "state": "open",
        }])

        res = process_sentinel(doc, client, "fake/repo", now=self.now)
        self.assertEqual(res["action"], "closed")
        self.assertEqual(res["closed_issues"], [88])
        self.assertEqual(len(client.closed), 1)
        self.assertEqual(client.closed[0][0], 88)
        self.assertIn("normalizada", client.closed[0][1].lower())
