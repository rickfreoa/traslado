# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import json
import ctypes
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from traslado.advanced import sanitize
from traslado.model import Options
from traslado.ocr import find_tesseract

SOURCE = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
LAST_PROCESS_PROBE = None
LAST_TREE_PROBES = []


class PathSearchRC3(unittest.TestCase):
    """Busca de tesseract no PATH: PATHEXT, ordem, X_OK, aspas e symlink para o cwd (valem nos dois sistemas)."""
    NAME = 'tesseract.exe' if os.name == 'nt' else 'tesseract'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='freoa-path-search-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        self.cwd = self.folder / 'cwd'
        self.cwd.mkdir()
        self.original = Path.cwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, self.original)
        # Sem motor gerenciado nem locais conhecidos.
        for target in ('traslado.ocr.managed_tesseract',):
            patcher = patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.folder / 'x'), 'ProgramFiles': str(self.folder / 'y')})
        env.start()
        self.addCleanup(env.stop)

    def engine(self, directory, name=None):
        directory.mkdir(exist_ok=True)
        path = directory / (name or self.NAME)
        path.write_bytes(b'INERT')
        path.chmod(0o755)
        return path

    def search(self, path, **extra):
        with patch.dict(os.environ, {'PATH': path, **extra}):
            return find_tesseract()

    def test_candidates_pathext_vazio_ou_ausente_usa_o_padrao_do_shutil(self):
        from traslado.ocr import _candidates
        d = Path('D:/ferramentas')
        padrao = [d / ('tesseract' + e) for e in ('.com', '.exe', '.bat', '.cmd')]
        for pathext in ('', None, os.pathsep * 2):
            self.assertEqual(_candidates(d, True, pathext), padrao, repr(pathext))
        self.assertEqual(_candidates(d, True, os.pathsep.join(['.CMD', '.EXE'])), [d / 'tesseract.cmd', d / 'tesseract.exe'])
        self.assertEqual(_candidates(d, False, '.EXE'), [d / 'tesseract'])

    @unittest.skipUnless(os.name == 'nt', 'PATHEXT é do Windows')
    def test_pathext_vazio_ainda_acha_exe(self):
        engine = self.engine(self.folder / 'bin')
        for pathext in ('', ';'):
            self.assertEqual(self.search(str(engine.parent), PATHEXT=pathext), str(engine))

    @unittest.skipUnless(os.name == 'nt', 'PATHEXT é do Windows')
    def test_ordem_diretorios_antes_de_extensoes_e_extensoes_na_ordem_do_pathext(self):
        a, b = self.folder / 'a', self.folder / 'b'
        self.engine(a, 'tesseract.cmd')
        self.engine(b, 'tesseract.exe')
        # primeiro diretório vence mesmo com extensão de menor prioridade
        self.assertEqual(self.search(os.pathsep.join([str(a), str(b)]), PATHEXT='.EXE;.CMD'), str(a / 'tesseract.cmd'))
        self.engine(a, 'tesseract.exe')
        self.assertEqual(self.search(str(a), PATHEXT='.CMD;.EXE'), str(a / 'tesseract.cmd'))
        self.assertEqual(self.search(str(a), PATHEXT='.EXE;.CMD'), str(a / 'tesseract.exe'))

    def test_arquivo_sem_permissao_de_execucao_e_ignorado(self):
        engine = self.engine(self.folder / 'bin')
        with patch('traslado.ocr.os.access', return_value=False):
            self.assertIsNone(self.search(str(engine.parent)))
        self.assertEqual(self.search(str(engine.parent)), str(engine))

    def test_diretorio_confiavel_entre_aspas(self):
        engine = self.engine(self.folder / 'bin')
        self.assertEqual(self.search('"%s"' % engine.parent), str(engine))

    def test_symlink_de_diretorio_para_o_cwd_e_excluido(self):
        self.engine(self.cwd)
        link = self.folder / 'link-para-cwd'
        try:
            link.symlink_to(self.cwd, target_is_directory=True)
        except OSError:
            self.skipTest('criar symlink exige privilégio neste ambiente')
        self.assertIsNone(self.search(str(link)))


