# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""User-requested word boundaries for the repeated chunk prefix."""
import unittest
from traslado.chunks import _ranges, _overlap_start, generate_chunks
from traslado.model import Block, Document, Page
from traslado.render import render


class WordOverlapRC8(unittest.TestCase):
    def test_bisected_word_advances_past_space(self):
        self.assertEqual(_overlap_start('abc defgh ijk', 0, 12, 5, []), (10, 'word_boundary'))

    def test_space_is_skipped_and_word_start_keeps_exact_overlap(self):
        text = 'abc defgh ijk'
        self.assertEqual(_overlap_start(text, 0, 12, 9, []), (4, 'word_boundary'))
        self.assertEqual(_overlap_start(text, 0, 12, 8, []), (4, 'none'))

    def test_unicode_whitespace_and_hyphenated_words(self):
        for whitespace in (' ', '\t', '\n', '\u00a0', '\u2003'):
            text = 'ação-direito' + whitespace + 'depois'
            self.assertEqual(_overlap_start(text, 0, len(text), 12, []), (13, 'word_boundary'))

    def test_long_word_can_reduce_overlap_to_zero_without_gaps(self):
        text = 'x' * 2000
        ranges = _ranges(text, 300, 60, [])
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(text))
        for previous, current in zip(ranges, ranges[1:]):
            self.assertEqual(previous[1], current[0])
            self.assertGreater(current[1], previous[1])
        doc = Document('Long.txt', 'a' * 64, [Page(1, 'native', [Block('text', text)])])
        records = generate_chunks(doc, render(doc), size_chars=300, overlap_chars=60)
        self.assertTrue(any(c['actual_overlap_chars'] == 0 and c['overlap_adjustment'] == 'word_boundary' for c in records[1:]))

    def test_word_alignment_cannot_land_inside_inline_atom(self):
        text = 'abcdefgh `literal code` after'
        begin = text.index('`')
        end = text.rindex('`') + 1
        start, reason = _overlap_start(text, 0, len(text), len(text) - 12, [(begin, end)])
        self.assertEqual(start, end + 1)
        self.assertEqual(reason, 'word_and_atomic_boundary')

    def test_word_then_atom_and_atom_then_word_are_both_checked(self):
        self.assertEqual(_overlap_start('abc def ghi jkl', 0, 14, 12, [(2, 7)]), (8, 'word_and_atomic_boundary'))
        self.assertEqual(_overlap_start('abcde more rest', 0, 15, 13, [(1, 4)]), (6, 'word_and_atomic_boundary'))

    def test_zero_requested_overlap_does_not_skip_source(self):
        self.assertEqual(_overlap_start('abcdefghijk', 0, 6, 0, []), (6, 'none'))


if __name__ == '__main__':
    unittest.main()
