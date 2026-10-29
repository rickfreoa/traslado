# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import fitz

from traslado import cli, ocr, pdf, runner, safety, worker
from traslado.model import Block, Document, Options

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '14bdfa0642e1dc37751cf4b080f586f717061f5b'


def baseline_pdf_source():
    try:
        return subprocess.check_output(['git', 'show', BASELINE + ':src/freoa_v6/pdf.py'], cwd=ROOT,
                                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        # Stage-identity checks need the repository history; installed-wheel CI runs skip them.
        raise unittest.SkipTest('revis\u00e3o ' + BASELINE + ' indispon\u00edvel fora do reposit\u00f3rio')
FAKE = '''import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if '--list-langs' in args:
    print('eng')
    sys.exit(0)
image, base = Path(args[0]), Path(args[1])
root = Path(os.environ['T11_RECEIPTS'])
(root / (str(os.getpid()) + '.pid')).write_text(json.dumps({'pid': os.getpid(), 'omp': os.environ.get('OMP_THREAD_LIMIT')}))
if os.environ.get('T11_SLEEP'):
    time.sleep(60)
else:
    # Stable shuffled completion; deterministic recognized content from input.
    import hashlib
    token = hashlib.sha256(image.read_bytes()).hexdigest()[:12]
    time.sleep(int(token[:2], 16) / 1000)
    base.with_suffix('.txt').write_text(token)
    base.with_suffix('.tsv').write_text('level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\tleft\\ttop\\twidth\\theight\\tconf\\ttext\\n5\\t1\\t1\\t1\\t1\\t1\\t1\\t2\\t10\\t12\\t95\\t' + token + '\\n')
'''


def sandbox_temp_compat():
    # Opt-in runner workaround for Python 3.14 mode-700 ACLs in this sandbox.
    if os.environ.get('T11_SANDBOX_TEMP_COMPAT'):
        original = os.mkdir
        def mkdir(path, mode=0o777, *, dir_fd=None):
            return original(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)
        os.mkdir = mkdir


def fake_adapter(script):
    def run(argv, cwd, timeout, log, **kwargs):
        return safety.run_bounded([sys.executable, str(script), *argv[1:]], cwd, timeout, log, **kwargs)
    return run


def generate(path, count=8):
    with fitz.open() as source:
        for i in range(count):
            page = source.new_page(width=180, height=240)
            page.insert_text((20, 30), 'Sheet ' + str(i), fontsize=12)
        source.save(path)


class OCRConcurrencyT11(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.pdf'
        generate(self.source)
        self.workspace = self.root / 'package'
        self.workspace.mkdir()
        self.script = self.root / 'fake.py'
        self.script.write_text(FAKE, encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, workers=4, module=pdf, **kwargs):
        document = Document('input.pdf', '0' * 64)
        module.extract(self.source, self.workspace,
                       Options(mode='ocr', language='eng', images=False, ocr_workers=workers, **kwargs), document)
        self.assertEqual(list(self.root.glob('.freoa-ocr-page-*')), [])
        return document.to_dict()

    def test_progress_matches_stage7_for_one_worker(self):
        baseline = types.ModuleType('traslado._stage7_progress')
        baseline.__package__ = 'traslado'
        source = baseline_pdf_source()
        exec(compile(source, BASELINE + '/pdf.py', 'exec'), baseline.__dict__)
        # Blank, native, full OCR, native + regional OCR, then another native page.
        from PIL import Image
        import io
        image = io.BytesIO()
        Image.new('RGB', (200, 200), 'gray').save(image, format='PNG')
        with fitz.open() as fixture:
            fixture.new_page()
            page = fixture.new_page()
            page.insert_text((30, 30), 'Native paragraph. ' * 12)
            page = fixture.new_page()
            page.insert_image(page.rect, stream=image.getvalue())
            page = fixture.new_page()
            page.insert_text((30, 30), 'Native paragraph. ' * 12)
            page.insert_image(fitz.Rect(50, 100, 400, 450), stream=image.getvalue())
            page = fixture.new_page()
            page.insert_text((30, 30), 'Native paragraph. ' * 12)
            fixture.save(self.root / 'mixed.pdf')
        for mode, path in (('ocr', self.source), ('auto', self.root / 'mixed.pdf')):
            sequences = []
            for module in (baseline, pdf):
                events = []
                with patch.object(module, '_progress', side_effect=lambda w, n, t, r: events.append((n, t, r))), patch.object(module, 'recognize_blocks', return_value=([Block('text', 'ocr')], [])), patch.object(module, 'recognize', return_value=(Block('text', 'region'), [])):
                    module.extract(path, self.workspace, Options(mode=mode, images=False, ocr_workers=1), Document('input.pdf', '0'*64))
                sequences.append(events)
            self.assertEqual(sequences[0], sequences[1])
            if mode == 'auto':
                self.assertTrue({'analyzing', 'ocr', 'ocr_done', 'native', 'blank', 'native+region_ocr'}.issubset({r for _, _, r in sequences[0]}))

    def test_default_environment_is_unchanged_and_explicit_env_preserved(self):
        script = "import os,json; print(json.dumps(dict(os.environ)))"
        for given in (None, {**os.environ, 'OMP_THREAD_LIMIT': '7', 'T11_ENV': 'preserved'}):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('OMP_THREAD_LIMIT', None)
                expected = dict(os.environ if given is None else given)
                log = self.root / 'env.log'
                with patch.object(safety.subprocess, 'Popen', wraps=subprocess.Popen) as popen:
                    safety.run_bounded([sys.executable, '-c', script], self.root, 10, log, env=given)
                # Popen é do módulo subprocess, global: outra thread viva da suíte pode chamá-lo; vale a chamada deste argv.
                # No Linux, run_bounded antepõe um shim de RLIMIT_AS; o argv do teste fica no fim.
                nossas = [c for c in popen.call_args_list if c.args and list(c.args[0][-3:]) == [sys.executable, '-c', script]]
                self.assertEqual(len(nossas), 1, popen.call_args_list)
                self.assertEqual(nossas[0].kwargs['env'], given)
                received = json.loads(log.read_text())
                # Windows não herda variáveis de ambiente de valor vazio (ex.: GITHUB_ACTION_REF no runner).
                expected_vivas = {k: v for k, v in expected.items() if v != '' or os.name != 'nt'}
                self.assertEqual({k: received.get(k) for k in expected_vivas}, expected_vivas)
                self.assertEqual(received.get('OMP_THREAD_LIMIT'), expected.get('OMP_THREAD_LIMIT'))
                if given is not None:
                    self.assertEqual(given, expected)

    def test_tesseract_explicit_environment_and_session(self):
        calls = []
        adapter = fake_adapter(self.script)
        def capture(*args, **kwargs):
            calls.append((args[0], kwargs.copy()))
            return adapter(*args, **kwargs)
        with patch.dict(os.environ, {'T11_RECEIPTS': str(self.root), 'T11_ENV': 'preserved', 'OMP_THREAD_LIMIT': '8'}), patch.object(ocr, 'run_bounded', side_effect=capture):
            self.extract(1, tesseract=sys.executable)
        self.assertTrue(any('--list-langs' in argv for argv, _ in calls))
        self.assertTrue(any('--list-langs' not in argv for argv, _ in calls))
        for argv, kwargs in calls:
            self.assertIs(kwargs['share_session'], True)
            self.assertEqual(kwargs['env']['OMP_THREAD_LIMIT'], '1')
            self.assertEqual(kwargs['env']['T11_ENV'], 'preserved')

    @unittest.skipIf(os.name == 'nt', 'POSIX sessions')
    def test_default_timeout_kills_child_and_grandchild(self):
        receipt = self.root / 'tree.json'
        script = ("import os,subprocess,sys,json,time; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  "open(sys.argv[1],'w').write(json.dumps([os.getpid(),p.pid])); time.sleep(60)")
        with self.assertRaises(TimeoutError):
            safety.run_bounded([sys.executable, '-c', script, str(receipt)], self.root, 1, self.root/'tree.log')
        pids = json.loads(receipt.read_text())
        for pid in pids:
            self.assert_dead(pid)

    @unittest.skipIf(os.name == 'nt', 'POSIX sessions')
    def test_session_sharing_is_explicit(self):
        for share in (False, True):
            with patch.dict(os.environ, {'_FREOA_BOUNDED_SID': str(os.getsid(0))}):
                log = self.root / 'sid.log'
                safety.run_bounded([sys.executable, '-c', 'import os; print(os.getpid(),os.getsid(0))'], self.root, 10, log, share_session=share)
            pid, sid = map(int, log.read_text().split())
            self.assertEqual(sid, os.getsid(0) if share else pid)

    def test_options_cli_and_toml(self):
        self.assertEqual(Options().ocr_workers, 4)
        for invalid in (0, 5, True, 2.5, '2'):
            with self.assertRaises(ValueError):
                Options(ocr_workers=invalid)
        config = self.root / 'options.toml'
        config.write_text('[conversion]\nocr_workers = 2\n')
        for extra, expected in (([], 2), (['--ocr-workers', '3'], 3)):
            with patch.object(cli, 'discover', return_value=[self.source]), patch.object(cli, 'execute', return_value={'status': 'complete'}) as run:
                self.assertEqual(cli.main(['convert', str(self.source), '-o', str(self.root), '--config', str(config), *extra]), 0)
                self.assertEqual(run.call_args.args[2].ocr_workers, expected)

    def test_shuffled_real_children_equal_serial_and_stage7(self):
        env = {'T11_RECEIPTS': str(self.root)}
        baseline = types.ModuleType('traslado._stage7')
        baseline.__package__ = 'traslado'
        source = baseline_pdf_source()
        exec(compile(source, BASELINE + '/pdf.py', 'exec'), baseline.__dict__)
        before = os.environ.get('OMP_THREAD_LIMIT')
        with patch.dict(os.environ, env), patch.object(ocr, 'run_bounded', side_effect=fake_adapter(self.script)), patch.object(pdf.os, 'cpu_count', return_value=4):
            old = self.extract(1, module=baseline, tesseract=sys.executable)
            serial = self.extract(1, tesseract=sys.executable)
            concurrent = self.extract(4, tesseract=sys.executable)
        self.assertEqual(old, serial)
        self.assertEqual(json.dumps(old), json.dumps(serial))
        self.assertEqual(json.dumps(serial), json.dumps(concurrent))
        receipts = [json.loads(p.read_text()) for p in self.root.glob('*.pid')]
        self.assertEqual(len(receipts), 24)
        self.assertTrue(all(r['omp'] == '1' for r in receipts))
        self.assertEqual(os.environ.get('OMP_THREAD_LIMIT'), before)

    def test_bounded_pngs_bytes_render_thread_and_ordered_progress(self):
        main = threading.get_ident()
        active = peak = 0
        lock = threading.Lock()
        completion = []
        barrier = threading.Barrier(4)
        progress = []
        original = pdf.render_page
        def render(*args, **kwargs):
            self.assertEqual(threading.get_ident(), main)
            original(*args, **kwargs)
            pngs = list(self.root.glob('.freoa-ocr-page-*/*.png'))
            self.assertLessEqual(len(pngs), 4)
            self.assertLessEqual(sum(p.stat().st_size for p in pngs), 100000)
        def recognize(image, workspace, options, transform):
            nonlocal active, peak
            index = int(image.stem.split('-')[-1]) if '-' in image.stem else 1
            with lock:
                active += 1
                peak = max(active, peak)
            barrier.wait(timeout=5)
            time.sleep(random.Random(index).uniform(.03, .2))
            transform(10, 20)
            with lock:
                active -= 1
                completion.append(index)
            return [Block('text', 'recognized', engine='tesseract')], []
        with patch.object(pdf, 'render_page', side_effect=render), patch.object(pdf, 'recognize_blocks', side_effect=recognize), patch.object(pdf, '_progress', side_effect=lambda w, n, t, r: progress.append((n, r))), patch.object(pdf.os, 'cpu_count', return_value=4):
            self.extract(4, max_output_bytes=100000)
        self.assertGreater(peak, 1)
        self.assertEqual(peak, 4)
        self.assertEqual([n for n, _ in progress], sorted(n for n, _ in progress))
        self.assertEqual({n for n, _ in progress}, set(range(1, 9)))
        self.assertEqual(progress[:3], [(1, 'analyzing'), (1, 'ocr'), (1, 'ocr_done')])
        self.assertNotEqual(completion, list(range(1, 9)))

    def test_live_byte_budget_waits_without_rejecting_fitting_pages(self):
        original = Path.write_bytes
        observed = []
        def write(path, data):
            if path.suffix == '.png' and path.parent.name.startswith('.freoa-ocr-page-'):
                live = sum(p.stat().st_size for p in path.parent.glob('*.png') if p.exists())
                observed.append(live + len(data))
                self.assertLessEqual(live + len(data), 10000)
            return original(path, data)
        def recognize(*args):
            time.sleep(.03)
            return [Block('text', 'ok')], []
        with patch.object(Path, 'write_bytes', write), patch.object(pdf, 'recognize_blocks', side_effect=recognize):
            report = self.extract(max_output_bytes=10000)
        self.assertEqual(len(observed), 8)
        self.assertTrue(all(not p['warnings'] for p in report['pages']))

    def test_oversize_png_is_local_and_never_written(self):
        original = pdf.render_page
        count = 0
        def render(page, image, options, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                kwargs['admit'](options.max_output_bytes + 1)
            original(page, image, options, **kwargs)
        with patch.object(pdf, 'render_page', side_effect=render), patch.object(pdf, 'recognize_blocks', return_value=([Block('text', 'ok')], [])):
            report = self.extract(max_output_bytes=10000)
        failures = [p['number'] for p in report['pages'] if any(w.startswith('ocr_failed:') for w in p['warnings'])]
        self.assertEqual(failures, [3])
        self.assertEqual(report['pages'][2]['blocks'][0]['engine'], 'pymupdf')

    def test_exception_joins_and_removes_inputs(self):
        def fail(*args):
            time.sleep(.05)
            raise LookupError('unexpected')
        with patch.object(pdf, 'recognize_blocks', side_effect=fail), patch.object(pdf.ThreadPoolExecutor, 'shutdown', autospec=True, side_effect=pdf.ThreadPoolExecutor.shutdown) as shutdown:
            with self.assertRaises(LookupError):
                self.extract()
        self.assertTrue(shutdown.call_args.kwargs['cancel_futures'])
        self.assertEqual(list(self.root.glob('.freoa-ocr-page-*')), [])

    def test_cpu_cap_and_unknown_cpu(self):
        for cpus in (None, 1):
            active = peak = 0
            def recognize(*args):
                nonlocal active, peak
                active += 1
                peak = max(active, peak)
                time.sleep(.005)
                active -= 1
                return [], []
            with patch.object(pdf.os, 'cpu_count', return_value=cpus), patch.object(pdf, 'recognize_blocks', side_effect=recognize):
                self.extract()
            self.assertEqual(peak, 1)

    def assert_dead(self, pid):
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0, f'PID {pid} survived')
                finally:
                    kernel.CloseHandle(handle)
            else:
                self.assertEqual(ctypes.get_last_error(), 87)
        else:
            for _ in range(60):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                status = Path(f'/proc/{pid}/stat')
                try:
                    if status.read_text().split()[2] == 'Z':
                        return  # dead, awaiting reaping by init
                except (FileNotFoundError, ProcessLookupError):
                    return  # reaped between os.kill and the read
                time.sleep(.05)
            self.fail(f'PID {pid} survived')

    def test_worker_timeout_and_cancellation_leave_no_ocr_children(self):
        for mode in ('timeout', 'cancel'):
            for receipt in self.root.glob('*.pid'):
                receipt.unlink()
            env = os.environ.copy()
            env.update(T11_RECEIPTS=str(self.root), T11_SLEEP='1', PYTHONPATH=str(ROOT / 'src'))
            def cancel():
                return len(list(self.root.glob('*.pid'))) >= 4
            started = time.monotonic()
            def supervised(argv, *args, **kwargs):
                return safety.run_bounded([sys.executable, str(Path(__file__).resolve()), '--request', argv[-1]],
                                          *args, **kwargs)
            with patch.dict(os.environ, env), patch.object(runner, 'run_bounded', side_effect=supervised):
                result = runner.execute(self.source, self.root / 'output',
                                        Options(mode='ocr', language='eng', tesseract=sys.executable,
                                                ocr_timeout=60, job_timeout=12 if mode == 'timeout' else 30),
                                        cancel=cancel if mode == 'cancel' else None)
            self.assertEqual(result['status'], 'timeout' if mode == 'timeout' else 'cancelled', result)
            receipts = [json.loads(p.read_text()) for p in self.root.glob('*.pid')]
            self.assertEqual(len(receipts), 4, result)
            for receipt in receipts:
                self.assert_dead(receipt['pid'])
            self.assertEqual(list((self.root / 'output').iterdir()), [], result)
            self.assertNotIn('cleanup_warning', result)
            self.assertLess(time.monotonic() - started, 35)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--request':
        sandbox_temp_compat()
        request = sys.argv[2]
        sys.argv = [sys.argv[0], request]
        with patch.object(pdf.os, 'cpu_count', return_value=4), patch.object(ocr, 'run_bounded', side_effect=fake_adapter(Path(os.environ['T11_RECEIPTS']) / 'fake.py')):
            raise SystemExit(worker.main())
    else:
        unittest.main()
