# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations
import faulthandler
import gc

from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from traslado.gui import App, ACCENT, _read_configuration


REPORT = {"capabilities": {name: {"available": True} for name in ("fitz", "PIL", "docx", "openpyxl", "pptx")},
          "tesseract": "managed-tesseract", "languages": ["por", "eng", "spa", "ita", "fra", "deu"], "warnings": []}


class GUIRc3(unittest.TestCase):
    def setUp(self):
        # Vigia: travamento do Tk vira traceback de TODAS as threads e falha em 180 s, em vez de consumir o job.
        faulthandler.dump_traceback_later(180, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        # Dispose destroyed Tk fixture cycles on the UI thread before worker allocations.
        gc.collect()
        try:
            self.root=tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        with patch.object(App,"check_installation",lambda self:None):
            self.app=App(self.root)
        self.temp=tempfile.TemporaryDirectory(prefix="freoa-gui-rc3-")
        self.folder=Path(self.temp.name)

    def tearDown(self):
        if hasattr(self,"root"):
            try:
                for callback in self.root.tk.splitlist(self.root.tk.call("after","info")):
                    self.root.after_cancel(callback)
                self.root.destroy()
            except tk.TclError:
                pass
        if hasattr(self,"temp"):
            self.temp.cleanup()

    def pump(self,condition,timeout=5):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            self.root.update()
            if condition():return
            time.sleep(.005)
        self.fail("Tk condition timed out")

    def source(self):
        path=self.folder/"entrada.txt"
        path.write_text("DOCUMENTO_SINTETICO 12345",encoding="utf-8")
        self.app.source.set(str(path))
        self.app.output.set(str(self.folder/"resultados"))
        self.app.input_kind.set("file")
        return path

    def test_familiar_sections_and_disabled_legacy_actions_are_visible(self):
        def labels(widget):
            found=[]
            for child in widget.winfo_children():
                try:found.append(str(child.cget("text")))
                except tk.TclError:pass
                found.extend(labels(child))
            return found
        texts=labels(self.root)
        for text in ("Traslado","1. Escolha o que deseja converter","Arquivo individual",
                     "Pasta inteira","Pasta de destino","2. Qualidade automática","Converter agora",
                     "Abrir pasta de destino","Configuração TOML (opcional)"):
            self.assertIn(text,texts)
        self.assertEqual(self.app.header.cget("bg"),ACCENT)
        self.assertEqual(self.app.notebook.tab(self.app.notebook.select(),"text"),"Registro da execução")
        self.assertTrue(self.app.recursive.get())
        self.assertTrue(self.app.chunk.get())
        self.assertFalse(self.app.overwrite.get())
        self.app.set_busy(True)
        self.app.set_busy(False)
        self.assertNotIn("disabled",self.app.chunk_box.state())
        self.assertNotIn("disabled",self.app.overwrite_box.state())

    def test_log_has_useful_height_in_720_pixel_window(self):
        self.root.geometry("980x720")
        self.root.deiconify()
        self.root.update()
        self.assertGreaterEqual(self.app.log.winfo_height(),100)
        self.assertLessEqual(self.app.notebook.winfo_rooty()+self.app.notebook.winfo_height(),
                             self.root.winfo_rooty()+self.root.winfo_height())

    def test_language_default_comes_from_managed_runtime(self):
        with patch("traslado.gui.default_language",return_value="eng"), patch.object(App,"check_installation",lambda self:None):
            second_root=tk.Toplevel(self.root)
            second_root.withdraw()
            # App uses the same Tk-compatible root API with a Toplevel.
            second=App(second_root)
            self.assertEqual(second.language.get(),"eng")
            self.assertEqual(second.conversion_options().language,"eng")
            for callback in second_root.tk.splitlist(second_root.tk.call("after","info")):
                second_root.after_cancel(callback)
            second_root.destroy()

    def test_file_folder_picker_and_safe_default_destination(self):
        source=self.source()
        self.app.output.set("")
        with patch("traslado.gui.filedialog.askopenfilename",return_value=str(source)):
            self.app.choose_file()
        self.assertEqual(self.app.input_kind.get(),"file")
        self.assertEqual(Path(self.app.output.get()),source.parent/"Markdown_convertido")
        folder=self.folder/"entrada-pasta"
        folder.mkdir()
        self.app.output.set("")
        self.app.input_kind.set("folder")
        with patch("traslado.gui.filedialog.askdirectory",return_value=str(folder)), patch("traslado.gui.filedialog.askopenfilename") as file_dialog:
            self.app.browse_input()
            file_dialog.assert_not_called()
        output=Path(self.app.output.get()).resolve()
        self.assertNotEqual(output,folder.resolve())
        self.assertNotIn(folder.resolve(),output.parents)

    def test_toml_controls_and_limits_apply_without_yaml_translation(self):
        config=self.folder/"config.toml"
        config.write_text('[conversion]\nmode="native"\nlanguage="eng"\njob_timeout=7\nocr_timeout=3\n',encoding="utf-8")
        with patch("traslado.gui.filedialog.askopenfilename",return_value=str(config)):
            self.app.choose_config()
        options=self.app.conversion_options()
        self.assertEqual((options.mode,options.language,options.job_timeout,options.ocr_timeout),("native","eng",7,3))
        yaml=self.folder/"legacy.yaml"
        yaml.write_text("conversion:\n  engine: native",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"YAML.*não é compatível"):
            _read_configuration(str(yaml))
        config.write_bytes(b"x"*(64*1024+1))
        with self.assertRaisesRegex(ValueError,"limite"):
            _read_configuration(str(config))

    def test_blank_quoted_input_does_not_mean_current_folder(self):
        self.app.source.set('""')
        self.app.output.set(str(self.folder/"out"))
        self.app.input_kind.set("folder")
        with patch("traslado.gui.messagebox.showerror") as error, patch.object(App,"convert") as convert:
            self.app.start()
        error.assert_called_once()
        convert.assert_not_called()
        self.assertFalse(self.app.busy)

    def test_actual_native_conversion_updates_log_results_and_preview(self):
        source=self.source()
        original=source.read_bytes()
        self.app.mode.set("native")
        with patch("traslado.gui.inspect",return_value=REPORT):
            self.app.start()
            self.pump(lambda:not self.app.busy and len(self.app.results.get_children())==1)
            self.pump(lambda:"DOCUMENTO_SINTETICO 12345" in self.app.preview.get("1.0","end"))
        self.assertEqual(source.read_bytes(),original)
        row=self.app.results.get_children()[0]
        self.assertEqual(self.app.results.item(row,"values")[1],"Concluído")
        log=self.app.log.get("1.0","end")
        self.assertIn("entrada.txt",log)
        self.assertIn("Concluído",log)
        self.assertIn("entrada.txt",self.app.preview_label.get())

    def test_cancel_reaches_worker_and_keeps_failed_preview_separate(self):
        self.source()
        entered=threading.Event()
        def execute(source,output,options,cancel,progress=None):
            entered.set()
            until=time.monotonic()+3
            while not cancel() and time.monotonic()<until:time.sleep(.005)
            return {"source":str(source),"status":"cancelled","output":None,"error":"CANCEL_SENTINEL"}
        with patch("traslado.gui.inspect",return_value=REPORT),patch("traslado.gui.execute",side_effect=execute):
            self.app.start()
            self.pump(entered.is_set)
            self.assertIn("disabled",self.app.source_entry.state())
            self.app.cancel()
            self.pump(lambda:not self.app.busy and len(self.app.results.get_children())==1)
        self.assertIn("Cancelado",self.app.log.get("1.0","end"))
        self.assertIn("CANCEL_SENTINEL",self.app.preview.get("1.0","end"))
        self.assertNotIn("disabled",self.app.source_entry.state())

    def test_chunks_controls_configure_real_output(self):
        self.source()
        self.app.chunk_size.set("512")
        self.app.chunk_overlap.set("64")
        with patch("traslado.gui.inspect",return_value=REPORT):
            self.app.start()
            self.pump(lambda:not self.app.busy and len(self.app.results.get_children())==1)
        import json
        result = next(iter(self.app._row_results.values()))
        folder = Path(result["output"])
        manifest = json.loads((folder/"chunks/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["size_chars"],manifest["overlap_chars"]),(512,64))
        self.assertTrue((folder/"chunks/0001.md").is_file())
        self.assertEqual(folder.name,"entrada")

    def test_page_progress_and_warning_summary_are_readable(self):
        self.app.events.put(("page_progress",{"page":12,"total":300,"route":"ocr"}))
        self.app.poll()
        self.assertIn("Página 12 de 300",self.app.status.get())
        self.app.events.put(("result",{"source":"scan.pdf","status":"partial","output":None,
            "warnings":2,"warning_summary":{"ocr_empty":1,"ocr_low_confidence":1},
            "routes":{"ocr":300}},"",1))
        self.app.poll()
        log=self.app.log.get("1.0","end")
        self.assertIn("LEIA-AVISOS.txt",log)
        self.assertIn("baixa confiança",log)
        self.assertIn("ocr: 300",log)

    def test_environment_probe_runs_off_tk_thread(self):
        entered=threading.Event()
        release=threading.Event()
        threads=[]
        def inspect(*args):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return REPORT
        try:
            with patch("traslado.gui.inspect",side_effect=inspect):
                self.app.check_installation()
                self.pump(entered.is_set)
                heartbeat=[]
                self.root.after(0,lambda:heartbeat.append("alive"))
                self.pump(lambda:bool(heartbeat))
                self.assertTrue(self.app.busy)
                release.set()
                self.pump(lambda:not self.app.busy)
            self.assertTrue(all(t!=threading.get_ident() for t in threads))
            self.assertIn("fra",self.app.capabilities.get())
        finally:release.set()


if __name__=="__main__":unittest.main()
