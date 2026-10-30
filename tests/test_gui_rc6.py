# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Physical-window/DPI simulations and complete-path result accessibility."""
from pathlib import Path
import faulthandler
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from traslado.gui import App



def setUpModule():
    # Vigia do módulo: travamento do Tk vira traceback de todas as threads e falha, em vez de consumir o job.
    faulthandler.dump_traceback_later(600, exit=True)


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()


class GUIRc6(unittest.TestCase):
    def make_app(self, dpi=1.0, size=(1366,768)):
        root=tk.Tk()
        self.addCleanup(self.destroy,root)
        root.tk.call("tk","scaling",(96/72)*dpi)
        with patch.object(App,"check_installation",lambda self:None):
            app=App(root)
        root.geometry(f"{size[0]}x{size[1]}+20+20")
        root.update()
        return root,app

    @staticmethod
    def destroy(root):
        try:
            for callback in root.tk.splitlist(root.tk.call("after","info")):
                root.after_cancel(callback)
            root.destroy()
        except tk.TclError:
            pass

    def add_results(self,root,app,count=12):
        with patch.object(App,"_read_preview",return_value="SYNTHETIC_PREVIEW"):
            for index in range(count):
                result={"source":f"C:/Documentos/Pontes de Miranda, Francisco Cavalcanti, Dez Anos de Pareceres, 1975, volume {index+1}.pdf",
                        "status":"partial","output":f"C:/Freoa/resultados/Documento completo volume {index+1}","warnings":1}
                app.events.put(("result",result,"",index+1))
            app.poll()
            app.notebook.select(1)
            root.update()

    def test_dpi_matrix_has_nonoverlapping_rows_and_accessible_actions(self):
        for size in [(1366,768),(1920,1080)]:
            for dpi in [1.0,1.5,2.0]:
                with self.subTest(size=size,dpi=dpi):
                    root,app=self.make_app(dpi,size)
                    self.add_results(root,app)
                    font_height=app._body_font.metrics("linespace")
                    row_height=int(ttk.Style(root).lookup("Results.Treeview","rowheight"))
                    self.assertGreaterEqual(row_height,font_height+8)
                    last=app.results.get_children()[-1]
                    bbox=app.results.bbox(last)
                    self.assertTrue(bbox,"At least the selected row must remain visible")
                    self.assertGreaterEqual(bbox[3],font_height+8)
                    self.assertLessEqual(app.subtitle.winfo_rooty()+app.subtitle.winfo_height(),
                                         app.header.winfo_rooty()+app.header.winfo_height())
                    self.assertGreaterEqual(app.results.winfo_height(),row_height+font_height)
                    self.assertLessEqual(app.notebook.winfo_rooty()+app.notebook.winfo_height(),
                                         root.winfo_rooty()+root.winfo_height())
                    for button in [app.convert_button,app.cancel_button,app.open_button,app.doctor_button]:
                        self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),root.winfo_rootx()+root.winfo_width())
                        self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),root.winfo_rooty()+root.winfo_height())
                    self.assertTrue(app.result_x_scroll.winfo_ismapped())
                    self.assertTrue(app.result_y_scroll.winfo_ismapped())
                    self.destroy(root)

    def test_selected_result_keeps_complete_paths_and_limits_column_error(self):
        root,app=self.make_app(1.5)
        source="C:/Documentos/"+"nome extenso "*30+".pdf"
        error="DETAIL_ERROR_"*1000
        app.events.put(("result",{"source":source,"status":"failed","output":None,"error":error},"",1))
        app.poll(); root.update()
        row=app.results.get_children()[0]
        self.assertLess(len(app.results.item(row,"values")[2]),100)
        self.assertIn(source,app.result_detail.get())
        self.assertIn("DETAIL_ERROR",app.detail_text.get("1.0","end"))
        self.assertLess(len(app.result_detail.get()),len(source)+6200)
        self.assertFalse(bool(app.results.column("status","stretch")))

    def test_advanced_configuration_and_scrolling_keep_controls_reachable(self):
        root,app=self.make_app(2.0)
        app._toggle_advanced(); root.update()
        self.assertTrue(app.chunk_size_entry.winfo_ismapped())
        app.language_entry.focus_force(); root.update()
        canvas=app.settings_canvas
        self.assertGreaterEqual(app.language_entry.winfo_rooty(),canvas.winfo_rooty())
        self.assertLessEqual(app.language_entry.winfo_rooty()+app.language_entry.winfo_height(),
                             canvas.winfo_rooty()+canvas.winfo_height())
        app.chunk_size.set("512"); app.chunk_overlap.set("64")
        self.assertEqual(app.conversion_options().chunk_size_chars,512)
        app._toggle_advanced(); root.update()
        self.assertFalse(app.chunk_size_entry.winfo_ismapped())
        self.assertNotIn("disabled",app.overwrite_box.state())

    def test_wrapping_tracks_actual_window_width(self):
        root,app=self.make_app(1.5,(1920,1080))
        large=int(app.subtitle.cget("wraplength"))
        root.geometry("1000x768"); root.update()
        self.assertLess(int(app.subtitle.cget("wraplength")),large)
        for label in app._wrap_labels:
            self.assertLessEqual(int(label.cget("wraplength")),root.winfo_width())

    def test_middle_ellipsis_distinguishes_volume_tails_and_keeps_original(self):
        root,app=self.make_app(1.5)
        names=[f"Pontes de Miranda, Francisco Cavalcanti, Dez Anos de Pareceres, Francisco Alves, 1975, volume {index}.pdf" for index in range(1,6)]
        for index,name in enumerate(names,1):
            app.events.put(("result",{"source":"C:/Documentos/"+name,"status":"failed","output":None},"",index))
        app.poll(); root.update()
        rows=app.results.get_children()
        displayed=[app.results.set(row,"file") for row in rows]
        self.assertEqual(len(set(displayed)),5)
        for index,(name,label) in enumerate(zip(names,displayed),1):
            self.assertTrue(label.startswith("Pontes"))
            self.assertTrue(label.endswith(f"volume {index}.pdf"))
            self.assertIn("1975",label)
            self.assertIn(" … ",label)
            self.assertLessEqual(app._body_font.measure(label),int(app.results.column("file","width"))-16)
            self.assertEqual(Path(app._row_results[rows[index-1]]["source"]).name,name)
        self.assertEqual(app._display_filename("curto.pdf"),"curto.pdf")
        app.results.column("file",width=2200)
        app._refresh_result_names(); root.update()
        self.assertEqual(app.results.set(rows[0],"file"),names[0])
        app.results.column("file",width=500)
        # Clique completo (pressionar e soltar) sobre o cabeçalho, como no redimensionamento real da coluna: o Tk 9
        # do macOS descarta ButtonRelease sem ButtonPress correspondente.
        app.results.event_generate("<ButtonPress-1>",x=5,y=5); app.results.event_generate("<ButtonRelease-1>",x=5,y=5); root.update()
        self.assertIn(" … ",app.results.set(rows[0],"file"))

    # Altura de janela medida (Windows, Tk escala 2.0, fontes padrão) em que duas linhas de resultado cabem: 710 px
    # (telas simuladas de 776 a 796 px de altura mostraram 1 linha; 800 px mostrou 2). A janela usa a altura da tela menos 90.
    ALTURA_JANELA_PARA_DUAS_LINHAS_2X=710
    MARGEM_DE_TELA=90
    # Atenção: em telas abaixo de 800 px de altura (ex.: runner Windows 1024x768) este método é pulado e fica SEM as verificações
    # de duas linhas, altura da linha e botões; elas só rodam em tela maior (máquina local, Linux/xvfb).

    def test_high_dpi_minimum_window_preserves_two_results_and_screen_margin(self):
        root,app=self.make_app(2.0,(900,650))
        # Pré-condição de ambiente, medida só pela tela e avaliada antes de qualquer asserção sobre o layout.
        if root.winfo_screenheight()-self.MARGEM_DE_TELA<self.ALTURA_JANELA_PARA_DUAS_LINHAS_2X:
            self.skipTest(f"tela {root.winfo_screenwidth()}x{root.winfo_screenheight()} abaixo de {self.ALTURA_JANELA_PARA_DUAS_LINHAS_2X+self.MARGEM_DE_TELA} px de altura: não há espaço para duas linhas em escala 2x")
        self.add_results(root,app)
        self.assertGreaterEqual(root.winfo_height(),650)  # o gerenciador de janelas pode não crescer além do pedido; o critério é o de duas linhas visíveis, abaixo
        self.assertLessEqual(root.minsize()[1],root.winfo_screenheight()-self.MARGEM_DE_TELA)
        rows=app.results.get_children()
        visible=[app.results.bbox(row) for row in rows if app.results.bbox(row)]
        self.assertGreaterEqual(len(visible),2,f"tela {root.winfo_screenwidth()}x{root.winfo_screenheight()}, janela {root.winfo_width()}x{root.winfo_height()}, minsize {root.minsize()}")
        self.assertGreaterEqual(visible[-1][3],app._body_font.metrics("linespace")+8)
        for button in [app.convert_button,app.cancel_button,app.open_button,app.doctor_button]:
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),root.winfo_rooty()+root.winfo_height())


if __name__=="__main__":
    unittest.main()
