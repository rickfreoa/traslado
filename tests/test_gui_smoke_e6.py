# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Fumaça real de GUI por evento sintético (E6): abre janela, preenche entrada/saída,
dispara conversão de fixture pelo botão, confere resultado no disco e no preview e encerra com timeout."""
from __future__ import annotations

import faulthandler
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get("T11_EXIGE_INSTALADO") and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

try:
    import tkinter as tk
    from traslado.gui import App
except ImportError:
    tk = None
    App = None

import traslado
ASSETS = Path(traslado.__file__).resolve().parent / "assets"



def setUpModule():
    # Vigia do módulo: travamento do Tk vira traceback de todas as threads e falha, em vez de consumir o job.
    faulthandler.dump_traceback_later(600, exit=True)


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()


@unittest.skipIf((tk is None or App is None) and not os.environ.get("T11_EXIGE_INSTALADO"), "Tkinter indisponível neste interpretador")
class GuiSmokeSyntheticEventE6(unittest.TestCase):
    def test_janela_real_evento_sintetico_converte_fixture_e_encerra_com_timeout(self) -> None:
        self.assertIsNotNone(tk, "Tkinter obrigatório na matriz de CI")
        self.assertIsNotNone(App, "GUI obrigatória na matriz de CI")
        with tempfile.TemporaryDirectory(prefix="e6-gui-smoke-") as tmp:
            base = Path(tmp)
            in_dir = base / "entrada"
            out_dir = base / "saida"
            in_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            if importlib.util.find_spec("docx") is not None and (ASSETS / "fixture.docx").is_file():
                fixture_path = in_dir / "fixture.docx"
                shutil.copyfile(ASSETS / "fixture.docx", fixture_path)
            else:
                fixture_path = in_dir / "fixture.txt"
                fixture_path.write_text("# Fumaça GUI E6\n\nTexto convertido por evento sintético.\n", encoding="utf-8")

            root = tk.Tk()
            app = App(root)
            try:
                root.deiconify()
                root.update_idletasks()
                root.update()

                # Aguarda término da checagem inicial de ambiente (doctor) para habilitar inputs
                init_deadline = time.monotonic() + 60.0
                while time.monotonic() < init_deadline:
                    root.update()
                    if not app.busy:
                        break
                    time.sleep(0.02)
                else:
                    self.fail("Timeout (60s) aguardando checagem inicial de ambiente da GUI")

                # Preenchimento por widgets de entrada e geração de eventos sintéticos
                app.source_entry.focus_force()
                app.source_entry.delete(0, "end")
                app.source_entry.insert(0, str(fixture_path))
                app.source_entry.event_generate("<KeyRelease>")

                app.output_entry.focus_force()
                app.output_entry.delete(0, "end")
                app.output_entry.insert(0, str(out_dir))
                app.output_entry.event_generate("<KeyRelease>")
                root.update()

                self.assertEqual(Path(app.source.get()), fixture_path)
                self.assertEqual(Path(app.output.get()), out_dir)

                # Disparo real pelo botão Converter agora
                app.convert_button.invoke()

                deadline = time.monotonic() + 60.0
                while time.monotonic() < deadline:
                    root.update()
                    if not app.busy and len(app._row_results) >= 1:
                        break
                    time.sleep(0.02)
                else:
                    self.fail("Timeout (60s) aguardando término da conversão na GUI")

                rows = app.results.get_children()
                self.assertEqual(len(rows), 1)
                app.results.selection_set(rows[0])
                app._select_result()

                # Aguarda carregamento assíncrono do preview com timeout
                preview_deadline = time.monotonic() + 10.0
                preview_txt = ""
                while time.monotonic() < preview_deadline:
                    root.update()
                    preview_txt = app.preview.get("1.0", "end").strip()
                    if preview_txt and not preview_txt.startswith("Carregando a prévia"):
                        break
                    time.sleep(0.02)

                res = app._row_results[rows[0]]
                self.assertIn(res.get("status"), {"complete", "partial", "ok"}, res)
                pacote = Path(res["output"])
                doc_md = pacote / "document.md"
                manifest_json = pacote / "manifest.json"
                self.assertTrue(doc_md.is_file(), f"document.md ausente em {pacote}")
                self.assertTrue(manifest_json.is_file(), f"manifest.json ausente em {pacote}")
                conteudo_md = doc_md.read_text(encoding="utf-8")
                self.assertTrue(conteudo_md.strip(), "document.md vazio")
                manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
                self.assertIn(fixture_path.name, manifest_json.read_text(encoding="utf-8"))

                self.assertTrue(preview_txt, "Preview da GUI ficou vazio após conversão")
            finally:
                try:
                    app.close()
                except Exception:
                    pass
                try:
                    root.destroy()
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
