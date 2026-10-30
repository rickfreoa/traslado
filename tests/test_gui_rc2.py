# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations
import faulthandler
import gc

from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
    from traslado.gui import App
except ImportError:
    tk = None
    App = None

def esperar_no_mainloop(root, condition, timeout):
    """Espera a condição girando o mainloop (como o produto), não por laço de root.update(): no Tk 8.6 do macOS,
    update() repetido em laço trava de forma intermitente no runner do CI. Devolve True se a condição ocorreu."""
    deadline = time.monotonic() + timeout
    estado = {"ok": False}
    def tick():
        if condition():
            estado["ok"] = True
            root.quit()
        elif time.monotonic() > deadline:
            root.quit()
        else:
            root.after(5, tick)
    root.after(0, tick)
    root.mainloop()
    return estado["ok"]

@unittest.skipIf(tk is None, 'Tk unavailable')
class GUIRC2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Uma só Tk por classe e uma Toplevel por teste: criar e destruir várias Tk no mesmo processo trava
        # root.update() no Tk do macOS (visto no CI, sempre no 2.º ou 3.º teste da classe).
        try:
            cls.raiz = tk.Tk()
            cls.raiz.withdraw()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.raiz.destroy()

    def setUp(self):
        # Vigia: travamento do Tk (visto no macOS do CI) vira traceback de todas as threads e falha, não timeout mudo.
        faulthandler.dump_traceback_later(180, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        # Dispose destroyed Tk fixture cycles on the UI thread before worker allocations.
        gc.collect()
        self.root = tk.Toplevel(self.raiz)
        self.root.withdraw()
        with patch.object(App, 'check_installation', lambda self: None):
            self.app = App(self.root)
        # Lixo cíclico da construção (variáveis Tk) coletado na thread da interface e o restante congelado:
        # finalizar Variable numa thread de trabalho (leitura da prévia) trava o Tk no macOS.
        gc.collect()
        gc.freeze()
        self.addCleanup(gc.unfreeze)
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        if hasattr(self, 'root'):
            for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
                self.root.after_cancel(callback)
            self.root.destroy()
        if hasattr(self, 'temporary'):
            self.temporary.cleanup()

    def pump_until(self, condition, timeout=3):
        if not esperar_no_mainloop(self.root, condition, timeout):
            self.fail('Tk event condition timed out')

    def text(self):
        return self.app.preview.get('1.0', 'end').strip()

    def result(self, name, text=None, status='complete'):
        output = None
        if text is not None:
            package = self.folder / name
            package.mkdir()
            (package / 'document.md').write_text(text, encoding='utf-8')
            output = str(package)
        return {'source': name, 'status': status, 'output': output,
                'error': 'SYNTHETIC_FAILURE' if status == 'failed' else None}

    def add_result(self, result):
        count = len(self.app.results.get_children()) + 1
        self.app.events.put(('result', result, '', count))
        self.app.poll()
        return self.app.results.get_children()[-1]

    def test_success_then_failure_clears_previous_preview_and_labels_source(self):
        self.add_result(self.result('first.txt', 'FIRST_SOURCE_SENTINEL'))
        self.pump_until(lambda: self.text() == 'FIRST_SOURCE_SENTINEL')
        failed = self.add_result(self.result('failed.pdf', status='failed'))
        self.assertEqual(self.app.results.selection(), (failed,))
        self.assertNotIn('FIRST_SOURCE_SENTINEL', self.text())
        self.assertIn('failed.pdf', self.text())
        self.assertIn('SYNTHETIC_FAILURE', self.text())
        self.assertIn('failed.pdf', self.app.preview_label.get())

    def test_tree_selection_restores_matching_previous_document(self):
        first = self.add_result(self.result('first.txt', 'FIRST_SOURCE_SENTINEL'))
        self.pump_until(lambda: self.text() == 'FIRST_SOURCE_SENTINEL')
        second = self.add_result(self.result('second.txt', 'SECOND_SOURCE_SENTINEL', status='partial'))
        self.pump_until(lambda: self.text() == 'SECOND_SOURCE_SENTINEL')
        # Janela mapeada: no Tk 9 do macOS (Python 3.13+ no macos-15), eventos gerados numa Toplevel retirada
        # (withdraw) não chegam aos bindings; a interface real só recebe seleção com a janela visível.
        self.root.deiconify()
        self.root.update_idletasks()
        self.app.results.selection_set(first)
        self.app.results.event_generate('<<TreeviewSelect>>')
        self.pump_until(lambda: self.text() == 'FIRST_SOURCE_SENTINEL')
        self.assertIn('first.txt', self.app.preview_label.get())
        self.app.results.selection_set(second)
        self.app.results.event_generate('<<TreeviewSelect>>')
        self.pump_until(lambda: self.text() == 'SECOND_SOURCE_SENTINEL')
        self.assertIn('second.txt', self.app.preview_label.get())

    def test_delayed_previous_read_cannot_overwrite_new_selection(self):
        started = threading.Event()
        release = threading.Event()
        late_enqueued = threading.Event()
        threads = []
        original_put = self.app.events.put
        def put(event):
            original_put(event)
            if event[0] == 'preview' and event[3] == 'STALE_FIRST_SENTINEL':
                late_enqueued.set()
        def read(output):
            threads.append(threading.get_ident())
            if output.endswith('first.txt'):
                started.set()
                release.wait(3)
                return 'STALE_FIRST_SENTINEL'
            return 'CURRENT_SECOND_SENTINEL'
        try:
            with patch.object(App, '_read_preview', side_effect=read), patch.object(self.app.events, 'put', side_effect=put):
                self.add_result(self.result('first.txt', 'first'))
                self.pump_until(started.is_set)
                self.add_result(self.result('second.txt', 'second'))
                self.pump_until(lambda: self.text() == 'CURRENT_SECOND_SENTINEL')
                release.set()
                self.pump_until(late_enqueued.is_set)
                # Drain the late response through the actual Tk polling handler.
                self.app.poll()
                self.assertEqual(self.text(), 'CURRENT_SECOND_SENTINEL')
                self.assertIn('second.txt', self.app.preview_label.get())
                self.assertTrue(all(worker != threading.get_ident() for worker in threads))
        finally:
            release.set()

    def test_preview_read_is_bounded_and_metadata_cache_contains_no_text(self):
        # Janela visível e com tamanho real: com a raiz retirada (withdraw), o Text não recebe largura e a quebra por
        # palavra de 128 KiB vira centenas de milhares de linhas de exibição; no macOS o cálculo passa de minutos.
        self.root.deiconify()
        self.root.geometry('1100x800')
        self.root.update()
        row = self.add_result(self.result('large.txt', 'x' * (129 * 1024)))
        self.pump_until(lambda: 'Prévia truncada' in self.text())
        self.assertLess(len(self.text().replace('\n', '')), 128 * 1024 + 100)
        self.assertLessEqual(max(len(linha) for linha in self.text().splitlines()), 200)  # sequência sem espaço quebrada só na prévia
        self.assertEqual(set(self.app._row_results[row]), {'source', 'status', 'output', 'error'})

if __name__ == '__main__':
    unittest.main()
