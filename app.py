#!/usr/bin/env python3
"""
AvisaVid — lector de textos y errores en video para macOS.

Carga un video, muestra su formato/duración/resolución, y arma una
línea de tiempo con:
  - texto detectado en pantalla (OCR sobre frames muestreados)
  - transcripción de audio (Whisper local, sin API key)

El resultado aparece en un cuadro de texto editable, exportable a .txt.

Requisitos del sistema (instalar antes de correr):
    brew install ffmpeg tesseract tesseract-lang

Requisitos de Python (ver requirements.txt):
    pip3 install -r requirements.txt

Ejecutar:
    python3 app.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Falta Pillow. Corré: pip3 install -r requirements.txt")
    sys.exit(1)

# ---------- Paleta / tipografía estilo macOS ----------
BG = "#F5F5F7"           # windowBackgroundColor (Big Sur / Sonoma)
PANEL_BG = "#FFFFFF"
ACCENT = "#007AFF"       # azul sistema de Apple
ACCENT_PRESSED = "#0062CC"
TEXT_MAIN = "#1D1D1F"    # labelColor
TEXT_MUTED = "#6E6E73"   # secondaryLabelColor
BORDER = "#E5E5EA"       # separatorColor
RADIUS = 8               # radio de esquina de botones, estilo controles macOS 11+
PANEL_RADIUS = 12        # radio de esquina de paneles/tarjetas (coherente entre ambos)
if sys.platform == "darwin":
    FONT_FAMILY = "SF Pro Text"
elif sys.platform == "win32":
    FONT_FAMILY = "Segoe UI"
else:
    FONT_FAMILY = "Helvetica"

BRAND_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "PEPSI_pl.ttf")
BRAND_FONT_NAME = "! PEPSI !"  # nombre interno del archivo de fuente


def ensure_brand_font_loaded():
    """Carga la tipografía de marca (usada solo en el título) sin instalarla
    para todo el sistema. Windows: se registra para este proceso. macOS: se
    copia a ~/Library/Fonts si todavía no está (CoreText la toma de ahí)."""
    if not os.path.exists(BRAND_FONT_PATH):
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            FR_PRIVATE = 0x10
            added = ctypes.windll.gdi32.AddFontResourceExW(
                ctypes.c_wchar_p(BRAND_FONT_PATH), FR_PRIVATE, 0
            )
            return added > 0
        elif sys.platform == "darwin":
            import shutil
            dest_dir = os.path.expanduser("~/Library/Fonts")
            dest = os.path.join(dest_dir, os.path.basename(BRAND_FONT_PATH))
            if not os.path.exists(dest):
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy(BRAND_FONT_PATH, dest)
            return True
    except Exception:
        return False
    return False

MAX_FRAMES = 24          # tope de frames a analizar (cuidar tiempo/CPU)


# ---------- Helpers de shell ----------

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def check_binary(name):
    import shutil
    return shutil.which(name) is not None


def ffprobe_metadata(path):
    """Devuelve dict con duración, resolución, codec y formato."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falló: {result.stderr}")
    data = json.loads(result.stdout)
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0))
    return {
        "duration": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec": video_stream.get("codec_name", "?"),
        "container": fmt.get("format_name", "?"),
        "has_audio": any(
            s.get("codec_type") == "audio" for s in data.get("streams", [])
        ),
    }


def extract_frames(path, out_dir, duration):
    """Muestrea frames a intervalo fijo, con tope MAX_FRAMES.

    El muestreo parejo empieza en t=0 y salta de a `interval` segundos, así
    que un texto de apertura que aparece y desaparece rápido puede caer
    entre dos tomas. Se agregan varias tomas extra bien al principio
    (títulos/subtítulos de apertura suelen durar menos de un segundo).
    """
    interval = max(duration / MAX_FRAMES, 1.0)
    pattern = os.path.join(out_dir, "frame_%03d.jpg")
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-vf", f"fps=1/{interval},scale=768:-1",
        "-q:v", "3", pattern,
    ]
    run(cmd)
    frames = sorted(f for f in os.listdir(out_dir) if f.startswith("frame_"))
    result = [(os.path.join(out_dir, f), i * interval) for i, f in enumerate(frames)]

    for idx, early_t in enumerate((0.1, 0.4, 0.8)):
        if early_t >= duration:
            continue
        if any(abs(t - early_t) <= 0.15 for _, t in result):
            continue
        early_path = os.path.join(out_dir, f"frame_early{idx}.jpg")
        run([
            "ffmpeg", "-y", "-ss", str(early_t), "-i", path,
            "-frames:v", "1", "-vf", "scale=768:-1", "-q:v", "3", early_path,
        ])
        if os.path.exists(early_path):
            result.append((early_path, early_t))

    result.sort(key=lambda item: item[1])
    return result


