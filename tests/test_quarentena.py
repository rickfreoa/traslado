# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Validação do formato de ci/quarentena.json e do runner com faulthandler e quarentena."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts.run_tests import (
    validate_quarantine_entry,
    load_and_validate_quarantine,
    get_quarantined_tests_for_cell,
    is_test_quarantined,
    TimeoutTestResult,
    FORBIDDEN_QUARANTINE_TESTS,
)


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

    def test_runner_carrega_modulo_com_prefixo_tests_com_pythonpath_src(self):
        """O runner deve encontrar a raiz de 'tests' mesmo quando chamado com PYTHONPATH=src."""
        import subprocess
        import sys
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "run_tests.py"),
            "-v",
            "tests.test_quarentena.QuarentenaContractTests.test_arquivo_quarentena_json_valido_e_inicialmente_vazio",
        ]
        env = {**os.environ, "PYTHONPATH": "src"}
        res = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, f"Falha ao executar runner: {res.stdout}\n{res.stderr}")
        self.assertNotIn("ModuleNotFoundError: No module named 'tests'", res.stdout + res.stderr)

    def test_import_scripts_funciona_com_raiz_do_pacote_isolada(self):
        """test_quarentena e test_revocation_sentinel devem encontrar scripts em ambiente de pacote instalado."""
        import shutil
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory(prefix="traslado-pkg-") as tmp:
            pkg = Path(tmp)
            (pkg / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts" / "run_tests.py", pkg / "scripts" / "run_tests.py")
            shutil.copyfile(ROOT / "scripts" / "revocation_sentinel.py", pkg / "scripts" / "revocation_sentinel.py")
            (pkg / "tests").mkdir()
            shutil.copyfile(ROOT / "tests" / "test_quarentena.py", pkg / "tests" / "test_quarentena.py")
            shutil.copyfile(ROOT / "tests" / "test_revocation_sentinel.py", pkg / "tests" / "test_revocation_sentinel.py")
            (pkg / "tests" / "__init__.py").write_text("", encoding="utf-8")

            code = (
                "import sys\n"
                f"sys.path = [r'{pkg / 'tests'}', r'{pkg}'] + [p for p in sys.path if 'wt30' not in p and p != '']\n"
                "import test_quarentena\n"
                "import test_revocation_sentinel\n"
                "print('OK')\n"
            )
            res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 0, f"Falha na importação isolada: {res.stdout}\n{res.stderr}")
            self.assertIn("OK", res.stdout)

    def test_core_runner_aplica_timeout_test_result(self):
        """O modo core deve usar TimeoutTestResult com faulthandler e timeout por teste."""
        import io
        import tempfile
        from tests import test_release_t11

        with tempfile.TemporaryDirectory(prefix="core-timeout-") as tmp:
            pasta = Path(tmp)
            (pasta / "core-skips.txt").write_text("", encoding="utf-8")
            (pasta / "test_fake.py").write_text(
                "import unittest\nclass FakeT(unittest.TestCase):\n    def test_ok(self): pass\n",
                encoding="utf-8",
            )
            fluxo = io.StringIO()
            with patch("scripts.run_tests.faulthandler.dump_traceback_later") as mock_arm:
                rc = test_release_t11.executar_core(pasta, opcionais=set(), fluxo=fluxo, timeout=600.0)
                self.assertEqual(rc, 0)
                mock_arm.assert_called()

    def test_conftest_pytest_arma_e_cancela_faulthandler(self):
        """tests/conftest.py deve armar dump_traceback_later(600) e cancelar ao fim de cada teste."""
        from tests import conftest
        with patch("tests.conftest.faulthandler.dump_traceback_later") as mock_arm, \
             patch("tests.conftest.faulthandler.cancel_dump_traceback_later") as mock_cancel:
            gen = conftest.pytest_runtest_protocol(None, None)
            next(gen)
            mock_arm.assert_called_once_with(600.0, repeat=False, exit=True)
            try:
                next(gen)
            except StopIteration:
                pass
            mock_cancel.assert_called_once()