@unittest.skipUnless(os.name == 'nt', 'Windows executable selection')
class OCRSelectionRC3(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='freoa-ocr-selection-')
        self.folder = Path(self.temporary.name)
        self.original_cwd = Path.cwd()
        self.cwd = self.folder / 'untrusted-document-directory'
        self.cwd.mkdir()
        self.fake = self.cwd / 'tesseract.exe'
        self.fake.write_bytes(b'INERT_CWD_EXECUTABLE_MUST_NEVER_RUN')
        (self.cwd / 'relative').mkdir()
        (self.cwd / 'relative/tesseract.exe').write_bytes(b'INERT_RELATIVE_EXECUTABLE_MUST_NEVER_RUN')
        os.chdir(self.cwd)
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.folder / 'empty-local'),
            'ProgramFiles': str(self.folder / 'empty-programs'), 'PATHEXT': '.COM;.EXE;.BAT;.CMD'})
        self.environment.start()
        # Suppress managed lookup through its real base-interpreter condition.
        self.prefix = patch.object(sys, 'prefix', sys.base_prefix)
        self.prefix.start()

    def tearDown(self):
        self.prefix.stop()
        self.environment.stop()
        os.chdir(self.original_cwd)
        self.temporary.cleanup()

    def test_inert_cwd_empty_relative_and_quoted_cwd_path_are_not_selected(self):
        for path in ['', '.', 'relative', str(self.cwd), f'"{self.cwd}"',
                     os.pathsep.join(['', '.', 'relative', str(self.cwd)])]:
            with self.subTest(path=path), patch.dict(os.environ, {'PATH': path}):
                self.assertIsNone(find_tesseract())
        self.assertEqual(self.fake.read_bytes(), b'INERT_CWD_EXECUTABLE_MUST_NEVER_RUN')

    def test_absolute_trusted_path_wins_over_cwd_and_relative_entries(self):
        trusted = self.folder / 'trusted-tools'
        trusted.mkdir()
        engine = trusted / 'tesseract.exe'
        engine.write_bytes(b'INERT_TRUSTED_EXECUTABLE_MUST_NEVER_RUN')
        path = os.pathsep.join(['', '.', 'relative', str(self.cwd), str(trusted)])
        with patch.dict(os.environ, {'PATH': path}):
            self.assertEqual(find_tesseract(), str(engine.resolve()))

    def test_explicit_absolute_selection_wins_and_relative_is_refused(self):
        with patch('traslado.ocr.managed_tesseract', return_value='UNUSED_MANAGED_ENGINE'), \
             patch.dict(os.environ, {'PATH': ''}):
            self.assertEqual(find_tesseract(str(self.fake)), str(self.fake))
            with self.assertRaises(ValueError):
                find_tesseract('tesseract.exe')

    def test_known_environment_roots_reject_relative_paths_but_keep_absolute_fallback(self):
        for relative in ['.', 'relative']:
            directory = self.cwd / relative / 'Tesseract-OCR'
            directory.mkdir(exist_ok=True)
            (directory / 'tesseract.exe').write_bytes(b'INERT_ENV_ROOT_EXECUTABLE_MUST_NEVER_RUN')
        trusted = self.folder / 'trusted-global-root'
        engine = trusted / 'Tesseract-OCR/tesseract.exe'
        engine.parent.mkdir(parents=True)
        engine.write_bytes(b'INERT_ABSOLUTE_ENV_ENGINE_MUST_NEVER_RUN')
        for variable in ['LOCALAPPDATA', 'ProgramFiles']:
            for value in ['.', 'relative']:
                with self.subTest(variable=variable, relative=value), \
                     patch.dict(os.environ, {'PATH': '', variable: value}):
                    self.assertIsNone(find_tesseract())
            with self.subTest(variable=variable, absolute=str(trusted)), \
                 patch.dict(os.environ, {'PATH': '', variable: str(trusted)}):
                self.assertEqual(find_tesseract(), str(engine.resolve()))


