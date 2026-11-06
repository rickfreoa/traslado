# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from traslado.errors import ConversionError
from traslado.model import Options
from traslado.runner import execute
from traslado import safety, worker


class ResourceCausesC1(unittest.TestCase):
    def test_monitor_limit_survives_cleanup_io(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'a.txt'
            source.write_text('sentinel')
            proc = MagicMock()
            job = MagicMock()
            job.peak_memory.return_value = 0
            for poll in (None, 2):  # monitor observes excess before/after worker exits
                proc.poll.return_value = poll
                with self.subTest(poll=poll), \
                     patch('traslado.safety.subprocess.Popen', return_value=proc), \
                     patch('traslado.windows_job.WindowsJob', return_value=job), \
                     patch('traslado.safety.group_processes', return_value=[]), \
                     patch('traslado.safety._directory_bytes', return_value=101), \
                     patch('traslado.safety.kill_tree', side_effect=OSError('process disappeared')):
                    result = execute(source, root / 'out', Options(max_output_bytes=100))
                self.assertEqual(result['error_code'], 'resource_limit', result)

    def test_recorded_limit_precedes_worker_diagnostics(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'a.txt'
            source.write_text('sentinel')
            for cause in ('directory', 'memory', 'log'):
                for payload in (None, '{', json.dumps({'code': 'io', 'error': 'write failed'})):
                    with self.subTest(cause=cause, payload=payload):
                        def finished(*args, **kwargs):
                            kwargs['measurements']['resource_limit'] = cause
                            if payload is not None:
                                (kwargs['budget_root'] / 'error.json').write_text(payload)
                            return 2
                        with patch('traslado.runner.run_bounded', side_effect=finished):
                            result = execute(source, root / 'out')
                        self.assertEqual(result['error_code'], 'resource_limit', result)

    def test_memory_exit_precedes_io_diagnostic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'a.txt'
            source.write_text('sentinel')
            def finished(*args, **kwargs):
                (kwargs['budget_root'] / 'error.json').write_text(json.dumps({'code': 'io', 'error': 'write'}))
                return -1073741801
            with patch('traslado.runner.run_bounded', side_effect=finished):
                self.assertEqual(execute(source, root / 'out')['error_code'], 'resource_limit')

    def test_worker_limit_diagnostic_outside_output_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / 'package'
            package.mkdir()
            source = root / 'a.txt'
            source.write_text('sentinel')
            request = root / 'request.json'
            request.write_text(json.dumps({'snapshot': str(source), 'workspace': str(package),
                                          'source_name': source.name, 'sha256': safety.sha256(source),
                                          'options': {}}))
            write_text = Path.write_text
            def write(path, *args, **kwargs):
                if path == package / 'error.json':
                    raise OSError('diagnostic output truncated')
                return write_text(path, *args, **kwargs)
            with patch.object(sys, 'argv', ['worker', str(request)]), \
                 patch('traslado.worker.extract', side_effect=ConversionError('resource_limit', 'quota')), \
                 patch.object(Path, 'write_text', write):
                self.assertEqual(worker.main(), 2)
            info = json.loads((root / 'worker-error.json').read_text())
            self.assertEqual(info['code'], 'resource_limit')

    def test_private_worker_cause_precedes_truncated_output_diagnostic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'a.txt'
            source.write_text('sentinel')
            def finished(argv, private, *args, **kwargs):
                (private / 'worker-error.json').write_text(json.dumps({'code': 'resource_limit', 'error': 'quota'}))
                (kwargs['budget_root'] / 'error.json').write_text('{')
                return 2
            with patch('traslado.runner.run_bounded', side_effect=finished):
                self.assertEqual(execute(source, root / 'out')['error_code'], 'resource_limit')

    def test_unrelated_worker_io_remains_io(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'a.txt'
            source.write_text('sentinel')
            def finished(*args, **kwargs):
                (kwargs['budget_root'] / 'error.json').write_text(json.dumps({'code': 'io', 'error': 'disk'}))
                return 2
            with patch('traslado.runner.run_bounded', side_effect=finished):
                self.assertEqual(execute(source, root / 'out')['error_code'], 'io')
