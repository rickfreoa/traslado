# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Testes do estado de revogação e avisos estáveis no traslado doctor."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from traslado import cli, doctor
from traslado.revocation import SCHEMA_STATE, write_revocation_state


class DoctorRevocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doc-rev-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

    def test_doctor_sem_documento_aceito_informa_aviso_estavel(self):
        report = doctor.inspect(root=self.tmp, now=self.now)
        self.assertIsNone(report.get("revocation"))
        warnings = report.get("warnings", [])
        self.assertTrue(any("nenhum documento de revogação aceito" in w for w in warnings), warnings)

    def test_doctor_com_documento_valido_com_mais_de_7_dias(self):
        expires_dt = self.now + timedelta(days=12, hours=12)
        write_revocation_state(self.tmp, {
            "schema": SCHEMA_STATE,
            "sequence": 42,
            "issued_at": "2026-11-05T00:00:00Z",
            "expires_at": expires_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "verified_at": "2026-11-06T00:00:00Z",
            "mode": "api-presence",
            "attestation_mode": "api-presence",
            "revoked_sha256": "0" * 64,
        })

        report = doctor.inspect(root=self.tmp, now=self.now)
        rev = report.get("revocation")
        self.assertIsNotNone(rev)
        self.assertEqual(rev["sequence"], 42)
        self.assertAlmostEqual(rev["days_remaining"], 12.5, places=1)
        self.assertEqual(rev["dias_restantes"], rev["days_remaining"])
        warnings = report.get("warnings", [])
        self.assertFalse(any("Canal de revogação" in w for w in warnings), warnings)

    def test_doctor_com_menos_de_7_dias_exibe_aviso_estavel(self):
        expires_dt = self.now + timedelta(days=5, hours=6)
        write_revocation_state(self.tmp, {
            "schema": SCHEMA_STATE,
            "sequence": 43,
            "issued_at": "2026-10-25T00:00:00Z",
            "expires_at": expires_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "verified_at": "2026-10-25T00:00:00Z",
            "mode": "api-presence",
            "attestation_mode": "api-presence",
            "revoked_sha256": "0" * 64,
        })

        report = doctor.inspect(root=self.tmp, now=self.now)
        rev = report.get("revocation")
        self.assertIsNotNone(rev)
        self.assertEqual(rev["sequence"], 43)
        self.assertAlmostEqual(rev["days_remaining"], 5.25, places=1)
        warnings = report.get("warnings", [])
        self.assertTrue(any("Canal de revogação" in w and "7 dias" in w for w in warnings), warnings)

    def test_doctor_cli_codigo_de_saida_zero(self):
        # O código de saída do doctor continua 0 mesmo quando há avisos de revogação
        rc = cli.main(["doctor"])
        self.assertEqual(rc, 0)