class AdvancedCleanupRC3(unittest.TestCase):
    def test_failed_png_conversion_removes_only_partial_asset_for_both_expected_errors(self):
        with tempfile.TemporaryDirectory(prefix='freoa-png-cleanup-') as raw:
            folder = Path(raw)
            model, workspace = folder / 'model', folder / 'workspace'
            model.mkdir()
            (workspace / 'assets').mkdir(parents=True)
            source = model / 'figure.png'
            source.write_bytes(b'INERT_MODEL_IMAGE')
            sentinel = workspace / 'assets/keep.png'
            sentinel.write_bytes(b'KEEP_OTHER_ASSET')
            target = workspace / 'assets/advanced-7-1.png'
            for error in [OSError('SYNTHETIC_WRITE_ERROR'), ValueError('SYNTHETIC_IMAGE_ERROR')]:
                def fail_image(origin, destination, options):
                    self.assertEqual(origin, source.resolve())
                    destination.write_bytes(b'PARTIAL_IMAGE')
                    raise error
                with self.subTest(error=type(error).__name__), \
                     patch('traslado.advanced.image_to_png', side_effect=fail_image):
                    with self.assertRaises(type(error)) as caught:
                        sanitize('![figure](figure.png)', model, workspace, 7, Options(images=True))
                    self.assertIs(caught.exception, error)
                    self.assertFalse(target.exists())
                    self.assertEqual(source.read_bytes(), b'INERT_MODEL_IMAGE')
                    self.assertEqual(sentinel.read_bytes(), b'KEEP_OTHER_ASSET')


@unittest.skipUnless(os.name == 'nt', 'PowerShell process filter')
class ReleaseProcessFilterRC3(unittest.TestCase):
    def test_real_ps_filter_includes_simulated_orphan_ocr_but_excludes_sibling(self):
        global LAST_PROCESS_PROBE
        with tempfile.TemporaryDirectory(prefix='freoa-process-filter-') as raw:
            folder = Path(raw).resolve()
            release = folder / 'release'
            entries = [
                {'ProcessId': 101, 'ExecutablePath': str(release / '.venv/Scripts/python.exe')},
                {'ProcessId': 102, 'ExecutablePath': str(release / 'ocr/tesseract.exe')},
                {'ProcessId': 103, 'ExecutablePath': str(folder / 'release-sibling/ocr/tesseract.exe')},
                {'ProcessId': 104, 'ExecutablePath': None},
            ]
            fixtures = folder / 'cim.json'
            fixtures.write_text(json.dumps(entries), encoding='utf-8')
            wrapper = folder / 'probe.ps1'
            wrapper.write_text('''param([string]$Script,[string]$ReleaseRoot,[string]$Fixtures)
$ErrorActionPreference='Stop'
$taskMock=Get-Content -LiteralPath $Fixtures -Raw -Encoding UTF8 | ConvertFrom-Json
function Get-CimInstance { param([string]$ClassName) $taskMock }
. $Script -ReleaseRoot $ReleaseRoot
''', encoding='utf-8-sig')
            result = subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(wrapper), '-Script', str(SOURCE / 'scripts/installation_processes.ps1'),
                '-ReleaseRoot', str(release), '-Fixtures', str(fixtures)],
                capture_output=True, timeout=15, check=True)
            captured = json.loads(result.stdout.decode('utf-8-sig'))
            self.assertEqual({entry['pid'] for entry in captured}, {101, 102})
            LAST_PROCESS_PROBE = {'kind': 'real PowerShell filter; CIM mocked; no OCR executed',
                                  'input': entries, 'output': captured}


