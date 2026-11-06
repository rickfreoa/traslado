# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Validação do formato de ci/quarentena.json e do runner com faulthandler e quarentena."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.run_tests import (
    validate_quarantine_entry,
    load_and_validate_quarantine,
    get_quarantined_tests_for_cell,
    is_test_quarantined,
    TimeoutTestResult,
    FORBIDDEN_QUARANTINE_TESTS,
)

ROOT = Path(__file__).resolve().parent.parent


class QuarentenaContractTests(unittest.TestCase):
    def test_arquivo_quarentena_json_valido_e_inicialmente_vazio(self):
        caminho = ROOT / "ci" / "quarentena.json"
        self.assertTrue(caminho.is_file(), f"Arquivo ausente: {caminho}")
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        self.assertIsInstance(dados, list)
        self.assertEqual(len(dados), 0, "ci/quarentena.json deve iniciar vazio no item D")
        # Validação do arquivo real
        entradas = load_and_validate_quarantine(caminho)
        self.assertEqual(entradas, [])

    def test_validador_falha_se_entrada_nao_tiver_issue(self):
        sem_issue = {
            "teste": "tests.test_detect_e3.DetectionE3.test_supported_formats_dispatch_by_content",
            "celula": {"os": "macos-15", "python": "3.14", "perfil": "full"},
            "motivo": "Travamento sob investigação",
        }
        with self.assertRaisesRegex(ValueError, "issue"):
            validate_quarantine_entry(sem_issue)

        issue_vazia = dict(sem_issue, issue="   ")
        with self.assertRaisesRegex(ValueError, "issue"):
            validate_quarantine_entry(issue_vazia)

    def test_validador_recusa_quarentena_de_test_execute_failure_matrix(self):
        for proibido in FORBIDDEN_QUARANTINE_TESTS:
            entrada = {
                "teste": proibido,
                "celula": {"os": "macos-15", "python": "3.14", "perfil": "full"},
                "issue": "https://github.com/rickfreoa/traslado/issues/999",
                "motivo": "Tentativa indevida de quarentenar teste bloqueante",
            }
            with self.assertRaisesRegex(ValueError, "permanece bloqueante"):
                validate_quarantine_entry(entrada)

    def test_validador_valida_campos_obrigatorios(self):
        valida = {
            "teste": "tests.test_detect_e3.DetectionE3.test_supported_formats_dispatch_by_content",
            "celula": {"os": "macos-15", "python": "3.14", "perfil": "full"},
            "issue": "https://github.com/rickfreoa/traslado/issues/100",
            "motivo": "Investigação de travamento esporádico no CI",
        }
        resultado = validate_quarantine_entry(valida)
        self.assertEqual(resultado["teste"], valida["teste"])

        # Falta célula
        with self.assertRaisesRegex(ValueError, "celula"):
            validate_quarantine_entry({**valida, "celula": {}})
        # Falta teste
        with self.assertRaisesRegex(ValueError, "teste"):
            validate_quarantine_entry({**valida, "teste": ""})
        # Falta motivo
        with self.assertRaisesRegex(ValueError, "motivo"):
            validate_quarantine_entry({**valida, "motivo": ""})

    def test_quarentena_exclui_teste_bloqueante_somente_na_celula_indicada(self):
        entradas = [
            {
                "teste": "tests.test_detect_e3.DetectionE3.test_supported_formats_dispatch_by_content",
                "celula": {"os": "macos-15", "python": "3.14", "perfil": "full"},
                "issue": "https://github.com/rickfreoa/traslado/issues/100",
                "motivo": "Travamento no macOS py3.14",
            }
        ]
        test_id = "tests.test_detect_e3.DetectionE3.test_supported_formats_dispatch_by_content"

        # Na célula indicada (macos-15, 3.14, full): quarentenado
        celula_macos = {"os": "macos-15", "python": "3.14", "perfil": "full"}
        self.assertTrue(is_test_quarantined(test_id, celula_macos, entradas))
        self.assertIn(test_id, get_quarantined_tests_for_cell(celula_macos, entradas))

        # Em outra célula (ubuntu-latest, 3.14, full): NÃO quarentenado
        celula_ubuntu = {"os": "ubuntu-latest", "python": "3.14", "perfil": "full"}
        self.assertFalse(is_test_quarantined(test_id, celula_ubuntu, entradas))
        self.assertNotIn(test_id, get_quarantined_tests_for_cell(celula_ubuntu, entradas))

        # Em outro python na mesma máquina (macos-15, 3.13, full): NÃO quarentenado
        celula_macos_313 = {"os": "macos-15", "python": "3.13", "perfil": "full"}
        self.assertFalse(is_test_quarantined(test_id, celula_macos_313, entradas))

        # test_execute_failure_matrix NUNCA é quarentenado em nenhuma célula
        for proibido in FORBIDDEN_QUARANTINE_TESTS:
            self.assertFalse(is_test_quarantined(proibido, celula_macos, entradas))

    def test_timeout_test_result_chama_faulthandler_no_inicio_e_fim_do_teste(self):
        result = TimeoutTestResult(stream=None, descriptions=True, verbosity=1, per_test_timeout=600.0)
        case = unittest.FunctionTestCase(lambda: None)

        with patch("scripts.run_tests.faulthandler.dump_traceback_later") as mock_arm, \
             patch("scripts.run_tests.faulthandler.cancel_dump_traceback_later") as mock_cancel:
            result.startTest(case)
            mock_arm.assert_called_once_with(600.0, repeat=False, exit=True)
            result.stopTest(case)
            mock_cancel.assert_called_once()