def extract_audio(path, out_wav):
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-vn", "-ac", "1", "-ar", "16000", out_wav,
    ]
    run(cmd)
    return os.path.exists(out_wav)


def extract_thumbnail(path, out_jpg, width=360):
    # out_jpg es una ruta fija y reutilizada entre videos: si ffmpeg falla
    # (archivo corrupto, sin pista de video) y no la borramos antes, el
    # thumbnail de un video anterior queda como si fuera del nuevo.
    if os.path.exists(out_jpg):
        os.remove(out_jpg)
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-frames:v", "1", "-vf", f"scale={width}:-1", out_jpg,
    ]
    run(cmd)
    return os.path.exists(out_jpg)


BLACK_FRAME_THRESHOLD = 12   # luma medio (0-255) por debajo del cual se considera frame negro
FREEZE_DIFF_THRESHOLD = 2.0  # diferencia media de luma entre frames por debajo de la cual se considera congelado


def frame_brightness(img):
    gray = img.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    return sum(i * c for i, c in enumerate(hist)) / total if total else 0


def frame_diff(img_a, img_b, size=(64, 36)):
    a = list(img_a.convert("L").resize(size).getdata())
    b = list(img_b.convert("L").resize(size).getdata())
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


OCR_MIN_CONFIDENCE = 60  # 0-100, Tesseract per-word confidence


def ocr_confident_text(img):
    """OCR filtrando palabras de baja confianza (ruido de fondo, íconos, etc.)."""
    import pytesseract
    data = pytesseract.image_to_data(
        img, lang="spa+eng", output_type=pytesseract.Output.DICT,
    )
    words = [
        w for w, conf in zip(data["text"], data["conf"])
        if w.strip() and float(conf) >= OCR_MIN_CONFIDENCE
    ]
    return " ".join(words)


_SPELL_CHECKERS = None


def get_spell_checkers():
    """Diccionarios es/en, cargados una sola vez (son lentos de inicializar)."""
    global _SPELL_CHECKERS
    if _SPELL_CHECKERS is None:
        try:
            from spellchecker import SpellChecker
            _SPELL_CHECKERS = (SpellChecker(language="es"), SpellChecker(language="en"))
        except ImportError:
            _SPELL_CHECKERS = ()
    return _SPELL_CHECKERS


