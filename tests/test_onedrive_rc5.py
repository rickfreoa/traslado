from __future__ import annotations
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from traslado.pathguard import unsafe_link
from traslado.safety import is_link
from traslado.runtime import _is_link

SOURCE = Path(__file__).resolve().parents[1]
script = SOURCE / "scripts/install_windows.py"
spec = importlib.util.spec_from_file_location("rc5_installer_guard", script)
installer = importlib.util.module_from_spec(spec)
exec(compile(script.read_bytes(), str(script), "exec"), installer.__dict__)

class FakePath:
    def __init__(self, tag, attrs=0x400, symlink=False, junction=False):
        self.tag, self.attrs, self.symlink, self.junction = tag, attrs, symlink, junction
    def is_symlink(self): return self.symlink
    def is_junction(self): return self.junction
    def lstat(self): return SimpleNamespace(st_file_attributes=self.attrs, st_reparse_tag=self.tag)

class CloudGuardsRC5(unittest.TestCase):
    def test_all_sixteen_explicit_cloud_tags_are_regular_files(self):
        for n in range(16):
            for guard in (unsafe_link, is_link, _is_link):
                self.assertFalse(guard(FakePath(0x9000001A | n << 12)))
    def test_unknown_reparse_points_and_links_fail_closed(self):
        for tag in (0, 0xA0000003, 0xA000000C, 0x8000001B, 0x9000001B, 0xB000001A, 0x9001001A):
            for guard in (unsafe_link, is_link, _is_link):
                self.assertTrue(guard(FakePath(tag)))
        self.assertTrue(unsafe_link(FakePath(0x9000001A, symlink=True)))
        self.assertTrue(unsafe_link(FakePath(0x9000001A, junction=True)))
    def test_installer_classifier_accepts_cloud_but_rejects_unknown(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            with patch.object(Path, "is_symlink", return_value=False), patch.object(Path, "is_junction", return_value=False, create=True):  # is_junction só existe no 3.12+; o produto usa hasattr
                for n in range(16):
                    with patch.object(Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0x400, st_reparse_tag=0x9000001A | n << 12)):
                        self.assertEqual(installer.no_links(path), path.resolve())
                for tag in (0, 0xA0000003, 0xA000000C, 0x9001001A):
                    with patch.object(Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0x400, st_reparse_tag=tag)):
                        with self.assertRaises(ValueError): installer.no_links(path)
    def test_real_junction_is_rejected_in_python_guards(self):
        if os.name != "nt": self.skipTest("Windows junction")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); target = root / "target"; target.mkdir(); link = root / "junction"
            probe = root / "junction.ps1"
            probe.write_text("param([string]$LinkPath,[string]$TargetPath)\nNew-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null", encoding="utf-8-sig")
            subprocess.run([str(installer.POWERSHELL), "-NoProfile", "-File", str(probe), "-LinkPath", str(link), "-TargetPath", str(target)], capture_output=True, check=True)
            try:
                self.assertTrue(unsafe_link(link))
                with self.assertRaises(ValueError): installer.no_links(link)
            finally: link.rmdir()
    def test_real_cloud_cache_can_be_read_without_modification(self):
        raw = os.environ.get("FREOA_RC5_CLOUD_CACHE")
        if not raw: self.skipTest("Real OneDrive cache supplied only in controlled Windows validation")
        cache = Path(raw)
        self.assertEqual(installer.no_links(cache), cache.resolve())
        for file in cache.iterdir():
            self.assertFalse(unsafe_link(file))
            self.assertEqual(installer.no_links(file), file.resolve())

class InstallerDiagnosticsRC5(unittest.TestCase):
    def test_child_error_is_visible_and_persisted_even_without_capture(self):
        with tempfile.TemporaryDirectory() as raw:
            logs = Path(raw).resolve()
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(installer, "LOG_DIRECTORY", logs), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    installer.run([sys.executable, "-I", "-c", "import sys;print('BOOTSTRAP_CAUSA_EXATA',file=sys.stderr);sys.exit(7)"])
            self.assertEqual(caught.exception.returncode, 7)
            self.assertIn(b"BOOTSTRAP_CAUSA_EXATA", caught.exception.stderr)
            self.assertIn("BOOTSTRAP_CAUSA_EXATA", stderr.getvalue())
            files = list(logs.glob("stage-*.log")); self.assertEqual(len(files), 1)
            self.assertIn("BOOTSTRAP_CAUSA_EXATA", files[0].read_text())
            self.assertIn(str(files[0]), stdout.getvalue())
    def test_successful_captured_json_stays_separate_from_stderr(self):
        with contextlib.redirect_stdout(io.StringIO()):
            data = installer.run([sys.executable, "-I", "-c", "import sys;print('{\"ok\":true}');print('ADVISORY',file=sys.stderr)"], capture=True)
        self.assertTrue(json.loads(data)["ok"])
    def test_timeout_persists_child_diagnostic(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(installer, "LOG_DIRECTORY", Path(raw)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.TimeoutExpired):
                installer.run([sys.executable, "-I", "-c", "import time;print('BEFORE_TIMEOUT',flush=True);time.sleep(30)"], timeout=1)
            self.assertIn("BEFORE_TIMEOUT", next(Path(raw).glob("*.log")).read_text())

    def test_excessive_stage_output_is_refused_with_bounded_tail_log(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(installer, "LOG_DIRECTORY", Path(raw)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                installer.run([sys.executable, "-I", "-c", "import os;os.write(1,b'X'*(65*1024*1024))"], timeout=10)
            self.assertLess(next(Path(raw).glob("*.log")).stat().st_size, 9 * 1024 * 1024)
