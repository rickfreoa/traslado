# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Real child/grandchild probes; machine-readable evidence outside the package."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from traslado import safety


ALLOCATOR = '''
import os,sys,time
from pathlib import Path
root=Path(sys.argv[1])
(root / ('pid-'+str(os.getpid()))).write_text(str(os.getpid()))
blocks=[]
try:
    while True:
        blocks.append(bytearray(8*1024*1024))
        time.sleep(.04)
except MemoryError:
    (root/'detected').write_text(str(time.monotonic()))
    sys.exit(42)
'''


def alive(pid):
    if sys.platform == 'win32':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    if sys.platform == 'linux':
        try:
            return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False
    import subprocess
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


class ContainmentE2(unittest.TestCase):
    def probe(self, kind):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        script = root / 'allocate.py'
        script.write_text(ALLOCATOR, encoding='utf-8')
        if kind == 'grandchild':
            # Each process remains below Linux's per-process address-space cap,
            # while their combined RSS crosses the tree budget.
            script.write_text(ALLOCATOR.replace('while True:', 'for _ in range(18):').replace('except MemoryError:', '    time.sleep(60)\nexcept MemoryError:'), encoding='utf-8')
            code = '''import os,sys,subprocess,time
from pathlib import Path
r=Path(sys.argv[1])
(r/('pid-'+str(os.getpid()))).write_text(str(os.getpid()))
p=subprocess.Popen([sys.executable,str(r/'allocate.py'),str(r)])
blocks=[]
try:
    for _ in range(18):
        blocks.append(bytearray(8*1024*1024))
        time.sleep(.04)
except MemoryError:
    (r/'detected').write_text(str(time.monotonic()))
    sys.exit(42)
sys.exit(p.wait())
'''
        elif kind == 'timeout':
            code = ("import os,sys,time,subprocess; from pathlib import Path; r=Path(sys.argv[1]); "
                    "(r/('pid-'+str(os.getpid()))).write_text(str(os.getpid())); "
                    "subprocess.Popen([sys.executable,'-c',\"import os,sys,time; from pathlib import Path; r=Path(sys.argv[1]); (r/('pid-'+str(os.getpid()))).write_text(str(os.getpid())); time.sleep(60)\",str(r)]); time.sleep(60)")
        else:
            code = ALLOCATOR
        measurements = {}
        error = None
        try:
            status = safety.run_bounded([sys.executable, '-c', code, str(root)], root,
                                        10 if kind == 'timeout' else 25, root / 'worker.log',
                                        max_memory_bytes=256*1024*1024, measurements=measurements)
        except (ValueError, TimeoutError) as exc:
            status, error = None, str(exc)
        stopped = time.monotonic()
        pids = sorted(set(int(p.read_text()) for p in root.glob('pid-*')) | set(measurements.get('job_pids', [])))
        deadline = stopped + 4
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.05)
        remaining = [pid for pid in pids if alive(pid)]
        if sys.platform != 'win32':
            remaining = sorted(set(remaining) | {pid for pid, _ in safety.group_processes(measurements['pid'])})
        drained = time.monotonic() - stopped
        measurements['elapsed_s'] += drained
        measurements['drain_s'] = drained
        detected = root / 'detected'
        delay = stopped + drained - float(detected.read_text()) if detected.exists() else None
        if measurements['detected_after_s'] is not None:
            delay = measurements['elapsed_s'] - measurements['detected_after_s']
        measurements.update(case=kind, status=status, error=error, remaining_processes=remaining,
                            termination_after_detection_s=delay, recorded_pids=pids)
        shutil.rmtree(root)
        measurements['remaining_temporaries'] = int(root.exists())
        output = Path(__file__).parent / '_medicoes' / ('contencao-' + sys.platform + '.json')
        output.parent.mkdir(exist_ok=True)
        data = json.loads(output.read_text()) if output.exists() else {}
        data[kind] = measurements
        output.write_text(json.dumps(data, indent=2), encoding='utf-8')
        self.assertGreaterEqual(len(pids), 2 if kind in ('grandchild', 'timeout') else 1, measurements)
        self.assertEqual(remaining, [], measurements)
        self.assertEqual(measurements['remaining_temporaries'], 0)
        self.assertNotEqual(status, 0, measurements)
        if kind == 'timeout':
            self.assertEqual(error, 'Tempo limite excedido')
            self.assertLess(measurements['elapsed_s'], 15)
        else:
            # A syntax error or an unrelated crash must not qualify as containment.
            if sys.platform == 'win32' or (sys.platform == 'linux' and kind == 'child'):
                self.assertEqual(status, 42, measurements)
                self.assertTrue(detected.exists() or delay is not None, measurements)
            else:
                self.assertEqual(error, 'Limite de memória excedido (monitorado)', measurements)
                self.assertIsNotNone(measurements['detected_after_s'], measurements)
            self.assertGreater(measurements['peak_bytes'], 0)
        if delay is not None:
            self.assertLess(delay, 5, measurements)

    def test_memory_child(self):
        self.probe('child')

    def test_memory_grandchild(self):
        self.probe('grandchild')

    def test_timeout(self):
        self.probe('timeout')

    def test_ocr_sibling_raster_counts_in_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / 'package'
            package.mkdir()
            raster = root / '.freoa-ocr-page-test'
            raster.mkdir()
            (raster / 'page.png').write_bytes(b'x' * 1024)
            with self.assertRaisesRegex(ValueError, 'Arquivos temporários'):
                safety.run_bounded([sys.executable, '-c', 'import time; time.sleep(10)'], root,
                                   15, root / 'log', budget_root=package, max_directory_bytes=512)

    def test_linux_rss_parser_filters_group_and_zombies(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for pid, group, state, rss in ((10, 10, 'S', 3), (11, 10, 'S', 4), (12, 20, 'S', 100), (13, 10, 'Z', 100)):
                folder = root / str(pid)
                folder.mkdir()
                (folder / 'stat').write_text(f'{pid} (name with ) parens) {state} 1 {group} 1 0')
                (folder / 'statm').write_text(f'100 {rss}')
            real_path = Path
            with patch.object(safety.sys, 'platform', 'linux'), patch.object(safety, 'Path', side_effect=lambda value: root if value == '/proc' else real_path(value)), patch.object(safety.os, 'sysconf', return_value=4096, create=True):
                self.assertEqual(sorted(safety.group_processes(10)), [(10, 3*4096), (11, 4*4096)])

    def test_ocr_txt_tsv_count_even_after_fast_exit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / 'package'
            package.mkdir()
            code = "from pathlib import Path; p=Path('package/ocr'); p.mkdir(); (p/'result.txt').write_bytes(b'x'*300); (p/'result.tsv').write_bytes(b'x'*300)"
            with self.assertRaisesRegex(ValueError, 'Arquivos temporários'):
                safety.run_bounded([sys.executable, '-c', code], root, 15, root / 'log', budget_root=package, max_directory_bytes=512)

    def test_macos_rss_parser(self):
        with patch.object(safety.sys, 'platform', 'darwin'), patch.object(safety.subprocess, 'check_output', return_value='10 10 50 S\n11 10 70 S\n12 20 100 S\n13 10 10 Z\n'):
            self.assertEqual(safety.group_processes(10), [(10, 50*1024), (11, 70*1024)])


if __name__ == '__main__':
    unittest.main()
