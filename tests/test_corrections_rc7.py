# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression contracts: overlap, atom boundaries, quota and process cleanup."""
import json
import hashlib
from dataclasses import asdict
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from traslado import chunks
from traslado.model import Block, Document, Options, Page
from traslado.render import render, table_markdown
from traslado.runner import execute
from traslado.safety import run_bounded


class CorrectionsRC7(unittest.TestCase):
    def document(self, text):
        return Document('Prosa.md', 'a' * 64, [Page(1, 'native', [Block('text', text)])])

    def check_ranges(self, text, size, overlap, atomic=()):
        ranges = chunks._ranges(text, size, overlap, list(atomic))
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(text))
        for i, (start, end) in enumerate(ranges):
            self.assertGreater(end, start)
            for a, b in atomic:
                self.assertFalse(a < start < b)
                self.assertFalse(a < end < b)
            if i:
                self.assertGreater(start, ranges[i-1][0])
                self.assertGreater(end, ranges[i-1][1])
                self.assertLessEqual(start, ranges[i-1][1])
                if not atomic:
                    actual = ranges[i-1][1] - start
                    self.assertLessEqual(actual, overlap)
                    if actual:
                        self.assertTrue(text[start].isspace() or text[start-1].isspace())
                    longest = max((len(word) for word in text.split()), default=0)
                    self.assertGreaterEqual(actual, max(0, overlap - longest - max((len(w) for w in re.findall(r"\s+", text)), default=0)))
        return ranges

    def test_paragraphs_1300_keep_word_aligned_overlap_near_600(self):
        self.check_ranges(('palavra ' * 162 + 'fim.\n\n') * 30, 6000, 600)

    def test_mixed_paragraphs_keep_word_aligned_overlap(self):
        text = '\n\n'.join('ação 😀 ' * n for n in (100, 187, 333, 65, 620, 201, 7, 412) * 8)
        self.check_ranges(text, 6000, 600)

    def test_overlap_near_size_must_advance_both_boundaries(self):
        self.check_ranges('abc def\n\n' * 100, 256, 255)

    def test_random_prose_and_atomic_ranges_preserve_coverage_and_progress(self):
        rng = random.Random(7106)
        for i in range(80):
            text = '\n\n'.join('Ab 😀 ç ' * rng.randint(2, 80) for _ in range(12))
            size = rng.randint(256, 700)
            overlap = rng.randrange(size)
            self.check_ranges(text, size, overlap)
            start = rng.randrange(len(text) - 1)
            end = rng.randint(start + 1, len(text))
            self.check_ranges(text, size, overlap, [(start, end)])

    def test_zero_overlap_remains_zero_even_with_large_atom(self):
        self.check_ranges('x' * 1600, 256, 0, [(200, 1000)])

    def test_actual_overlap_and_exception_are_exported(self):
        doc = Document('Tabela.md', 'a' * 64, [Page(1, 'native', [
            Block('text', 'antes ' * 80),
            Block('table', rows=[['célula ' * 100]]),
            Block('text', 'depois ' * 80)])])
        result = chunks.generate_chunks(doc, render(doc), size_chars=300, overlap_chars=60)
        self.assertEqual(result[0]['actual_overlap_chars'], 0)
        for previous, current in zip(result, result[1:]):
            actual = previous['char_end'] - current['char_start']
            self.assertEqual(current['actual_overlap_chars'], actual)
            if actual != 60:
                self.assertIn(current['overlap_adjustment'], {'word_boundary', 'atomic_boundary', 'word_and_atomic_boundary'})
                self.assertIn('chunk_overlap_reduced', current['warnings'])
        self.assertTrue(any(c['actual_overlap_chars'] < 60 for c in result[1:]))

    def test_no_phantom_inline_atom_across_paragraphs_or_fences(self):
        text = 'Unmatched ` before\n\n' + 'prose ' * 150 + '\n\n```txt\n` inside\n```\n\nAfter ` unmatched'
        atoms = chunks._protected(text, [])
        fence_start = text.index('```txt')
        fence_end = text.index('```\n\nAfter') + 4
        self.assertEqual(atoms, [(fence_start, fence_end)])

    def test_real_multiline_inline_span_stays_whole(self):
        text = 'before `long\ninline` after'
        self.assertEqual(chunks._protected(text, []), [(7, 20)])

    def test_stream_writer_does_not_call_list_api_and_stops_on_quota(self):
        doc = self.document('prosa ' * 1000)
        consumed = []
        original = chunks._iter_chunks
        def tracked(*args, **kwargs):
            for chunk in original(*args, **kwargs):
                consumed.append(chunk['index'])
                yield chunk
        with tempfile.TemporaryDirectory() as tmp, patch.object(chunks, 'generate_chunks', side_effect=AssertionError('eager API')), patch.object(chunks, '_iter_chunks', tracked):
            folder = Path(tmp) / 'chunks'
            with self.assertRaisesRegex(ValueError, 'limite de saída'):
                chunks.write_chunks(doc, render(doc), folder, size_chars=256, overlap_chars=250, max_output_bytes=100)
            self.assertFalse(folder.exists())
            self.assertEqual(consumed, [1])

    def test_worker_passes_only_remaining_output_budget_to_chunks(self):
        from traslado import worker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.txt'
            source.write_text('texto ' * 100)
            workspace = root / 'package'
            workspace.mkdir()
            request = root / 'request.json'
            options = Options(chunks=True, max_output_bytes=20000)
            request.write_text(json.dumps({'workspace': str(workspace), 'snapshot': str(source),
                'source_name': 'Input.txt', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'options': asdict(options)}))
            original = chunks.write_chunks
            budgets = []
            def observed(*args, **kwargs):
                used = sum(p.stat().st_size for p in workspace.rglob('*') if p.is_file())
                self.assertEqual(kwargs['max_output_bytes'], options.max_output_bytes - used)
                budgets.append(kwargs['max_output_bytes'])
                return original(*args, **kwargs)
            with patch.object(sys, 'argv', ['worker', str(request)]), patch.object(chunks, 'write_chunks', observed):
                self.assertEqual(worker.main(), 0)
            self.assertEqual(len(budgets), 1)
            self.assertLess(budgets[0], options.max_output_bytes)

    def test_sparse_table_expansion_is_bounded_before_rendering(self):
        rows = [['x'] * 1001] + [[] for _ in range(1000)]
        with self.assertRaisesRegex(ValueError, 'células'):
            table_markdown(rows)

    def test_configured_table_budget_is_applied_to_padding(self):
        doc = Document('Ragged.csv', 'a' * 64, [Page(1, 'native', [Block('table', rows=[['a', 'b'], []])])], options={'max_cells': 3})
        with self.assertRaisesRegex(ValueError, 'células'):
            render(doc)

    def test_published_overlap_manifest_and_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'Parecer.txt'
            source.write_text(('palavra ' * 162 + 'fim.\n\n') * 30, encoding='utf-8')
            result = execute(source, root / 'out', Options(chunks=True))
            self.assertEqual(result['status'], 'complete', result)
            folder = Path(result['output']) / 'chunks'
            records = [json.loads(line) for line in (folder / 'chunks.jsonl').read_text().splitlines()]
            self.assertTrue(all(593 <= c['actual_overlap_chars'] <= 600 for c in records[1:]))
            markdown = (folder.parent / 'document.md').read_text()
            for c in records[1:]:
                start = c['char_start']
                self.assertTrue(markdown[start].isspace() or markdown[start - 1].isspace())
            summary = json.loads((folder / 'manifest.json').read_text())
            self.assertEqual(summary['overlap_reduced_transitions'], sum(c['actual_overlap_chars'] < 600 for c in records[1:]))
            self.assertEqual(summary['actual_overlap_min_chars'], min(c['actual_overlap_chars'] for c in records[1:]))
            self.assertIn('actual_overlap_chars: ' + str(records[1]['actual_overlap_chars']), (folder / '0002.md').read_text())

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group contract')
    def test_normal_parent_exit_kills_descendants_in_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinel = root / 'survivor.txt'
            child = 'import time,pathlib;time.sleep(.4);pathlib.Path(' + repr(str(sentinel)) + ').write_text("survived")'
            parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",' + repr(child) + '])'
            self.assertEqual(run_bounded([sys.executable, '-c', parent], root, 5, root / 'log'), 0)
            time.sleep(.6)
            self.assertFalse(sentinel.exists(), 'descendant outlived supervised parent')


if __name__ == '__main__':
    unittest.main()