def find_misspelled_spans(text, checkers):
    """(start, end) de palabras que ningún diccionario (es/en) reconoce."""
    if not checkers:
        return []
    matches = list(re.finditer(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,}", text))
    if not matches:
        return []
    lower_words = [m.group().lower() for m in matches]
    unknown_sets = [ck.unknown(lower_words) for ck in checkers]
    common_unknown = set.intersection(*unknown_sets)
    return [(m.start(), m.end()) for m in matches if m.group().lower() in common_unknown]


def _match_case(original, suggestion):
    """pyspellchecker siempre sugiere en minúscula; conservamos las
    mayúsculas de la palabra original (p. ej. inicio de oración)."""
    if original.isupper():
        return suggestion.upper()
    if original[:1].isupper():
        return suggestion[:1].upper() + suggestion[1:]
    return suggestion


def is_meaningful_text(text):
    """Descarta ruido típico del OCR: strings cortos o sin palabras reales."""
    if len(text) < 4:
        return False
    if not re.search(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,}", text):
        return False
    letters = sum(c.isalpha() or c.isspace() for c in text)
    return letters / len(text) >= 0.6


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _rounded_rect_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """Botón con esquinas redondeadas y feedback en el press (no en el release),
    como pide el HIG: la respuesta táctil tiene que sentirse instantánea."""

    def __init__(self, parent, text, command, bg, fg, font, outline=None,
                 padx=18, pady=9, disabled_bg="#D2D2D7", disabled_fg="#A0A0A5"):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0,
                          cursor="hand2", takefocus=1)
        self.command = command
        self.bg_color = bg
        self.fg_color = fg
        self.outline = outline
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg
        self.enabled = True
        self._padx, self._pady = padx, pady
        self._font = tkfont.Font(family=font[0], size=font[1],
                                  weight=font[2] if len(font) > 2 else "normal")
        self._shape = self._label = self._focus_ring = None
        self.set_text(text)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_activate_key)
        self.bind("<space>", self._on_activate_key)

    FOCUS_MARGIN = 3  # aire alrededor del botón para que el anillo de foco no se recorte

    def set_text(self, text):
        text_w = self._font.measure(text)
        text_h = self._font.metrics("linespace")
        w, h = text_w + self._padx * 2, text_h + self._pady * 2
        m = self.FOCUS_MARGIN
        self.configure(width=w + 2 * m, height=h + 2 * m)
        r = min(RADIUS, h / 2)
        fill = self.bg_color if self.enabled else self.disabled_bg
        fg = self.fg_color if self.enabled else self.disabled_fg
        points = _rounded_rect_points(m + 1, m + 1, m + w - 1, m + h - 1, r)
        if self._shape is None:
            self._shape = self.create_polygon(
                points, smooth=True, fill=fill, outline=self.outline or "", width=1,
            )
            self._label = self.create_text(m + w / 2, m + h / 2, text=text, fill=fg, font=self._font)
        else:
            self.coords(self._shape, *points)
            self.coords(self._label, m + w / 2, m + h / 2)
            self.itemconfig(self._label, text=text)

    def _on_press(self, _event):
        if self.enabled:
            self.itemconfig(self._shape, fill=ACCENT_PRESSED)

    def _on_release(self, event):
        if not self.enabled:
            return
        self.itemconfig(self._shape, fill=self.bg_color)
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def _on_activate_key(self, _event):
        if self.enabled:
            self.command()

    def _on_focus_in(self, _event):
        self._draw_focus_ring()

    def _on_focus_out(self, _event):
        if self._focus_ring is not None:
            self.delete(self._focus_ring)
            self._focus_ring = None

    def _draw_focus_ring(self):
        if self._focus_ring is not None:
            self.delete(self._focus_ring)
        x0, y0, x1, y1 = self.bbox(self._shape)
        r = min(RADIUS + 2, (y1 - y0) / 2)
        self._focus_ring = self.create_polygon(
            _rounded_rect_points(x0 - 2, y0 - 2, x1 + 2, y1 + 2, r),
            smooth=True, fill="", outline=ACCENT, width=2,
        )

    def set_state(self, enabled):
        self.enabled = enabled
        fill = self.bg_color if enabled else self.disabled_bg
        text_fill = self.fg_color if enabled else self.disabled_fg
        self.itemconfig(self._shape, fill=fill)
        self.itemconfig(self._label, fill=text_fill)
        self.configure(cursor="hand2" if enabled else "arrow")


class RoundedPanel(tk.Frame):
    """Contenedor con esquinas curvas de verdad (no un Frame rectangular con
    borde). El contenido va adentro de `self.inner`.

    Con `size_to_content=True` (paneles de una fila fija, como la zona de
    carga) el panel mide el alto real de `self.inner` y se ajusta solo —
    nada de alturas fijas adivinadas. Con `size_to_content=False` (default,
    para paneles que se empaquetan con fill+expand, como el cuadro de
    texto) el panel ocupa lo que el padre le dé, de arriba hacia abajo.
    """

    def __init__(self, parent, bg=PANEL_BG, border=BORDER, radius=PANEL_RADIUS,
                 size_to_content=False):
        super().__init__(parent, bg=parent["bg"])
        self.bg_color = bg
        self.border_color = border
        self.radius = radius
        self.size_to_content = size_to_content
        self._inset = radius
        self.canvas = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0,
                                 height=1 if size_to_content else 0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        if size_to_content:
            self.inner.bind("<Configure>", self._on_inner_resize)

    def _on_canvas_resize(self, event):
        w, h = max(event.width, 2), max(event.height, 2)
        r = min(self.radius, w / 2, h / 2)
        self._inset = max(r, 1)
        self.canvas.delete("bg")
        self.canvas.create_polygon(
            _rounded_rect_points(1, 1, w - 1, h - 1, r),
            smooth=True, fill=self.bg_color, outline=self.border_color, width=1,
            tags="bg",
        )
        self.canvas.tag_lower("bg")
        # El contenido se inserta a `r` px del borde para que sus esquinas
        # rectas queden tapadas por la curva (si no, asoman por fuera).
        new_w = max(w - 2 * self._inset, 1)
        self.canvas.coords(self._window, self._inset, self._inset)
        if self.size_to_content:
            self.canvas.itemconfig(self._window, width=new_w)
        else:
            self.canvas.itemconfig(self._window, width=new_w, height=max(h - 2 * self._inset, 1))

    def _on_inner_resize(self, event):
        target_h = event.height + 2 * self._inset
        if abs(self.canvas.winfo_reqheight() - target_h) > 1:
            self.canvas.configure(height=target_h)


