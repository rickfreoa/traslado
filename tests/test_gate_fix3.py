# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Regressões da emissão e do encerramento dos testes do CI."""
import importlib.util
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def pasta_temporaria():
    # Permissões herdadas também permitem executar sob perfil restrito no Windows.
    path = Path(tempfile.gettempdir()) / ('gate-fix3-' + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


class GateFix3(unittest.TestCase):
    def subprocesso(self, code):
        return subprocess.run(
            [sys.executable, '-I', '-X', 'utf8', '-X', 'faulthandler', '-c',
             f'import sys; sys.path.append({str(ROOT)!r})\n'
             'from tests.test_gate_fix3 import pasta_temporaria\n' + textwrap.dedent(code)],
            capture_output=True, text=True, encoding='utf-8', timeout=8, cwd=ROOT,
        )

    def assert_interrompido(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Timeout (', result.stderr)
        self.assertIn('File ', result.stderr)
        self.assertNotIn('FIM_INDEVIDO', result.stdout)

    def test_correcao_de_espaco_na_versao_reemite_e_revoga(self):
        from traslado import revocation as r
        with pasta_temporaria() as tmp:
            source = Path(tmp) / 'lista.json'
            raw = {'schema': r.SCHEMA_REVOKED, 'sequence': 1,
                   'revoked': [{'version': '1.0.0 ', 'reason': 'correcao'}]}
            source.write_text(json.dumps(raw), encoding='utf-8')
            previous = r.issue_revoked_document(source, now='2026-11-06T10:00:00Z')
            r.check_not_revoked(previous, '1.0.0')
            raw['revoked'][0]['version'] = '1.0.0'
            source.write_text(json.dumps(raw), encoding='utf-8')
            issued = r.issue_revoked_document(source, previous_document=previous,
                                             now='2026-11-06T18:00:00Z')
            self.assertIsNotNone(issued)
            self.assertEqual(issued['sequence'], previous['sequence'] + 1)
            with self.assertRaises(r.RevocationError):
                r.check_not_revoked(issued, '1.0.0')

    def test_pytest_ci_exige_encerramento_nativo_sem_timer_concorrente(self):
        from types import SimpleNamespace
        from tests import conftest
        self.assertFalse(hasattr(conftest, 'pytest_runtest_protocol'))
        config = SimpleNamespace(inicfg={})
        conftest.pytest_configure(config)
        self.assertEqual(config.inicfg['faulthandler_timeout'], '600')
        self.assertEqual(config.inicfg['faulthandler_exit_on_timeout'], 'true')

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest ausente no ambiente')
    def test_pytest_timeout_emite_traceback_e_encerra(self):
        import pytest
        if pytest.__version__ != '9.1.1':
            self.skipTest('prova exige pytest 9.1.1 fixado no CI')
        with pasta_temporaria() as tmp:
            test = Path(tmp) / 'test_travado.py'
            shutil.copyfile(ROOT / 'tests/conftest.py', Path(tmp) / 'conftest.py')
            test.write_text('import time\ndef test_trava():\n    time.sleep(4)\n', encoding='utf-8')
            result = subprocess.run(
                [sys.executable, '-I', '-X', 'utf8', '-X', 'faulthandler', '-m', 'pytest', '-v',
                 '-o', 'faulthandler_timeout=0.3', '-o', 'faulthandler_exit_on_timeout=true', str(test)],
                capture_output=True, text=True, timeout=8,
            )
            self.assert_interrompido(result)

    def test_runner_restaura_prazo_restante_sem_reiniciar_contagem(self):
        result = self.subprocesso('''
            import io, time, unittest
            from unittest.mock import patch
            from scripts import run_tests as r
            case = unittest.FunctionTestCase(lambda: None)
            outer = r.TimeoutTestResult(io.StringIO(), True, 0, per_test_timeout=10)
            inner = r.TimeoutTestResult(io.StringIO(), True, 0, per_test_timeout=100)
            with patch.object(r, '_TIMEOUT_DEADLINES', [], create=True), \\
                 patch.object(time, 'monotonic', side_effect=[100, 102, 105]), \\
                 patch.object(r.faulthandler, 'dump_traceback_later') as arm, \\
                 patch.object(r.faulthandler, 'cancel_dump_traceback_later') as cancel:
                outer.startTest(case)
                inner.startTest(case)
                inner.stopTest(case)
                outer.stopTest(case)
                assert [call.args[0] for call in arm.call_args_list] == [10, 8, 5]
                assert all(call.kwargs == {'repeat': False, 'exit': True} for call in arm.call_args_list)
                assert cancel.call_count == 2
                assert not r._TIMEOUT_DEADLINES
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_contencao_usa_limite_e_preserva_relatorio_de_falha(self):
        result = self.subprocesso('''
            import io, json, types, unittest
            from pathlib import Path
            from unittest.mock import patch
            from scripts import contencao_e6 as c
            from scripts.run_tests import TimeoutTestResult
            class Falha(unittest.TestCase):
                def test_falha(self): self.fail('falha simulada')
            sys.modules['test_containment_e2'] = types.SimpleNamespace(ContainmentE2=Falha)
            with pasta_temporaria() as tmp:
                c.ROOT = Path(tmp)
                source = c.ROOT / 'tests/_medicoes' / ('contencao-' + sys.platform + '.json')
                source.parent.mkdir(parents=True)
                output = c.ROOT / 'saida.json'
                def suite(_):
                    source.write_text('{"medicao_preservada": 123}')
                    return unittest.TestSuite([Falha('test_falha')])
                original = unittest.TextTestRunner.run
                def run(runner, tests):
                    assert isinstance(runner._makeResult(), TimeoutTestResult)
                    return original(runner, tests)
                with patch.object(sys, 'argv', ['contencao_e6.py', '--saida', str(output)]), \\
                     patch.object(unittest.defaultTestLoader, 'loadTestsFromTestCase', side_effect=suite), \\
                     patch.object(unittest.TextTestRunner, 'run', run):
                    assert c.main() == 1
                report = json.loads(output.read_text())
                assert report['medicao_preservada'] == 123
                assert report['success'] is False
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_contencao_travada_emite_traceback_e_encerra(self):
        result = self.subprocesso('''
            import time, types, unittest
            from pathlib import Path
            from unittest.mock import patch
            from scripts import contencao_e6 as c, run_tests as r
            class Trava(unittest.TestCase):
                def test_trava(self): time.sleep(4)
            original = r.TimeoutTestResult.__init__
            def curto(self, *args, **kwargs):
                kwargs['per_test_timeout'] = 0.3
                original(self, *args, **kwargs)
            sys.modules['test_containment_e2'] = types.SimpleNamespace(ContainmentE2=Trava)
            with pasta_temporaria() as tmp:
                c.ROOT = Path(tmp)
                with patch.object(sys, 'argv', ['contencao_e6.py', '--saida', str(c.ROOT / 'saida.json')]), \\
                     patch.object(unittest.defaultTestLoader, 'loadTestsFromTestCase',
                                  return_value=unittest.TestSuite([Trava('test_trava')])), \\
                     patch.object(r.TimeoutTestResult, '__init__', curto):
                    c.main()
                print('FIM_INDEVIDO')
        ''')
        self.assert_interrompido(result)

    def test_runner_interno_restaura_prazo_externo_inclusive_core(self):
        for core in (False, True):
            with self.subTest(core=core):
                result = self.subprocesso(f'''
                    import io, tempfile, time, unittest
                    from pathlib import Path
                    from scripts.run_tests import run_tests_with_runner
                    class Externo(unittest.TestCase):
                        def test_externo(self):
                            if {core!r}:
                                from tests.test_release_t11 import executar_core
                                with pasta_temporaria() as tmp:
                                    pasta = Path(tmp)
                                    (pasta / 'test_interno.py').write_text(
                                        'import unittest\\nclass T(unittest.TestCase):\\n    def test_ok(self): pass\\n')
                                    assert executar_core(pasta, set(), io.StringIO(), timeout=2) == 0
                            else:
                                assert run_tests_with_runner(unittest.TestSuite([
                                    unittest.FunctionTestCase(lambda: time.sleep(0.1))]), timeout=2)
                            print('INTERNO_CONCLUIDO', flush=True)
                            time.sleep(4)
                            print('FIM_INDEVIDO')
                    run_tests_with_runner(unittest.TestSuite([Externo('test_externo')]), timeout=0.6)
                ''')
                self.assertIn('INTERNO_CONCLUIDO', result.stdout)
                self.assert_interrompido(result)
