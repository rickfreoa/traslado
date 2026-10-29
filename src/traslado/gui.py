# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Local desktop UI. Extraction runs off the Tk thread; preview is inert text."""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tomllib
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

from . import __version__
from .doctor import inspect
from .model import Options
from .runner import discover, execute
from .runtime import default_language, default_output_path
from .review import warning_label


STATUS = {"complete": "Concluído", "partial": "Parcial — revisar avisos", "failed": "Falhou",
          "cancelled": "Cancelado", "timeout": "Tempo esgotado"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
APP_TITLE = "Traslado"
ACCENT, ACCENT_DARK = "#A70F1B", "#7E0B14"
BG, CARD, TEXT, MUTED, BORDER = "#F4F4F4", "#FFFFFF", "#1F1F1F", "#666666", "#D9D9D9"
SUPPORTED_FILETYPES = [
    ("Documentos suportados", "*.docx *.pdf *.xlsx *.pptx *.csv *.tsv *.html *.htm *.json *.xml *.txt *.md *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
    ("Word", "*.docx"), ("PDF", "*.pdf"), ("Excel", "*.xlsx *.csv *.tsv"),
    ("PowerPoint", "*.pptx"), ("Texto e dados", "*.txt *.md *.json *.xml *.html *.htm"),
    ("Imagens", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"), ("Todos os arquivos", "*.*")]


def _read_configuration(path: str) -> dict:
    path=path.strip().strip('"')
    if Path(path).suffix.lower() in {".yaml", ".yml"}:
        raise ValueError("A configuração v6 usa TOML. O YAML da v5.5 não é compatível.")
    with Path(path).expanduser().open("rb") as reader:
        payload = reader.read(64 * 1024 + 1)
    if len(payload) > 64 * 1024:
        raise ValueError("Configuração excede o limite de 64 KB")
    data = tomllib.loads(payload.decode("utf-8"))
    if set(data) != {"conversion"} or not isinstance(data["conversion"], dict):
        raise ValueError("Use somente a seção [conversion] na configuração TOML")
    Options(**data["conversion"])
    return data["conversion"]


def capability_text(report: dict, language: str) -> str:
    capabilities = report.get("capabilities", {})
    names = {"fitz": "PDF", "PIL": "Imagens", "docx": "DOCX", "openpyxl": "XLSX", "pptx": "PPTX"}
    labels = [f'{label}: {"disponível" if capabilities.get(module, {}).get("available") else "instale Full"}'
              for module, label in names.items()]
    parts = [" · ".join(labels)]
    available = report.get("languages", [])
    missing = sorted(set(language.split("+")) - set(available))
    parts.append("OCR: " + ("Tesseract encontrado" if report.get("tesseract") else "Tesseract não encontrado"))
    parts.append("Idiomas OCR instalados: " + (", ".join(available) or "nenhum verificado"))
    if missing:
        parts.append("Idiomas solicitados ausentes: " + ", ".join(missing))
    parts.extend(report.get("warnings", []))
    return "\n".join(parts)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"{APP_TITLE} {__version__}")
        height = min(900, max(600, root.winfo_screenheight() - 90))
        width = min(1180, max(900, root.winfo_screenwidth() - 120))
        root.geometry(f"{width}x{height}")
        root.minsize(900, min(650, height))
        root.configure(bg=BG)
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.closing = False
        self._row_results: dict[str, dict] = {}
        self._preview_token = 0
        self._preview_row: str | None = None
        self.preview_label = tk.StringVar(master=root, value="Selecione um resultado para ver a prévia textual.")
        self.result_detail = tk.StringVar(master=root, value="Selecione um arquivo para consultar os caminhos completos.")
        self._wrap_labels = []
        self._layout_callback = None
        self._advanced_visible = False
        self._file_display_width = None
        self.source = tk.StringVar(master=root)
        self.output = tk.StringVar(master=root, value=str(default_output_path()))
        self.input_kind = tk.StringVar(master=root, value="file")
        self.config_path = tk.StringVar(master=root)
        self.mode = tk.StringVar(master=root, value="auto")
        self.language = tk.StringVar(master=root, value=default_language())
        self.recursive = tk.BooleanVar(master=root, value=True)
        self.chunk = tk.BooleanVar(master=root, value=True)
        self.chunk_size = tk.StringVar(master=root, value="6000")
        self.chunk_overlap = tk.StringVar(master=root, value="600")
        self.scan_previews = tk.BooleanVar(master=root, value=False)
        self.overwrite = tk.BooleanVar(master=root, value=False)
        self.status = tk.StringVar(master=root, value="Verificando instalação…")
        self.capabilities = tk.StringVar(master=root, value="Verificando capacidades e idiomas…")
        self._setup_style()
        self.header = tk.Frame(root, bg=ACCENT)
        self.header.pack(fill="x")
        ttk.Label(self.header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w", padx=28, pady=(10,0))
        self._subtitle_full="Converta arquivos e pastas em Markdown com seleção automática do método para PDFs."
        self.subtitle = ttk.Label(self.header, text=self._subtitle_full,
                  style="Subtitle.TLabel", wraplength=1000)
        self.subtitle.pack(anchor="w", padx=28, pady=(4,10))
        self.panel = ttk.Frame(root, padding=(24,8,24,8))
        self.panel.pack(fill="both", expand=True)
        settings = ttk.Frame(self.panel)
        settings.pack(fill="x")
        self.settings_canvas = tk.Canvas(settings, bg=BG, highlightthickness=0, height=280)
        self.settings_scrollbar = ttk.Scrollbar(settings, orient="vertical", command=self.settings_canvas.yview)
        self.settings_canvas.configure(yscrollcommand=self.settings_scrollbar.set)
        self.settings_canvas.pack(side="left", fill="x", expand=True)
        self.settings_scrollbar.pack(side="right", fill="y")
        self.settings_panel = panel = ttk.Frame(self.settings_canvas)
        self._settings_window = self.settings_canvas.create_window(0, 0, window=panel, anchor="nw")
        self.settings_canvas.bind("<Configure>", self._settings_width)
        panel.bind("<Configure>", self._settings_region)
        self.settings_canvas.bind("<MouseWheel>", self._settings_wheel)
        self.input_card = card = ttk.Frame(panel, style="Card.TFrame", padding=8)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="1. Escolha o que deseja converter", style="Section.TLabel").grid(row=0,column=0,columnspan=3,sticky="w")
        choices = ttk.Frame(card, style="Card.TFrame")
        choices.grid(row=1,column=0,columnspan=3,sticky="w",pady=(5,6))
        self.file_radio = ttk.Radiobutton(choices,text="Arquivo individual",variable=self.input_kind,value="file",command=self.clear_input)
        self.file_radio.pack(side="left",padx=(0,24))
        self.folder_button = ttk.Radiobutton(choices,text="Pasta inteira",variable=self.input_kind,value="folder",command=self.clear_input)
        self.folder_button.pack(side="left")
        ttk.Label(card,text="Arquivo ou pasta de entrada",style="Card.TLabel").grid(row=2,column=0,sticky="w",padx=(0,12),pady=(0,6))
        self.source_entry = ttk.Entry(card,textvariable=self.source)
        self.source_entry.grid(row=2,column=1,sticky="ew",padx=(0,10),pady=(0,6))
        self.file_button = ttk.Button(card,text="Procurar...",style="Secondary.TButton",command=self.browse_input)
        self.file_button.grid(row=2,column=2,sticky="e",pady=(0,6))
        ttk.Label(card,text="Pasta de destino",style="Card.TLabel").grid(row=3,column=0,sticky="w",padx=(0,12),pady=(0,6))
        self.output_entry = ttk.Entry(card,textvariable=self.output)
        self.output_entry.grid(row=3,column=1,sticky="ew",padx=(0,10),pady=(0,6))
        self.output_button = ttk.Button(card,text="Escolher destino...",style="Secondary.TButton",command=self.choose_output)
        self.output_button.grid(row=3,column=2,sticky="e",pady=(0,6))
        ttk.Label(card,text="Configuração TOML (opcional)",style="Card.TLabel").grid(row=4,column=0,sticky="w",padx=(0,12))
        self.config_entry = ttk.Entry(card,textvariable=self.config_path)
        self.config_entry.grid(row=4,column=1,sticky="ew",padx=(0,10))
        self.config_button = ttk.Button(card,text="Escolher TOML...",style="Secondary.TButton",command=self.choose_config)
        self.config_button.grid(row=4,column=2,sticky="e")
        self.options_card = options = ttk.Frame(panel,style="Card.TFrame",padding=8)
        options.pack(fill="x",pady=(8,0))
        ttk.Label(options,text="2. Qualidade automática",style="Section.TLabel").pack(anchor="w")
        label = ttk.Label(options,text="Extração rápida de documentos regulares, com OCR seletivo quando necessário. Resultados parciais mostram os avisos para revisão.",
                  style="Muted.Card.TLabel",wraplength=900)
        label.pack(anchor="w",pady=(3,5)); self._wrap_labels.append(label)
        checks = ttk.Frame(options,style="Card.TFrame")
        checks.pack(fill="x")
        self.chunk_box = ttk.Checkbutton(checks,text="Gerar chunks para IA/RAG",variable=self.chunk)
        self.chunk_box.grid(row=0,column=0,sticky="w",padx=(0,18),pady=2)
        self.overwrite_box = ttk.Checkbutton(checks,text="Sobrescrever resultado anterior",variable=self.overwrite)
        self.overwrite_box.grid(row=1,column=1,sticky="w",pady=2)
        self.recursive_box = ttk.Checkbutton(checks,text="Percorrer subpastas",variable=self.recursive)
        self.recursive_box.grid(row=0,column=1,sticky="w",pady=2)
        self.scan_previews_box = ttk.Checkbutton(checks,text="Incluir prévias de páginas",variable=self.scan_previews)
        self.scan_previews_box.grid(row=1,column=0,sticky="w",padx=(0,18),pady=2)
        self.advanced_button = ttk.Button(options,text="Configurações avançadas ▾",style="Secondary.TButton",command=self._toggle_advanced)
        self.advanced_button.pack(anchor="w",pady=(5,0))
        self.chunk_settings = chunk_settings = ttk.Frame(options,style="Card.TFrame")
        ttk.Label(chunk_settings,text="Chunks: tamanho em caracteres",style="Card.TLabel").pack(side="left")
        self.chunk_size_entry = ttk.Entry(chunk_settings,textvariable=self.chunk_size,width=7)
        self.chunk_size_entry.pack(side="left",padx=6)
        ttk.Label(chunk_settings,text="sobreposição",style="Card.TLabel").pack(side="left")
        self.chunk_overlap_entry = ttk.Entry(chunk_settings,textvariable=self.chunk_overlap,width=7)
        self.chunk_overlap_entry.pack(side="left",padx=6)
        label = ttk.Label(options,text="Cada arquivo cria uma subpasta com seu nome. Repetições recebem (2), (3)… Escolha OCR para refazer o reconhecimento de um PDF com texto.",
                  style="Muted.Card.TLabel",wraplength=900)
        label.pack(anchor="w",pady=(5,8)); self._wrap_labels.append(label)
        self.ocr_controls = ocr = ttk.Frame(options,style="Card.TFrame")
        ocr.pack(fill="x")
        ocr.columnconfigure(1,weight=1)
        ttk.Label(ocr,text="Método:",style="Card.TLabel").grid(row=0,column=0,sticky="w",padx=(0,8),pady=(0,4))
        self.mode_box = ttk.Combobox(ocr, textvariable=self.mode, values=("auto", "native", "ocr"),
                                      state="readonly", width=10)
        self.mode_box.grid(row=0,column=1,sticky="w",pady=(0,4))
        ttk.Label(ocr,text="Idioma(s) do OCR:",style="Card.TLabel").grid(row=1,column=0,sticky="w",padx=(0,8))
        self.language_entry = ttk.Entry(ocr,textvariable=self.language,width=30)
        self.language_entry.grid(row=1,column=1,sticky="ew",padx=(0,8))
        ttk.Label(ocr,text="Ex.: por ou por+eng",style="Muted.Card.TLabel").grid(row=1,column=2,sticky="w")
        panel = self.panel
        self.controls = controls = ttk.Frame(panel,padding=(0,8,0,0))
        controls.pack(fill="x")
        self.convert_button = ttk.Button(controls,text="Converter agora",style="Primary.TButton",command=self.start)
        self.convert_button.grid(row=0,column=0,sticky="w")
        self.cancel_button = ttk.Button(controls,text="Cancelar",style="Secondary.TButton",command=self.cancel,state="disabled")
        self.cancel_button.grid(row=0,column=1,sticky="w",padx=8)
        self.open_button = ttk.Button(controls,text="Abrir pasta de destino",style="Secondary.TButton",command=self.open_output)
        self.open_button.grid(row=0,column=2,sticky="w",padx=(0,8))
        self.doctor_button = ttk.Button(controls,text="Verificar ambiente",style="Secondary.TButton",command=self.check_installation)
        self.doctor_button.grid(row=0,column=3,sticky="w")
        self.status_label = ttk.Label(panel,textvariable=self.status,wraplength=930)
        self.status_label.pack(anchor="w",pady=(6,0)); self._wrap_labels.append(self.status_label)
        self.progress = ttk.Progressbar(panel, maximum=1, mode="determinate")
        self.progress.pack(fill="x",pady=(6,0))
        self.notebook = ttk.Notebook(panel)
        self.notebook.pack(fill="both",expand=True,pady=(8,0))
        log_tab = ttk.Frame(self.notebook,style="Card.TFrame",padding=10)
        result_tab = ttk.Frame(self.notebook)
        preview_tab = ttk.Frame(self.notebook)
        installation_tab = ttk.Frame(self.notebook,style="Card.TFrame",padding=12)
        self.notebook.add(log_tab,text="Registro da execução")
        self.notebook.add(result_tab, text="Resultados por arquivo")
        self.notebook.add(preview_tab, text="Prévia textual")
        self.notebook.add(installation_tab,text="Ambiente e idiomas")
        self.log = scrolledtext.ScrolledText(log_tab,height=8,font=("Consolas",9),bg="#FAFAFA",fg=TEXT,
                                            relief="solid",bd=1,wrap="word",state="disabled")
        self.log.pack(fill="both",expand=True)
        label = ttk.Label(installation_tab,textvariable=self.capabilities,justify="left",wraplength=880,style="Card.TLabel")
        label.pack(anchor="w"); self._wrap_labels.append(label)
        result_tab.columnconfigure(0,weight=1); result_tab.rowconfigure(0,weight=1)
        self.results = ttk.Treeview(result_tab, columns=("file", "status", "output"), show="headings", height=5,style="Results.Treeview")
        status_width = self._body_font.measure("Parcial — revisar avisos") + 24
        for column, title, width in (("file", "Arquivo", max(420,self._body_font.measure("Arquivo de documento com nome extenso.pdf"))), ("status", "Resultado", status_width), ("output", "Pasta criada / detalhes", 620)):
            self.results.heading(column, text=title)
            self.results.column(column, width=width, minwidth=width,stretch=False)
        self.results.grid(row=0,column=0,sticky="nsew")
        self.result_y_scroll = ttk.Scrollbar(result_tab,orient="vertical",command=self.results.yview)
        self.result_y_scroll.grid(row=0,column=1,sticky="ns")
        self.result_x_scroll = ttk.Scrollbar(result_tab,orient="horizontal",command=self.results.xview)
        self.result_x_scroll.grid(row=1,column=0,sticky="ew")
        self.results.configure(yscrollcommand=self.result_y_scroll.set,xscrollcommand=self.result_x_scroll.set)
        self.results.bind("<Configure>",self._refresh_result_names,add=True)
        self.results.bind("<ButtonRelease-1>",lambda event:self.root.after_idle(self._refresh_result_names),add=True)
        detail_panel = ttk.Frame(result_tab)
        detail_panel.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(5,0))
        detail_panel.columnconfigure(0,weight=1)
        self.detail_text = tk.Text(detail_panel,height=3,wrap="none",font=self._body_font,
                                   state="disabled",bg=CARD,fg=TEXT,relief="solid",bd=1)
        self.detail_text.grid(row=0,column=0,sticky="ew")
        detail_y_scroll = ttk.Scrollbar(detail_panel,orient="vertical",command=self.detail_text.yview)
        detail_y_scroll.grid(row=0,column=1,sticky="ns")
        detail_scroll = ttk.Scrollbar(detail_panel,orient="horizontal",command=self.detail_text.xview)
        detail_scroll.grid(row=1,column=0,sticky="ew")
        self.detail_text.configure(xscrollcommand=detail_scroll.set,yscrollcommand=detail_y_scroll.set)
        self._set_result_detail(self.result_detail.get())
        self.results.bind("<<TreeviewSelect>>", self._select_result)
        label = ttk.Label(preview_tab, textvariable=self.preview_label, wraplength=930)
        label.pack(anchor="w", pady=5); self._wrap_labels.append(label)
        ttk.Label(preview_tab, text="Até 128 KB; imagens e HTML não são executados").pack(anchor="w", pady=5)
        preview_panel = ttk.Frame(preview_tab)
        preview_panel.pack(fill="both", expand=True)
        self.preview = tk.Text(preview_panel, wrap="word", height=10, state="disabled", font=("Consolas", 10))
        scrollbar = ttk.Scrollbar(preview_panel, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=scrollbar.set)
        self.preview.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.inputs = [self.source_entry, self.output_entry, self.file_button, self.folder_button,
                       self.file_radio,self.output_button,self.config_entry,self.config_button,
                       self.mode_box,self.language_entry,self.recursive_box,self.chunk_box,
                       self.chunk_size_entry,self.chunk_overlap_entry,self.scan_previews_box,self.overwrite_box]
        self.append_log("Pronto. Selecione a entrada, escolha a pasta de destino e clique em Converter agora.\n")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Configure>",self._schedule_layout,add=True)
        root.bind("<MouseWheel>",self._settings_wheel,add=True)
        root.bind("<FocusIn>",self._show_focused_setting,add=True)
        root.after_idle(self._adaptive_layout)
        root.after(80, self.poll)
        self.check_installation()

    def _setup_style(self) -> None:
        style=ttk.Style(self.root)
        self._body_font = tkfont.Font(root=self.root,family="Segoe UI",size=10)
        self._heading_font = tkfont.Font(root=self.root,family="Segoe UI Semibold",size=10)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame",background=BG)
        style.configure("Card.TFrame",background=CARD)
        style.configure("TLabel",background=BG,foreground=TEXT,font=("Segoe UI",10))
        style.configure("Card.TLabel",background=CARD,foreground=TEXT,font=("Segoe UI",10))
        style.configure("Muted.Card.TLabel",background=CARD,foreground=MUTED,font=("Segoe UI",9))
        style.configure("Title.TLabel",background=ACCENT,foreground="white",font=("Segoe UI Semibold",18))
        style.configure("Subtitle.TLabel",background=ACCENT,foreground="white",font=("Segoe UI",10))
        style.configure("Section.TLabel",background=CARD,foreground=TEXT,font=("Segoe UI Semibold",11))
        style.configure("TRadiobutton",background=CARD,foreground=TEXT,font=("Segoe UI",10))
        style.configure("TCheckbutton",background=CARD,foreground=TEXT,font=("Segoe UI",10))
        style.configure("TEntry",fieldbackground="white",foreground=TEXT,bordercolor=BORDER)
        style.configure("Primary.TButton",font=("Segoe UI Semibold",10),foreground="white",background=ACCENT,padding=(14,5))
        style.map("Primary.TButton",background=[("active",ACCENT_DARK),("disabled","#BDBDBD")])
        style.configure("Secondary.TButton",font=("Segoe UI",10),padding=(12,4))
        style.configure("Horizontal.TProgressbar",background=ACCENT,troughcolor="#E6E6E6",bordercolor="#E6E6E6")
        style.configure("Results.Treeview",font=self._body_font,rowheight=self._body_font.metrics("linespace")+8)
        style.configure("Results.Treeview.Heading",font=self._heading_font,padding=(6,5))
        style.configure("TNotebook.Tab",font=self._body_font,padding=(8,5))

    def _settings_width(self,event) -> None:
        self.settings_canvas.itemconfigure(self._settings_window,width=max(1,event.width))
        self._schedule_layout()

    def _settings_region(self,event=None) -> None:
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))
        self._schedule_layout()

    def _settings_wheel(self,event) -> str:
        if not (self.settings_canvas.winfo_rootx() <= event.x_root < self.settings_canvas.winfo_rootx()+self.settings_canvas.winfo_width()
                and self.settings_canvas.winfo_rooty() <= event.y_root < self.settings_canvas.winfo_rooty()+self.settings_canvas.winfo_height()):
            return None
        self.settings_canvas.yview_scroll(-int(event.delta/120),"units")
        return "break"

    def _show_focused_setting(self,event) -> None:
        widget = event.widget
        parent = widget
        while parent is not self.settings_panel and parent is not None:
            parent = getattr(parent,"master",None)
        if parent is None:
            return
        top=self.settings_canvas.canvasy(0)
        widget_top=widget.winfo_rooty()-self.settings_panel.winfo_rooty()
        widget_bottom=widget_top+widget.winfo_height()
        total=max(1,self.settings_panel.winfo_height())
        if widget_top<top:
            self.settings_canvas.yview_moveto(widget_top/total)
        elif widget_bottom>top+self.settings_canvas.winfo_height():
            self.settings_canvas.yview_moveto((widget_bottom-self.settings_canvas.winfo_height())/total)

    def _schedule_layout(self,event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self._layout_callback is None:
            self._layout_callback = self.root.after_idle(self._adaptive_layout)

    def _adaptive_layout(self) -> None:
        self._layout_callback = None
        if not self.root.winfo_exists():
            return
        width=max(300,self.root.winfo_width()-56)
        self.subtitle.configure(wraplength=width)
        for label in self._wrap_labels:
            parent_width=label.master.winfo_width()
            label.configure(wraplength=max(240,min(width,parent_width-24 if parent_width>100 else width)))
        controls=(self.convert_button,self.cancel_button,self.open_button,self.doctor_button)
        required=sum(button.winfo_reqwidth() for button in controls)+32
        narrow=required>self.panel.winfo_width()
        for index,button in enumerate(controls):
            button.grid_configure(row=index//2 if narrow else 0,column=index%2 if narrow else index,
                                  pady=(0,4) if narrow else 0)
        # Preserve room for actions and results at high DPI and in short windows;
        # every configuration remains reachable with the visible scrollbar.
        line=self._body_font.metrics("linespace")
        row_height=line+8
        tab_height=max(self.notebook.nametowidget(tab).winfo_reqheight() for tab in self.notebook.tabs())
        notebook_chrome=max(0,self.notebook.winfo_reqheight()-tab_height)
        # Treeview asks for five body rows. Retain its measured heading/borders,
        # every result-detail control, and at least two body rows at the minimum.
        tree_min=self.results.winfo_reqheight()-3*row_height
        result_min=(self.results.master.winfo_reqheight()-self.results.winfo_reqheight()
                    +tree_min+notebook_chrome)
        reserved=(self.header.winfo_reqheight()+self.controls.winfo_reqheight()
                  +self.status_label.winfo_reqheight()+self.progress.winfo_reqheight()+64+result_min)
        screen_height=max(300,self.root.winfo_screenheight()-90)
        detail_rows=int(self.detail_text.cget("height"))
        full_reserved=reserved+(3-detail_rows)*line
        capacity=min(self.root.winfo_height(),screen_height)
        compact=full_reserved+100>capacity
        wanted_detail_rows=(2 if full_reserved-line+80<=capacity else 1) if compact else 3
        if wanted_detail_rows!=detail_rows:
            self.detail_text.configure(height=wanted_detail_rows)
            self._schedule_layout()
            return
        minimum_settings=80 if compact else 100
        full_header_lines=max(1,(self._body_font.measure(self._subtitle_full)+width-1)//width)
        current_header_lines=max(1,(self.subtitle.winfo_reqheight()-4)//line)
        full_header_extra=max(0,full_header_lines-current_header_lines)*line
        small_header=(compact and full_header_lines>1 and
                      full_reserved+full_header_extra+minimum_settings>screen_height)
        subtitle="Converta arquivos e pastas em Markdown." if small_header else self._subtitle_full
        if self.subtitle.cget("text")!=subtitle:
            self.subtitle.configure(text=subtitle)
            self._schedule_layout()
            return
        self.root.minsize(900,min(screen_height,max(650,reserved+minimum_settings)))
        available=max(minimum_settings,min(int(self.root.winfo_height()*0.39),self.root.winfo_height()-reserved))
        requested=self.settings_panel.winfo_reqheight()
        self.settings_canvas.configure(height=min(requested,available))
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))

    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.chunk_settings.pack(fill="x",pady=(5,0),after=self.advanced_button)
            self.advanced_button.configure(text="Configurações avançadas ▴")
        else:
            self.chunk_settings.pack_forget()
            self.advanced_button.configure(text="Configurações avançadas ▾")
        self._schedule_layout()

    def _set_result_detail(self,text: str) -> None:
        self.result_detail.set(text)
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0","end")
        self.detail_text.insert("1.0",text)
        self.detail_text.configure(state="disabled")

    def _display_filename(self,name: str) -> str:
        available=max(24,int(self.results.column("file","width"))-16)
        if self._body_font.measure(name)<=available:
            return name
        marker=" … "
        # Preserve the identifying suffix (year/volume/extension), while keeping
        # the beginning recognizable. Measure pixels, not a guessed char count.
        tail_budget=max(1,int((available-self._body_font.measure(marker))*0.60))
        low,high=0,len(name)
        while low<high:
            middle=(low+high+1)//2
            if self._body_font.measure(name[-middle:])<=tail_budget:
                low=middle
            else:
                high=middle-1
        tail=name[-low:] if low else ""
        prefix_budget=max(0,available-self._body_font.measure(marker+tail))
        low,high=0,len(name)-len(tail)
        while low<high:
            middle=(low+high+1)//2
            if self._body_font.measure(name[:middle])<=prefix_budget:
                low=middle
            else:
                high=middle-1
        return name[:low]+marker+tail

    def _refresh_result_names(self,event=None) -> None:
        width=int(self.results.column("file","width"))
        if width==self._file_display_width:
            return
        self._file_display_width=width
        for row,result in self._row_results.items():
            if self.results.exists(row):
                self.results.set(row,"file",self._display_filename(Path(result["source"]).name))

    def choose_file(self) -> None:
        value = filedialog.askopenfilename(title="Selecione o arquivo para converter",filetypes=SUPPORTED_FILETYPES)
        if value:
            self.input_kind.set("file")
            self.source.set(value)
            if not self.output.get().strip():
                self.output.set(str(Path(value).parent / "Markdown_convertido"))

    def choose_folder(self) -> None:
        value = filedialog.askdirectory(title="Escolher pasta de entrada")
        if value:
            self.input_kind.set("folder")
            self.source.set(value)
            if not self.output.get().strip():
                path=Path(value)
                self.output.set(str(path.parent / f"{path.name}_Markdown_convertido"))

    def clear_input(self) -> None:
        self.source.set("")

    def browse_input(self) -> None:
        if self.input_kind.get()=="folder":
            self.choose_folder()
        else:
            self.choose_file()

    def choose_config(self) -> None:
        value=filedialog.askopenfilename(title="Selecione a configuração TOML",filetypes=[("Configuração TOML","*.toml")])
        if not value:
            return
        try:
            configured=_read_configuration(value)
        except (ValueError,OSError,TypeError) as exc:
            messagebox.showerror("Verifique a configuração",str(exc),parent=self.root)
            return
        self.config_path.set(value)
        if "mode" in configured:
            self.mode.set(configured["mode"])
        if "language" in configured:
            self.language.set(configured["language"])
        if "chunks" in configured:
            self.chunk.set(configured["chunks"])
        if "chunk_size_chars" in configured:
            self.chunk_size.set(str(configured["chunk_size_chars"]))
        if "chunk_overlap_chars" in configured:
            self.chunk_overlap.set(str(configured["chunk_overlap_chars"]))
        if "scan_previews" in configured:
            self.scan_previews.set(configured["scan_previews"])
        if "overwrite" in configured:
            self.overwrite.set(configured["overwrite"])

    def conversion_options(self) -> Options:
        configured=_read_configuration(self.config_path.get().strip()) if self.config_path.get().strip() else {}
        configured["mode"]=self.mode.get()
        configured["language"]=self.language.get().strip() or configured.get("language",default_language())
        configured["chunks"] = self.chunk.get()
        configured["chunk_size_chars"] = int(self.chunk_size.get())
        configured["chunk_overlap_chars"] = int(self.chunk_overlap.get())
        configured["scan_previews"] = self.scan_previews.get()
        configured["overwrite"] = self.overwrite.get()
        return Options(**configured)

    def choose_output(self) -> None:
        value = filedialog.askdirectory(title="Escolher pasta de saída")
        if value:
            self.output.set(value)

    def open_output(self) -> None:
        raw=self.output.get().strip()
        if not raw or not Path(raw).expanduser().is_dir():
            self.status.set("Escolha uma pasta de destino existente para abrir.")
            return
        path=str(Path(raw).expanduser().resolve())
        try:
            if os.name=="nt":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform=="darwin" else "xdg-open",path])
        except OSError as exc:
            messagebox.showerror("Abrir pasta de destino",str(exc),parent=self.root)

    def append_log(self,text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end",text)
        lines=int(self.log.index("end-1c").split(".")[0])
        if lines>1500:
            self.log.delete("1.0",f"{lines-1200}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy: bool, converting: bool = False) -> None:
        self.busy = busy
        for widget in self.inputs:
            widget.configure(state="disabled" if busy else "readonly" if widget is self.mode_box else "normal")
        self.convert_button.configure(state="disabled" if busy else "normal")
        self.doctor_button.configure(state="disabled" if busy else "normal")
        self.open_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if converting else "disabled")

    def check_installation(self) -> None:
        if self.busy:
            return
        try:
            options=self.conversion_options()
        except (ValueError,OSError,TypeError) as exc:
            messagebox.showerror("Verifique as opções",str(exc),parent=self.root)
            return
        language = options.language
        self.set_busy(True)
        self.status.set("Verificando instalação; a janela continua disponível…")
        def work() -> None:
            try:
                self.events.put(("doctor", inspect(options.tesseract,options.tessdata), language))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("idle",))
        threading.Thread(target=work, daemon=True).start()

    def start(self) -> None:
        if self.busy:
            return
        try:
            source_text=self.source.get().strip().strip('"')
            output_text=self.output.get().strip().strip('"')
            if not source_text or not output_text:
                raise ValueError("Escolha a entrada e a pasta de saída")
            source = Path(source_text).expanduser()
            output = Path(output_text).expanduser()
            if self.input_kind.get()=="file" and not source.is_file():
                raise ValueError("Selecione um arquivo ou escolha a opção Pasta inteira")
            if self.input_kind.get()=="folder" and not source.is_dir():
                raise ValueError("Selecione uma pasta ou escolha a opção Arquivo individual")
            options = self.conversion_options()
        except (ValueError,OSError,TypeError) as exc:
            messagebox.showerror("Verifique as opções", str(exc), parent=self.root)
            return
        recursive = self.recursive.get()
        self.cancel_event.clear()
        self._preview_token += 1
        self._preview_row = None
        self._row_results.clear()
        self.results.delete(*self.results.get_children())
        self._set_result_detail("Aguardando os resultados da conversão.")
        self.preview_label.set("Aguardando o resultado do arquivo selecionado.")
        self._set_preview("")
        self.progress.configure(value=0, maximum=1)
        self.set_busy(True, converting=True)
        self.append_log(f"\n--- Nova conversão ---\nEntrada: {source}\nSaída: {output}\nMétodo: {options.mode}; idiomas: {options.language}\n")
        self.status.set("Verificando capacidades antes da conversão…")
        threading.Thread(target=self.convert, args=(source, output, options, recursive), daemon=True).start()

    def convert(self, source: Path, output: Path, options: Options, recursive: bool) -> None:
        results = []
        try:
            report = inspect(options.tesseract, options.tessdata)
            self.events.put(("doctor", report, options.language))
            files = discover(source, output, recursive, legacy=options.legacy)
            if not files:
                raise ValueError("Nenhum arquivo suportado encontrado")
            has_ocr_input = any(p.suffix.lower() == ".pdf" or p.suffix.lower() in IMAGE_SUFFIXES for p in files)
            missing = set(options.language.split("+")) - set(report.get("languages", []))
            if options.mode == "ocr" and has_ocr_input and (not report.get("tesseract") or missing):
                raise ValueError("OCR indisponível ou idiomas ausentes. Confira a verificação de instalação acima.")
            self.events.put(("total", len(files)))
            for index, file in enumerate(files, 1):
                if self.cancel_event.is_set():
                    break
                self.events.put(("running", index, len(files), file.name))
                result = execute(file, output, options, self.cancel_event.is_set,
                                 lambda event: self.events.put(("page_progress", event)))
                results.append(result)
                # Queue metadata only. Preview reads happen separately when a row
                # is selected, avoiding a 128 KB text cache for every batch item.
                self.events.put(("result", result, "", index))
                if result["status"] == "cancelled":
                    break
            counts = Counter(r["status"] for r in results)
            summary = "; ".join(f"{STATUS.get(status, status)}: {count}" for status, count in counts.items())
            unprocessed = len(files) - len(results)
            if unprocessed:
                summary += f"; não processados após cancelamento: {unprocessed}"
            self.events.put(("finished", summary or "Cancelado antes da conversão"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("idle",))

    def cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelamento solicitado; aguardando encerramento do processo atual…")

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    @staticmethod
    def _read_preview(output: str) -> str:
        try:
            with (Path(output) / "document.md").open("rb") as reader:
                payload = reader.read(128 * 1024 + 1)
            text = payload[:128 * 1024].decode("utf-8", errors="replace")
            # Linha muito longa paralisa o layout do Tk Text (no macOS, minutos para uma linha de 128 KiB).
            limite = 4000
            text = "\n".join(line if len(line) <= limite else line[:limite] + " […linha truncada na prévia]"
                              for line in text.split("\n"))
            if len(payload) > 128 * 1024:
                text += "\n\n[Prévia truncada; consulte document.md.]"
            return text
        except OSError:
            return "Prévia indisponível; consulte os arquivos de saída."

    def _select_result(self, event=None) -> None:
        selected = self.results.selection()
        row = selected[0] if selected else None
        if row == self._preview_row:
            return
        self._preview_token += 1
        self._preview_row = row
        token = self._preview_token
        result = self._row_results.get(row)
        if not result:
            self._set_result_detail("Selecione um arquivo para consultar os caminhos completos.")
            self.preview_label.set("Selecione um resultado para ver a prévia textual.")
            self._set_preview("")
            return
        name = Path(result["source"]).name
        status = STATUS.get(result["status"], result["status"])
        self.preview_label.set(f"Prévia: {name} · {status}")
        output = result.get("output")
        details = f"Entrada: {result['source']}\nResultado: {status}\nSaída: {output or 'não gerada'}"
        if result.get("error"):
            details += "\nMotivo: " + str(result["error"])[:6000]
        self._set_result_detail(details)
        if result["status"] not in {"complete", "partial"} or not output:
            reason = result.get("error") or status
            self._set_preview(f"{name}\nSem prévia de conversão: {reason}")
            return
        self._set_preview(f"Carregando a prévia de {name}…")
        def work() -> None:
            text = self._read_preview(output)
            self.events.put(("preview", token, row, text))
        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "doctor":
                text=capability_text(event[1], event[2])
                self.capabilities.set(text)
                self.append_log("\nAmbiente:\n"+text+"\n")
            elif kind == "total":
                self.progress.configure(maximum=event[1])
            elif kind == "running":
                self.status.set(f"Arquivo {event[1]} de {event[2]}: {event[3]} — aguardando resultado")
                self.append_log(f"[{event[1]}/{event[2]}] {event[3]}\n")
            elif kind == "page_progress":
                update = event[1]
                self.status.set(f"Página {update['page']} de {update['total']} · {update.get('route', '')} · Cancelar permanece disponível")
            elif kind == "result":
                result = event[1]
                row = self.results.insert("", "end", values=(self._display_filename(Path(result["source"]).name),
                    STATUS.get(result["status"], result["status"]), result.get("output") or "Consulte os detalhes do arquivo selecionado"))
                self._row_results[row] = {key: result.get(key) for key in ("source", "status", "output", "error")}
                self.append_log(f"  {STATUS.get(result['status'],result['status'])}: {Path(result['source']).name}\n")
                if result.get("error"):
                    self.append_log("  Motivo: "+str(result["error"])+"\n")
                if result.get("warnings"):
                    self.append_log(f"  Avisos: {result['warnings']}. Consulte LEIA-AVISOS.txt para as páginas afetadas.\n")
                    for code, count in sorted(result.get("warning_summary", {}).items()):
                        self.append_log(f"    {warning_label(code)}: {count}\n")
                if result.get("routes"):
                    self.append_log("  Métodos por página: " + "; ".join(f"{route}: {count}" for route,count in result["routes"].items()) + "\n")
                if result.get("full_page_images_in_original"):
                    self.append_log(f"  Imagens de página inteira no PDF original: {result['full_page_images_in_original']}. Para exportá-las como prévias, marque Incluir prévias de páginas.\n")
                if result.get("output"):
                    self.append_log("  Pasta criada: " + result["output"] + "\n")
                if result.get("cleanup_warning"):
                    self.append_log("  Aviso de limpeza: "+str(result["cleanup_warning"])+"\n")
                self.progress.configure(value=event[3])
                self.results.selection_set(row)
                self.results.focus(row)
                self.results.see(row)
                self._select_result()
            elif kind == "preview":
                selected = self.results.selection()
                if event[1] == self._preview_token and selected and selected[0] == event[2]:
                    self._set_preview(event[3])
            elif kind == "finished":
                self.status.set(event[1])
                self.append_log("\n"+event[1]+"\n")
            elif kind == "error":
                self.status.set("Falhou: " + event[1])
                self.append_log("Falhou: "+event[1]+"\n")
                self._preview_token += 1
                self._preview_row = None
                name = Path(self.source.get()).name or "Conversão"
                self.preview_label.set(f"Prévia: {name} · Falhou")
                self._set_preview(f"{name}\nSem prévia de conversão: {event[1]}")
            elif kind == "idle":
                self.set_busy(False)
                if self.status.get().startswith("Verificando instalação"):
                    self.status.set("Pronto. Confira capacidades e idiomas antes de converter.")
        if self.closing and not self.busy:
            self.root.destroy()
            return
        self.root.after(80, self.poll)

    def close(self) -> None:
        if self.busy:
            self.closing = True
            self.cancel()
        else:
            self.root.destroy()


def main() -> None:
    from .platform import enable_hidpi
    enable_hidpi()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