# ---------- App principal ----------

class AvisaVidApp:
    def __init__(self, root):
        self.root = root
        self.video_path = None
        self.metadata = None
        self.results = []  # lista de (segundos, fuente, texto)
        self._thumb_path = os.path.join(tempfile.gettempdir(), "avisavid_thumb.jpg")

        root.title("AvisaVid")
        root.geometry("640x720")
        root.configure(bg=BG)
        self._setup_style()
        self._build_menu()
        self._build_ui()
        self._check_deps()

    def _build_menu(self):
        is_mac = sys.platform == "darwin"
        menubar = tk.Menu(self.root)

        if is_mac:
            app_menu = tk.Menu(menubar, name="apple", tearoff=0)
            menubar.add_cascade(menu=app_menu)
            app_menu.add_command(label="Acerca de AvisaVid", command=self._show_about)
            self.root.createcommand("tk::mac::Quit", self.root.destroy)

        mod = "Cmd" if is_mac else "Ctrl"
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Abrir video…", command=self.select_file,
                               accelerator=f"{mod}+O")
        file_menu.add_separator()
        file_menu.add_command(label="Cerrar", command=self.root.destroy,
                               accelerator=f"{mod}+W")
        if not is_mac:
            file_menu.add_command(label="Salir", command=self.root.destroy,
                                   accelerator=f"{mod}+Q")
        menubar.add_cascade(label="Archivo", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Deshacer", accelerator=f"{mod}+Z",
                               command=lambda: self.text_box.event_generate("<<Undo>>"))
        edit_menu.add_command(label="Rehacer", accelerator=f"{mod}+Shift+Z",
                               command=lambda: self.text_box.event_generate("<<Redo>>"))
        edit_menu.add_separator()
        edit_menu.add_command(label="Cortar", accelerator=f"{mod}+X",
                               command=lambda: self.text_box.event_generate("<<Cut>>"))
        edit_menu.add_command(label="Copiar", accelerator=f"{mod}+C",
                               command=lambda: self.text_box.event_generate("<<Copy>>"))
        edit_menu.add_command(label="Pegar", accelerator=f"{mod}+V",
                               command=lambda: self.text_box.event_generate("<<Paste>>"))
        edit_menu.add_separator()
        edit_menu.add_command(label="Seleccionar todo", accelerator=f"{mod}+A",
                               command=self._select_all_text)
        menubar.add_cascade(label="Edición", menu=edit_menu)

        self.root.config(menu=menubar)

        cmd_key = "Command" if is_mac else "Control"
        self.root.bind_all(f"<{cmd_key}-o>", lambda e: self.select_file())
        self.root.bind_all(f"<{cmd_key}-w>", lambda e: self.root.destroy())
        self.root.bind_all(f"<{cmd_key}-a>", lambda e: self._select_all_text())
        if not is_mac:
            self.root.bind_all(f"<{cmd_key}-q>", lambda e: self.root.destroy())

    def _select_all_text(self):
        self.text_box.tag_add("sel", "1.0", "end")
        return "break"

    def _show_about(self):
        messagebox.showinfo(
            "Acerca de AvisaVid",
            "AvisaVid\n\nLee textos en pantalla (OCR) y transcribe el audio de un video, localmente.",
        )

    def _setup_style(self):
        style = ttk.Style()
        # 'clam' es el único tema ttk que respeta colores custom en Windows
        # (los temas nativos "vista"/"xpnative" ignoran background/foreground).
        style.theme_use("clam")
        style.configure(
            "Flat.Horizontal.TProgressbar",
            troughcolor=BG, background=ACCENT, bordercolor=BG,
            lightcolor=ACCENT, darkcolor=ACCENT, thickness=6,
        )
        # #8E8E93 es el systemGray de Apple: 3.26:1 sobre blanco, pasa el mínimo
        # de contraste para UI no textual (la versión anterior, #E5E5EA, daba 1.26:1).
        style.configure(
            "TScrollbar", troughcolor=PANEL_BG, background="#8E8E93",
            bordercolor=PANEL_BG, arrowcolor="#8E8E93", width=8, gripcount=0,
        )
        style.map("TScrollbar", background=[("active", TEXT_MUTED)])

    # ---- UI ----
    def _build_ui(self):
        pad = {"padx": 20, "pady": 8}

        # Fuente de marca solo para el título; si no cargó (plataforma no
        # soportada, archivo faltante), cae al tipo de sistema en negrita.
        title_font = (
            (BRAND_FONT_NAME, 30)
            if BRAND_FONT_NAME in tkfont.families()
            else (FONT_FAMILY, 22, "bold")
        )
        title = tk.Label(
            self.root, text="AvisaVid", font=title_font,
            bg=BG, fg=TEXT_MAIN,
        )
        title.pack(anchor="w", padx=20, pady=(20, 2))

        subtitle = tk.Label(
            self.root, text="Cargá un video para leer sus textos y su audio",
            font=(FONT_FAMILY, 12), bg=BG, fg=TEXT_MUTED,
        )
        subtitle.pack(anchor="w", padx=20, pady=(0, 16))

        # Zona de carga: fila compacta (thumbnail chico + nombre + botón),
        # para no robarle lugar vertical al cuadro de texto, que es lo importante.
        drop_panel = RoundedPanel(self.root, bg=PANEL_BG, size_to_content=True)
        drop_panel.pack(fill="x", **pad)
        row = drop_panel.inner

        # Centrado como bloque: un frame de contenido sin fill, empaquetado
        # con expand=True, queda centrado en el ancho disponible del panel.
        content = tk.Frame(row, bg=PANEL_BG)
        content.pack(expand=True, pady=12)

        preview_wrap = tk.Frame(content, bg=PANEL_BG, width=88, height=54)
        preview_wrap.pack(side="left", padx=(0, 14), anchor="n")
        preview_wrap.pack_propagate(False)
        self.preview_label = tk.Label(preview_wrap, bg=PANEL_BG, anchor="center")
        self.preview_label.pack(fill="both", expand=True)
        self._preview_photo = None

        text_col = tk.Frame(content, bg=PANEL_BG)
        text_col.pack(side="left")

        self.drop_label = tk.Label(
            text_col, text="Ningún video cargado", anchor="w",
            font=(FONT_FAMILY, 13), bg=PANEL_BG, fg=TEXT_MUTED,
        )
        self.drop_label.pack(fill="x")

        self.select_btn = RoundedButton(
            text_col, text="Elegir video…", command=self.select_file,
            bg=ACCENT, fg="white", font=(FONT_FAMILY, 12, "bold"),
        )
        self.select_btn.pack(anchor="w", pady=(8, 0))

        # Info bar
        self.info_frame = tk.Frame(self.root, bg=BG)
        self.info_frame.pack(fill="x", **pad)
        self.info_label = tk.Label(
            self.info_frame, text="", font=(FONT_FAMILY, 11),
            bg=BG, fg=TEXT_MUTED, justify="left",
        )
        self.info_label.pack(anchor="w")

        # Botón analizar + progreso
        action_frame = tk.Frame(self.root, bg=BG)
        action_frame.pack(fill="x", **pad)

        self.analyze_btn = RoundedButton(
            action_frame, text="Analizar video", command=self.start_analysis,
            bg=ACCENT, fg="white", font=(FONT_FAMILY, 12, "bold"),
        )
        self.analyze_btn.pack(side="left")
        self.analyze_btn.set_state(False)

        self.progress = ttk.Progressbar(
            action_frame, mode="indeterminate", length=200,
            style="Flat.Horizontal.TProgressbar",
        )
        # No se empaqueta acá: los indicadores de progreso son transitorios,
        # solo deben verse mientras corre una operación (progress-indicators.md).

        self.status_label = tk.Label(
            action_frame, text="", font=(FONT_FAMILY, 11),
            bg=BG, fg=TEXT_MUTED,
        )
        self.status_label.pack(side="left")

        self.timer_label = tk.Label(
            action_frame, text="", font=(FONT_FAMILY, 11),
            bg=BG, fg=TEXT_MUTED,
        )
        self.timer_label.pack(side="left", padx=(8, 0))
        self._timer_job = None
        self._analysis_start = None

        # Traducir
        translate_frame = tk.Frame(self.root, bg=BG)
        translate_frame.pack(fill="x", padx=20, pady=(0, 4))

        tk.Label(
            translate_frame, text="Traducir a:", font=(FONT_FAMILY, 12),
            bg=BG, fg=TEXT_MUTED,
        ).pack(side="left", padx=(0, 8))

        self.translate_lang = tk.StringVar(value="Elegir…")
        self.translate_menu = ttk.OptionMenu(
            translate_frame, self.translate_lang, "Elegir…",
            "Español", "Inglés", command=self._on_translate_selected,
        )
        self.translate_menu.pack(side="left")

        # Lector de texto
        reader_title = tk.Label(
            self.root, text="Texto detectado", font=(FONT_FAMILY, 14, "bold"),
            bg=BG, fg=TEXT_MAIN,
        )
        reader_title.pack(anchor="w", padx=20, pady=(16, 6))

        text_panel = RoundedPanel(self.root, bg=PANEL_BG)
        text_panel.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        text_area = text_panel.inner

        self.text_box = tk.Text(
            text_area, wrap="word", font=(FONT_FAMILY, 12),
            bg=PANEL_BG, fg=TEXT_MAIN, relief="flat", padx=6, pady=6,
            undo=True, insertbackground=TEXT_MAIN, selectbackground=ACCENT,
            selectforeground="white",
        )
        scrollbar = ttk.Scrollbar(text_area, command=self.text_box.yview)
        self.text_box.configure(yscrollcommand=scrollbar.set)
        self.text_box.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        audio_font = tkfont.Font(family=FONT_FAMILY, size=12, weight="bold")
        pantalla_font = tkfont.Font(family=FONT_FAMILY, size=12, weight="normal")
        self.text_box.tag_configure("audio", foreground=ACCENT, font=audio_font)
        self.text_box.tag_configure("pantalla", foreground="#8A6D00", font=pantalla_font)
        self.text_box.tag_configure("error", foreground="#D70015", font=pantalla_font)
        self.text_box.tag_configure("misspell", underline=True)
        self.text_box.tag_bind("misspell", "<Button-3>", self._show_suggestions)
        self.text_box.tag_bind("misspell", "<Enter>",
                                lambda e: self.text_box.config(cursor="hand2"))
        self.text_box.tag_bind("misspell", "<Leave>",
                                lambda e: self.text_box.config(cursor="xterm"))

        # Exportar / corregir
        export_frame = tk.Frame(self.root, bg=BG)
        export_frame.pack(fill="x", padx=20, pady=(0, 20))
        export_btn = RoundedButton(
            export_frame, text="Exportar .txt", command=self.export_text,
            bg=PANEL_BG, fg=TEXT_MAIN, font=(FONT_FAMILY, 12), outline=BORDER,
        )
        export_btn.pack(side="right")
        correct_btn = RoundedButton(
            export_frame, text="Corregir texto", command=self.correct_text,
            bg=PANEL_BG, fg=TEXT_MAIN, font=(FONT_FAMILY, 12), outline=BORDER,
        )
        correct_btn.pack(side="right", padx=(0, 10))

    def _check_deps(self):
        missing = [b for b in ("ffmpeg", "ffprobe", "tesseract") if not check_binary(b)]
        if missing:
            if sys.platform == "darwin":
                hint = "brew install ffmpeg tesseract tesseract-lang"
            elif sys.platform == "win32":
                hint = "winget install Gyan.FFmpeg UB-Mannheim.TesseractOCR"
            else:
                hint = "sudo apt install ffmpeg tesseract-ocr"
            messagebox.showwarning(
                "Faltan dependencias",
                "No encontré: " + ", ".join(missing) + f"\nInstalá con: {hint}",
            )

    # ---- Acciones ----
    def select_file(self):
        path = filedialog.askopenfilename(
            title="Elegí un video",
            filetypes=[("Videos", "*.mp4 *.mov *.m4v *.avi *.mkv")],
        )
        if not path:
            return
        self.video_path = path
        self.drop_label.config(text=os.path.basename(path), fg=TEXT_MAIN)
        self.select_btn.set_text("Cambiar video")
        try:
            self.metadata = ffprobe_metadata(path)
        except Exception as e:
            messagebox.showerror("Error leyendo el video", str(e))
            return
        self._render_metadata()
        self._update_preview(path)
        self.analyze_btn.set_state(True)
        self.status_label.config(text="Listo para analizar")

    def _update_preview(self, path):
        if extract_thumbnail(path, self._thumb_path):
            try:
                img = Image.open(self._thumb_path)
                img.thumbnail((88, 54))
                self._preview_photo = ImageTk.PhotoImage(img)
                self.preview_label.config(image=self._preview_photo)
                return
            except Exception:
                pass
        self._preview_photo = None
        self.preview_label.config(image="")

    def _render_metadata(self):
        m = self.metadata
        size_mb = os.path.getsize(self.video_path) / (1024 * 1024)
        text = (
            f"Formato: {m['container']} ({m['codec']})   |   "
            f"Duración: {fmt_time(m['duration'])}   |   "
            f"Resolución: {m['width']}x{m['height']}   |   "
            f"Tamaño: {size_mb:.1f} MB   |   "
            f"Audio: {'sí' if m['has_audio'] else 'no'}"
        )
        self.info_label.config(text=text)

    def start_analysis(self):
        if not self.video_path:
            return
        self.analyze_btn.set_state(False)
        self.progress.pack(side="left", padx=14, before=self.status_label)
        self.progress.start(12)
        self.status_label.config(text="Analizando…")
        self.text_box.delete("1.0", "end")
        self._analysis_start = time.time()
        self._tick_timer()
        threading.Thread(target=self._analyze_thread, daemon=True).start()

    def _tick_timer(self):
        elapsed = time.time() - self._analysis_start
        self.timer_label.config(text=fmt_time(elapsed))
        self._timer_job = self.root.after(1000, self._tick_timer)

    def _stop_timer(self):
        if self._timer_job is not None:
            self.root.after_cancel(self._timer_job)
            self._timer_job = None

    def _analyze_thread(self):
        try:
            results = self._run_pipeline()
        except Exception as e:
            self.root.after(0, lambda: self._on_error(e))
            return
        self.root.after(0, lambda: self._on_done(results))

    def _run_pipeline(self):
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            # 1) Texto en pantalla (OCR)
            self._set_status("Extrayendo frames…")
            frames = extract_frames(self.video_path, tmp, self.metadata["duration"])
            try:
                import pytesseract
            except ImportError:
                pytesseract = None

            last_text = None
            prev_img = None
            prev_t = None
            freeze_start = None
            for frame_path, t in frames:
                img = Image.open(frame_path)

                # Errores visuales: frame negro / imagen congelada
                if frame_brightness(img) < BLACK_FRAME_THRESHOLD:
                    results.append((t, "error", "Frame negro detectado"))
                if prev_img is not None:
                    if frame_diff(prev_img, img) < FREEZE_DIFF_THRESHOLD:
                        if freeze_start is None:
                            freeze_start = prev_t
                    elif freeze_start is not None:
                        results.append(
                            (freeze_start, "error",
                             f"Posible imagen congelada hasta {fmt_time(t)}")
                        )
                        freeze_start = None
                prev_img, prev_t = img, t

                # Texto en pantalla (OCR)
                if pytesseract is None:
                    continue
                try:
                    text = ocr_confident_text(img)
                except Exception:
                    text = ""
                if text and text != last_text and is_meaningful_text(text):
                    results.append((t, "pantalla", text))
                    last_text = text

            if freeze_start is not None and frames:
                results.append(
                    (freeze_start, "error",
                     f"Posible imagen congelada hasta {fmt_time(frames[-1][1])}")
                )

            # 2) Audio (Whisper local)
            if self.metadata.get("has_audio"):
                self._set_status("Transcribiendo audio…")
                wav_path = os.path.join(tmp, "audio.wav")
                if extract_audio(self.video_path, wav_path):
                    try:
                        from faster_whisper import WhisperModel
                        model = WhisperModel("base", device="cpu", compute_type="int8")
                        segments, _ = model.transcribe(wav_path, language="es")
                        for seg in segments:
                            txt = seg.text.strip()
                            if txt:
                                results.append((seg.start, "audio", txt))
                    except ImportError:
                        results.append(
                            (0, "aviso",
                             "faster-whisper no instalado: sin transcripción de audio.")
                        )
                    except Exception as e:
                        results.append((0, "aviso", f"Whisper falló: {e}"))

        results.sort(key=lambda r: r[0])
        return results

    def _set_status(self, msg):
        self.root.after(0, lambda: self.status_label.config(text=msg))

    def _on_done(self, results):
        self._stop_timer()
        self.results = results
        self.progress.stop()
        self.progress.pack_forget()
        self.analyze_btn.set_state(True)
        elapsed = fmt_time(time.time() - self._analysis_start)
        self.status_label.config(text=f"Listo — {len(results)} detecciones ({elapsed})")
        self.timer_label.config(text="")
        self.text_box.delete("1.0", "end")
        if not results:
            self.text_box.insert("end", "No se detectó texto ni audio.")
            return
        for t, source, text in results:
            tag = source if source in ("audio", "pantalla", "error") else None
            line = f"[{fmt_time(t)}] ({source}) {text}\n\n"
            if tag:
                self.text_box.insert("end", line, tag)
            else:
                self.text_box.insert("end", line)

    def _on_error(self, error):
        self._stop_timer()
        self.progress.stop()
        self.progress.pack_forget()
        self.analyze_btn.set_state(True)
        self.status_label.config(text="Error")
        self.timer_label.config(text="")
        messagebox.showerror("Error al analizar", str(error))

    def export_text(self):
        content = self.text_box.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Nada para exportar", "Analizá un video primero.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Texto", "*.txt")],
            initialfile="textos_video.txt",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("Exportado", f"Guardado en {path}")

    # ---- Corrector ortográfico ----
    def correct_text(self):
        checkers = get_spell_checkers()
        if not checkers:
            messagebox.showwarning(
                "Corrector no disponible",
                "Falta pyspellchecker. Instalá con: pip install pyspellchecker",
            )
            return
        content = self.text_box.get("1.0", "end")
        self.text_box.tag_remove("misspell", "1.0", "end")
        spans = find_misspelled_spans(content, checkers)
        for start, end in spans:
            self.text_box.tag_add("misspell", f"1.0+{start}c", f"1.0+{end}c")
        if spans:
            self.status_label.config(text=f"{len(spans)} palabra(s) para revisar")
        else:
            self.status_label.config(text="Sin errores ortográficos detectados")

    def _show_suggestions(self, event):
        index = self.text_box.index(f"@{event.x},{event.y}")
        start = self.text_box.index(f"{index} wordstart")
        end = self.text_box.index(f"{index} wordend")
        word = self.text_box.get(start, end)
        if not word.strip():
            return

        checkers = get_spell_checkers()
        suggestions = []
        for ck in checkers:
            for cand in sorted(ck.candidates(word.lower()) or []):
                cand = _match_case(word, cand)
                if cand not in suggestions:
                    suggestions.append(cand)
        suggestions = suggestions[:6]

        menu = tk.Menu(self.root, tearoff=0)
        if suggestions:
            for cand in suggestions:
                menu.add_command(
                    label=cand,
                    command=lambda c=cand, s=start, e=end: self._replace_word(s, e, c),
                )
            menu.add_separator()
        else:
            menu.add_command(label="(sin sugerencias)", state="disabled")
            menu.add_separator()
        menu.add_command(
            label="Ignorar",
            command=lambda: self.text_box.tag_remove("misspell", start, end),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _replace_word(self, start, end, new_word):
        tags = [t for t in self.text_box.tag_names(start) if t != "misspell"]
        self.text_box.delete(start, end)
        self.text_box.insert(start, new_word, tuple(tags))

    # ---- Traductor ----
    def _on_translate_selected(self, choice):
        lang_code = "es" if choice == "Español" else "en"
        self.translate_text(lang_code)

    def translate_text(self, target_lang):
        content = self.text_box.get("1.0", "end")
        if not content.strip():
            messagebox.showinfo("Nada para traducir", "Analizá un video primero.")
            return
        try:
            import deep_translator  # noqa: F401
        except ImportError:
            messagebox.showwarning(
                "Traductor no disponible",
                "Instalá con: pip install deep-translator\n(Necesita conexión a internet.)",
            )
            return
        self.status_label.config(text="Traduciendo…")
        lines = content.split("\n")
        threading.Thread(
            target=self._translate_thread, args=(lines, target_lang), daemon=True,
        ).start()

    def _translate_thread(self, lines, target_lang):
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="auto", target=target_lang)

        # Mandar todo junto en pocas peticiones grandes en vez de una por línea
        # (el endpoint gratuito de Google limita a 5 peticiones por segundo).
        chunks, current, length = [], [], 0
        for line in lines:
            if current and length + len(line) + 1 > 4500:
                chunks.append("\n".join(current))
                current, length = [], 0
            current.append(line)
            length += len(line) + 1
        if current:
            chunks.append("\n".join(current))

        try:
            translated_chunks = []
            for i, chunk in enumerate(chunks):
                if i > 0:
                    time.sleep(0.3)
                translated_chunks.append(translator.translate(chunk) or "")
        except Exception as e:
            self.root.after(0, lambda: self._on_translate_error(e))
            return
        result = "\n".join(translated_chunks).split("\n")
        self.root.after(0, lambda: self._on_translated(result))

    def _on_translated(self, lines):
        self.text_box.delete("1.0", "end")
        self.text_box.insert("end", "\n".join(lines))
        self.status_label.config(text="Traducido")

    def _on_translate_error(self, error):
        self.status_label.config(text="Error al traducir")
        messagebox.showerror("Error al traducir", str(error))


def main():
    ensure_brand_font_loaded()
    root = tk.Tk()
    AvisaVidApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