@unittest.skipUnless(os.name == 'nt', 'Real Windows Job Object supervision')
class InstallerProcessTreeRC3(unittest.TestCase):
    def probe_tree(self, normal_exit):
        with tempfile.TemporaryDirectory(prefix='freoa-installer-job-') as raw:
            folder = Path(raw)
            script = folder / 'scripts/install_windows.py'
            helper = folder / 'src/traslado/windows_job.py'
            script.parent.mkdir(parents=True)
            helper.parent.mkdir(parents=True)
            script.write_bytes((SOURCE / 'scripts/install_windows.py').read_bytes())
            helper.write_bytes((SOURCE / 'src/traslado/windows_job.py').read_bytes())
            (folder / 'INSTALL-PAYLOAD.json').write_text(json.dumps({'files': [{
                'path': 'src/traslado/windows_job.py',
                'sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}]}), encoding='utf-8')
            spec = importlib.util.spec_from_file_location('freoa_security_test_installer', script)
            installer = importlib.util.module_from_spec(spec)
            # Compile the effective source bytes without reusing cached bytecode.
            exec(compile(script.read_bytes(), str(script), 'exec'), installer.__dict__)
            receipt = folder / 'child.pid'
            parent = folder / 'parent.py'
            parent.write_text('''import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(30)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
if sys.argv[2] == 'wait': time.sleep(30)
''', encoding='utf-8')
            args = [sys.executable, '-I', str(parent), str(receipt), 'exit' if normal_exit else 'wait']
            if normal_exit:
                self.assertEqual(installer.run(args, capture=True, timeout=10), '')
            else:
                with self.assertRaises(subprocess.TimeoutExpired):
                    installer.run(args, capture=True, timeout=8)
            self.assertTrue(receipt.is_file(), 'Own child must start before the cleanup claim')
            child_pid = int(receipt.read_text())
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.WaitForSingleObject.restype = ctypes.c_ulong
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle.restype = ctypes.c_int
            handle = kernel.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
            try:
                if handle:
                    wait = kernel.WaitForSingleObject(handle, 2000)
                    self.assertEqual(wait, 0, 'Own sleeping child survived the Job close')
                else:
                    error = ctypes.get_last_error()
                    self.assertEqual(error, 87, 'Unexpected process query failure')
                LAST_TREE_PROBES.append({'mode': 'normal_parent_exit' if normal_exit else 'timeout',
                    'child_pid_receipt': child_pid, 'child_started': True, 'child_alive_after_run': False,
                    'process_oracle': 'Windows process handle signaled or ERROR_INVALID_PARAMETER',
                    'installer_sha256': hashlib.sha256(script.read_bytes()).hexdigest()})
            finally:
                if handle:
                    kernel.CloseHandle(handle)

    def test_timeout_closes_real_sleeping_child_tree(self):
        self.probe_tree(False)

    def test_normal_parent_exit_closes_real_orphan_sleeping_child(self):
        self.probe_tree(True)


try:
    import tkinter as tk
    from traslado.gui import App
except ImportError:
    tk = None
    App = None


@unittest.skipIf(tk is None, 'Tk unavailable')
class GUICleanupWarningRC3(unittest.TestCase):
    def test_result_cleanup_warning_is_visible_in_real_tk_log(self):
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        try:
            with patch.object(App, 'check_installation', lambda self: None):
                app = App(root)
            app.events.put(('result', {'source': 'synthetic-source.pdf', 'status': 'failed',
                'output': None, 'error': 'SYNTHETIC_FAILURE',
                'cleanup_warning': 'CLEANUP_WARNING_SENTINEL'}, '', 1))
            app.poll()
            content = app.log.get('1.0', 'end')
            self.assertIn('CLEANUP_WARNING_SENTINEL', content)
            self.assertIn('synthetic-source.pdf', content)
            self.assertIn('Aviso de limpeza', content)
        finally:
            for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                root.after_cancel(callback)
            root.destroy()


if __name__ == '__main__':
    unittest.main()
