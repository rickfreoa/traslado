# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks for E6 scripts and local lifecycle transitions (no network)."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get('T11_EXIGE_INSTALADO'):
    sys.path.insert(0, str(ROOT / 'src'))
from traslado import lifecycle


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name+'.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScriptsE6(unittest.TestCase):
    def test_consolidation_missing_or_duplicate_evidence_never_passes(self):
        helper = script('resumir_contencao')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            table, complete = helper.consolidate(root)
            self.assertFalse(complete)
            self.assertEqual(table.count('NOT_RUN'), 36)
            (root/'contencao-one.json').write_text('{"platform":"win32"}', encoding='utf-8')
            (root/'contencao-two.json').write_text('{"platform":"win32"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'duplicada'):
                helper.consolidate(root)

    def test_allowlist_requires_prefix_and_separator(self):
        audit = script('auditar_libs')
        allowed = ['/lib/', '/install/']
        self.assertTrue(audit._is_permitted('/lib/libc.so', allowed, [], False)[0])
        for path in ('/tmp/lib/libc.so', '/installation/a.so', '/home/user/lib/a.so'):
            self.assertFalse(audit._is_permitted(path, allowed, [], False)[0], path)
        self.assertFalse(audit._is_permitted('/usr/local/lib/a.so', ['/usr/'], ['/usr/local/'], False)[0])
        self.assertTrue(audit._is_permitted('C:\\WINDOWS\\System32\\a.dll', ['C:\\Windows\\System32\\'], [], True)[0])

    def test_post1_wheel_metadata_record_and_checksum(self):
        helper = script('e2e_runner')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root/'dist'; dist.mkdir()
            with zipfile.ZipFile(dist/'traslado-2.0.0-py3-none-any.whl', 'w') as z:
                z.writestr('traslado/__init__.py', '__version__ = "2.0.0"\n')
                z.writestr('traslado-2.0.0.dist-info/METADATA', 'Name: traslado\nVersion: 2.0.0\nRequires-Python: >=3.11\n')
                z.writestr('traslado-2.0.0.dist-info/RECORD', '')
            helper._build_post1_release_dir(dist, root/'post')
            wheel = root/'post/traslado-2.0.0.post1-py3-none-any.whl'
            with zipfile.ZipFile(wheel) as z:
                meta = z.read('traslado-2.0.0.post1.dist-info/METADATA').decode()
                self.assertIn('Version: 2.0.0.post1\nRequires-Python:', meta)
                rows = csv.reader(io.StringIO(z.read('traslado-2.0.0.post1.dist-info/RECORD').decode()))
                for name, digest, size in rows:
                    if digest:
                        want = base64.urlsafe_b64encode(hashlib.sha256(z.read(name)).digest()).rstrip(b'=').decode()
                        self.assertEqual(digest, 'sha256='+want)
                        self.assertEqual(int(size), len(z.read(name)))
            sums = (root/'post/SHA256SUMS').read_text()
            self.assertIn(hashlib.sha256(wheel.read_bytes()).hexdigest(), sums)

    def test_missing_dist_and_wrong_tag_are_rejected(self):
        errors = script('versao').conferir_versao(ROOT, ROOT/'work/absent-version-dist', 'v9.0.0')
        self.assertTrue(any('ausente' in e for e in errors), errors)
        self.assertTrue(any('Tag' in e for e in errors), errors)

    @unittest.skipUnless((ROOT / "release-files.txt").is_file(), "composition gate requires checkout manifest")
    def test_composition_rejects_added_member_and_missing_licence(self):
        build = script('build_release')
        with tempfile.TemporaryDirectory() as raw:
            wheel = Path(raw)/'traslado-2.0.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as z:
                z.writestr('unlisted.txt', 'public sabotage fixture')
            self.assertTrue(build.conferir_composicao(wheel))
            self.assertTrue(build.conferir_licenca_e_spdx(wheel))

    def test_local_update_hash_and_profile_shortcuts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); dist = root/'dist'; dist.mkdir()
            installer = dist/'install.sh'; installer.write_text('# public fixture\n')
            sums = dist/'SHA256SUMS'
            digest = hashlib.sha256(installer.read_bytes()).hexdigest()
            sums.write_text(digest+'  install.sh\n'+'0'*64+'  traslado-2.0.0.post1-py3-none-any.whl\n')
            calls = []
            def run(argv):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, '', '')
            manifest = dict(profile='Core', extras=[], languages=['eng'], shortcuts=[])
            with patch.object(lifecycle.plat, 'read_current', return_value=manifest):
                result = lifecycle.update_release(root=root/'managed', from_dir=dist, yes=True,
                                                  platform_name='linux', env={}, run_cmd=run)
            self.assertEqual(result['version'], '2.0.0.post1')
            self.assertIn('--no-shortcut', calls[0])
            self.assertEqual(calls[0][calls[0].index('--profile')+1], 'core')
            installer.write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'Hash'):
                lifecycle.update_release(root=root/'managed', from_dir=dist, env={}, platform_name='linux', run_cmd=run)
            self.assertEqual(len(calls), 1)

    def test_fuzz_preserves_unknown_failure_and_accepts_typed_malformed(self):
        fuzz = script('fuzz_atheris')
        from traslado.errors import ConversionError
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw); artifacts = work/'findings'
            with patch('traslado.formats.extract', side_effect=RuntimeError('regression sentinel')):
                with self.assertRaises(AssertionError):
                    fuzz.run_single_input('txt', b'public payload', work, artifacts)
            self.assertEqual(len(list(artifacts.glob('*.txt'))), 1)
            with patch('traslado.formats.extract', side_effect=ConversionError('malformed', 'bad input')):
                fuzz.run_single_input('txt', b'invalid public payload', work, artifacts)
            self.assertFalse((work/'out').exists())

    @unittest.skipUnless(os.name == 'nt', 'self removal is Windows specific')
    def test_self_uninstall_waits_for_interpreter_exit_and_removes_root(self):
        with tempfile.TemporaryDirectory() as raw:
            managed = Path(raw)/'managed'
            dados = Path(raw)/'dados'; dados.mkdir()
            (dados/'mantido.txt').write_text('dado do usuario', encoding='utf-8')
            release = managed/'releases'/'2.0.0_teste'
            scripts = release/'.venv/Scripts'; scripts.mkdir(parents=True)
            python = scripts/'python.exe'
            shutil.copyfile(sys.executable, python)
            config = Path(sys.prefix)/'pyvenv.cfg'
            if not config.is_file():
                self.skipTest('requires venv launcher')
            shutil.copyfile(config, release/'.venv/pyvenv.cfg')
            (managed/'current.json').write_text(json.dumps({'schema': 2, 'release_id': release.name, 'data_root': str(dados)}), encoding='utf-8')
            env = {**os.environ, 'PYTHONPATH': str(Path(lifecycle.__file__).resolve().parents[1])}
            proc = subprocess.run([str(python), '-c', 'from traslado.lifecycle import uninstall_managed; import sys,json; from pathlib import Path; print(json.dumps(uninstall_managed(root=Path(sys.argv[1]),yes=True)))', str(managed)],
                                  env=env, capture_output=True, text=True, timeout=40)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            resultado = json.loads(proc.stdout)
            self.assertEqual(resultado['status'], 'uninstall_pending')
            self.assertTrue(resultado['deferred_until_process_exit'], resultado)
            deadline = time.monotonic()+25
            while managed.exists() and time.monotonic()<deadline:
                time.sleep(.1)
            self.assertFalse(managed.exists(), proc.stdout)
            self.assertEqual((dados/'mantido.txt').read_text(encoding='utf-8'), 'dado do usuario')


if __name__ == '__main__':
    unittest.main()
