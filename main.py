"""Rebuild Plugins - batch-rebuild Unreal Engine plugins for another engine version.

The window is two permanent zones and two contextual ones:

    chrome      dark header, carries the target configuration AND the primary action
    workspace   the plugin list - the product; everything else frames it
    run line    2px progress, only while building
    console     bottom drawer, opened per plugin, closed by default

The build logic lives in ue.py and is untouched by this file.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
import time
import tkinter as tk
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from PIL import Image, ImageTk

import ue

APP_DIR = ue.app_dir()
WORK_DIR = APP_DIR / "Work"          # our editable copies of the selected plugins
OUT_DIR = APP_DIR / "RebuiltPlugins"
LOG_DIR = APP_DIR / "BuildLogs"   # not "Logs": Windows would merge it with the legacy logs/
PLATFORMS = ["Win64", "Linux", "Mac", "Android", "IOS"]


class T:
    """One dark scale. Every surface is a step on it, every border is the same line,
    so nothing in the window belongs to a different theme."""
    BG = "#0E1014"            # the gutter the panels sit on
    CHROME = "#171A21"        # header bar
    PANEL = "#15181E"         # panel body: the list, the console
    PANEL_HEAD = "#1A1E26"    # a panel's own header strip
    RAISED = "#212630"        # control fill
    BORDER = "#2E3440"        # the one border colour
    HAIRLINE = "#232833"      # separators inside a panel
    HOVER = "#1C212B"
    SELECT = "#1D2A40"

    TEXT = "#DDE1E8"
    MUTED = "#8B93A1"
    DIM = "#636B7A"

    ACCENT = "#4C82F7"
    ACCENT_DOWN = "#3D6FE0"
    ACCENT_SOFT = "#2C4478"
    ACCENT_OFF = "#242A36"

    OK = "#5BC47E"
    FAIL = "#FF7F6E"
    FAIL_WASH = "#251B1A"
    BUSY = "#6BA5F7"
    SKIP = "#E2B65C"
    IDLE = "#59616F"


BORDERED = dict(highlightthickness=1, highlightbackground=T.BORDER,
                highlightcolor=T.BORDER, bd=0)


STATUS_COLOUR = {"ready": T.IDLE, "building": T.BUSY, "ok": T.OK,
                 "failed": T.FAIL, "copied": T.SKIP}

ROW_H = 52
ROW_H_FAILED = 76
PAD = 20                      # the single horizontal rhythm of the window

F = {}
TR = {}


def make_scrollbar_style(root):
    """tk.Scrollbar ignores colours on Windows and renders bright white against a
    dark panel. clam is the only built-in theme that honours them."""
    style = ttk.Style(root)
    style.theme_use("clam")
    for orient in ("Vertical", "Horizontal"):
        name = f"Dark.{orient}.TScrollbar"
        style.configure(name, background=T.RAISED, troughcolor=T.PANEL,
                        bordercolor=T.PANEL, darkcolor=T.RAISED, lightcolor=T.RAISED,
                        arrowcolor=T.DIM, relief="flat", borderwidth=0, gripcount=0,
                        arrowsize=12)
        style.map(name, background=[("active", T.BORDER)],
                  arrowcolor=[("active", T.TEXT)])
    return style


def make_fonts():
    F["title"] = tkfont.Font(family="Segoe UI", size=9, weight="bold")
    F["row"] = tkfont.Font(family="Segoe UI", size=10)
    F["meta"] = tkfont.Font(family="Segoe UI", size=9)
    F["meta_b"] = tkfont.Font(family="Segoe UI", size=9, weight="bold")
    F["section"] = tkfont.Font(family="Segoe UI", size=8, weight="bold")
    F["button"] = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    F["mono"] = tkfont.Font(family="Consolas", size=9)
    F["big"] = tkfont.Font(family="Segoe UI", size=15)


def t(key, *args):
    s = TR.get(key, key)
    return s.format(*args) if args else s


def resource(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


def load_language(code):
    global TR
    try:
        TR = json.loads(resource("locales", f"{code}.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        TR = {}


def fit(text, font, max_px):
    """Truncate to an ellipsis at the exact pixel width - canvas text does not clip."""
    if max_px <= 0:
        return ""
    if font.measure(text) <= max_px:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if font.measure(text[:mid] + "\u2026") <= max_px:
            low = mid
        else:
            high = mid - 1
    return text[:low] + "\u2026"


def compact_error(text, limit=200):
    """`C:\\long\\path\\File.cpp(88): error C2039: msg` -> `File.cpp(88): error ...`.
    The directory is noise; the file, line and message are the signal."""
    match = re.search(r"[^\\/]+\(\d+[,\d]*\)\s*:.*", text)
    if match:
        text = match.group(0)
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


def open_path(path):
    path = Path(path)
    if not path.exists():
        return
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


# --------------------------------------------------------------------- controls

class Pill(tk.Label):
    """A platform toggle. Filled when on, outlined-dark when off - no checkbox chrome."""

    def __init__(self, master, text, value, on_toggle):
        super().__init__(master, text=text, font=F["meta"], padx=10, pady=3,
                         cursor="hand2", **BORDERED)
        self.value = value
        self.on_toggle = on_toggle
        self.bind("<Button-1>", self._click)
        self.paint()

    def _click(self, _event):
        self.value = not self.value
        self.paint()
        self.on_toggle()

    def paint(self):
        if self.value:
            self.configure(background=T.ACCENT, foreground="white",
                           highlightbackground=T.ACCENT, highlightcolor=T.ACCENT)
        else:
            self.configure(background=T.RAISED, foreground=T.MUTED,
                           highlightbackground=T.BORDER, highlightcolor=T.BORDER)


class Primary(tk.Label):
    """The one dominant action. A label, so nothing native fights the chrome."""

    def __init__(self, master, command):
        super().__init__(master, font=F["button"], padx=26, pady=11, bd=0,
                         cursor="hand2", background=T.ACCENT, foreground="white")
        self.command = command
        self.enabled = True
        self.bind("<Button-1>", lambda e: self.enabled and self.command())
        self.bind("<Enter>", lambda e: self.enabled and self.configure(background=T.ACCENT_DOWN))
        self.bind("<Leave>", lambda e: self.enabled and self.configure(background=T.ACCENT))

    def set_state(self, label, enabled):
        self.enabled = enabled
        self.configure(text=label, background=T.ACCENT if enabled else T.ACCENT_OFF,
                       foreground="white" if enabled else T.MUTED,
                       cursor="hand2" if enabled else "arrow")


class Link(tk.Label):
    """A quiet text action. Subordinate to the primary by construction, not by size."""

    def __init__(self, master, text, command, bg, fg, hover=None):
        super().__init__(master, text=text, font=F["meta"], background=bg,
                         foreground=fg, cursor="hand2", bd=0, padx=6, pady=2)
        self.base, self.hover = fg, hover or T.ACCENT
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self.configure(foreground=self.hover))
        self.bind("<Leave>", lambda e: self.configure(foreground=self.base))

    def relabel(self, text):
        self.configure(text=text)


# ------------------------------------------------------------------ plugin list

class PluginList(tk.Canvas):
    """The workspace. Drawn rather than assembled: a Treeview cannot give a row two
    lines, a status bar down its left edge, or a taller shape when it fails."""

    def __init__(self, master, target_label, on_select, on_output):
        super().__init__(master, background=T.PANEL, highlightthickness=0, bd=0)
        self.target_label = target_label
        self.rows = []            # dicts: plugin, status, detail, fixes, activity, log
        self.on_select = on_select
        self.on_output = on_output
        self.selected = None
        self.hovered = None
        self.spans = []           # (y0, y1, index) for hit testing
        self.filter_failed = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Button-3>", self._context)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._hover(None))
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-e.delta // 120, "units"))

    # -------------------------------------------------------------- data access
    def add(self, plugin):
        # every plugin starts queued: "Copied" is a result, not a prediction
        self.rows.append({"plugin": plugin, "status": "ready", "detail": "",
                          "fixes": [], "activity": "", "log": None,
                          "hint": t("content_only_hint") if plugin.content_only else ""})
        self.redraw()

    def update_row(self, index, **kw):
        if 0 <= index < len(self.rows):
            self.rows[index].update(kw)
            self.redraw()

    def clear(self):
        self.rows.clear()
        self.selected = None
        self.redraw()

    def remove(self, index):
        if 0 <= index < len(self.rows):
            self.rows[index] = None
            self.selected = None
            self.redraw()

    def visible(self):
        for i, row in enumerate(self.rows):
            if row is None:
                continue
            if self.filter_failed and row["status"] != "failed":
                continue
            yield i, row

    def counts(self):
        tally = {"ok": 0, "failed": 0, "copied": 0}
        for row in self.rows:
            if row and row["status"] in tally:
                tally[row["status"]] += 1
        return tally

    # ------------------------------------------------------------------ drawing
    def redraw(self):
        self.delete("all")
        self.spans = []
        width = self.winfo_width() or 800
        y = 0
        for index, row in self.visible():
            height = ROW_H_FAILED if row["status"] == "failed" else ROW_H
            self._draw_row(index, row, y, height, width)
            self.spans.append((y, y + height, index))
            y += height
        self.configure(scrollregion=(0, 0, width, max(y, 1)))

    def _draw_row(self, index, row, y, height, width):
        """Two anchors per row: identity on the left, action and outcome on the right,
        so a 1400px window reads as a grid instead of text hugging one edge."""
        colour = STATUS_COLOUR[row["status"]]
        failed = row["status"] == "failed"
        plugin = row["plugin"]

        if index == self.selected:
            self.create_rectangle(0, y, width, y + height, fill=T.SELECT, width=0)
        elif failed:
            self.create_rectangle(0, y, width, y + height, fill=T.FAIL_WASH, width=0)
        elif index == self.hovered:
            self.create_rectangle(0, y, width, y + height, fill=T.HOVER, width=0)

        # the status bar down the left edge: position and shape, not just colour
        self.create_rectangle(0, y, 3, y + height, fill=colour, width=0)
        self.create_line(0, y + height, width, y + height, fill=T.HAIRLINE)

        right = width - PAD
        if row["log"]:
            label = t("log_link") + "  \u203a"
            self.create_text(right, y + 18, anchor="e", font=F["meta"],
                             fill=T.ACCENT, text=label, tags=(f"out:{index}",))
            right -= F["meta"].measure(label) + 28

        self.create_text(PAD, y + 18, anchor="w", font=F["row"], fill=T.TEXT,
                         text=fit(plugin.friendly, F["row"], right - PAD))

        # line 2 left: version, migration, verdict - the four facts, in reading order
        x = PAD
        # an unknown source version would read as "\u2014 \u2192 5.8"; say "\u2192 5.8" instead
        made_for = plugin.engine_version
        jump = (f"\u2192 {self.target_label()}" if made_for == ue.UNKNOWN
                else f"{made_for} \u2192 {self.target_label()}")
        facts = ((f"v{plugin.version}", F["meta"], T.MUTED),
                 (jump, F["meta"], T.MUTED),
                 (t("state_" + row["status"]), F["meta_b"], colour))
        for position, (text, font, fill) in enumerate(facts):
            if position:
                self.create_text(x, y + 38, anchor="w", font=F["meta"],
                                 fill=T.DIM, text="\u00b7")
                x += 12
            self.create_text(x, y + 38, anchor="w", font=font, fill=fill, text=text)
            x += font.measure(text) + 8

        # line 2 right: what the tool did, or what it is doing right now
        trail = (row["activity"]
                 or (t("fix_1" if len(row["fixes"]) == 1 else "fixes_n", len(row["fixes"]))
                     if row["fixes"] else "")
                 or (row.get("hint", "") if row["status"] == "ready" else ""))
        if trail:
            self.create_text(width - PAD, y + 38, anchor="e", font=F["meta"],
                             fill=T.MUTED, text=fit(trail, F["meta"], width - PAD - x - 30))

        if failed and row["detail"]:
            self.create_text(PAD, y + 60, anchor="w", font=F["mono"], fill=T.FAIL,
                             text=fit(row["detail"], F["mono"], width - 2 * PAD))

    # ------------------------------------------------------------- interaction
    def _index_at(self, event):
        y = self.canvasy(event.y)
        for y0, y1, index in self.spans:
            if y0 <= y < y1:
                return index
        return None

    def _click(self, event):
        for tag in self.gettags("current"):
            if tag.startswith("out:"):
                return self.on_output(int(tag[4:]))
        index = self._index_at(event)
        self.selected = index
        self.redraw()
        self.on_select(index)

    def _context(self, event):
        """Row-level actions live here: a menu costs no pixels in the layout."""
        index = self._index_at(event)
        if index is None:
            return
        self.selected = index
        self.redraw()
        menu = tk.Menu(self, tearoff=0, bd=0, font=F["meta"])
        if self.rows[index]["log"]:
            menu.add_command(label=t("menu_log"), command=lambda: self.on_output(index))
        menu.add_command(label=t("menu_remove"), command=lambda: self.remove(index))
        menu.tk_popup(event.x_root, event.y_root)

    def _motion(self, event):
        self._hover(self._index_at(event))

    def _hover(self, index):
        if index != self.hovered:
            self.hovered = index
            self.redraw()


# ---------------------------------------------------------------------- console

class Console(tk.Frame):
    """A drawer, not furniture. Closed by default, titled with one plugin's name."""

    MIN_H, DEFAULT_H = 120, 260

    def __init__(self, master):
        super().__init__(master, background=T.PANEL, height=self.DEFAULT_H, **BORDERED)
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.index = None
        self.path = None
        self.offset = 0

        grip = tk.Frame(self, background=T.PANEL_HEAD, height=5,
                        cursor="sb_v_double_arrow")
        grip.grid(row=0, column=0, sticky="ew")
        grip.bind("<B1-Motion>", self._drag)

        head = tk.Frame(self, background=T.PANEL_HEAD)
        head.grid(row=1, column=0, sticky="ew")
        inner = tk.Frame(head, background=T.PANEL_HEAD)
        inner.pack(fill="x", padx=PAD - 4, pady=(4, 9))
        inner.columnconfigure(1, weight=1)
        self.title = tk.Label(inner, font=F["section"], background=T.PANEL_HEAD,
                              foreground=T.MUTED, anchor="w")
        self.title.grid(row=0, column=0, sticky="w", padx=(4, 0))
        Link(inner, t("copy"), self.copy, T.PANEL_HEAD, T.MUTED,
             T.TEXT).grid(row=0, column=2)
        Link(inner, "✕", self.hide, T.PANEL_HEAD, T.MUTED,
             T.TEXT).grid(row=0, column=3, padx=(10, 0))
        tk.Frame(self, background=T.BORDER, height=1).grid(row=2, column=0, sticky="ew")

        body = tk.Frame(self, background=T.PANEL)
        body.grid(row=3, column=0, sticky="nsew", padx=(PAD, 0), pady=(8, 10))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.text = tk.Text(body, font=F["mono"], background=T.PANEL, bd=0,
                            foreground=T.TEXT, highlightthickness=0,
                            wrap="none", padx=0, pady=0, spacing1=1)
        self.text.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(body, command=self.text.yview,
                             style="Dark.Vertical.TScrollbar")
        vbar.grid(row=0, column=1, sticky="ns")
        # UAT lines are long absolute paths; without this the right half is unreachable
        hbar = ttk.Scrollbar(body, command=self.text.xview, orient="horizontal",
                             style="Dark.Horizontal.TScrollbar")
        hbar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.text.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set,
                            state="disabled")
        for tag, colour in (("err", T.FAIL), ("ok", T.OK),
                            ("fix", T.SKIP), ("muted", T.DIM)):
            self.text.tag_configure(tag, foreground=colour)

    def _drag(self, event):
        height = max(self.MIN_H, self.winfo_height() - event.y)
        self.configure(height=min(height, self.master.winfo_height() - 200))

    def show(self, index, name, path):
        self.index, self.path, self.offset = index, Path(path), 0
        self.title.configure(text=f"{t('console_title')}  \u2014  {name.upper()}")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.grid()
        self.pump()

    def hide(self):
        self.index = None
        self.grid_remove()

    def pump(self):
        """Tail the plugin's log file. Nothing streams through the UI queue, so a
        three-minute compile cannot flood it."""
        if self.index is None or not self.path or not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
        except OSError:
            return
        if chunk:
            self.text.configure(state="normal")
            for line in chunk.splitlines():
                tag = ("err" if ue.ERROR_LINE.search(line)
                       else "fix" if line.startswith("-- ") else "")
                self.text.insert("end", line + "\n", tag)
            self.text.configure(state="disabled")
            self.text.see("end")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))


# -------------------------------------------------------------------------- app

class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.plugins = []
        self.engines = ue.find_engines()
        self.engine_index = 0
        self.building = False
        self.total = 0
        self.notice_job = None
        self._build()
        self.root.after(60, self._drain)
        self.root.after(700, self._pump_console)

    # ----------------------------------------------------------------- assembly
    def _build(self):
        root = self.root
        root.title(t("title"))
        root.geometry("1180x760")
        root.minsize(940, 600)
        root.configure(background=T.BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)

        self._chrome()
        self._runline()
        self._workspace()

        self.console = Console(root)
        self.console.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        self.console.grid_remove()

        self._sync()
        if not self.engines:
            self.notice(t("no_engines"), error=True)

    def _chrome(self):
        bar = tk.Frame(self.root, background=T.CHROME)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        left = tk.Frame(bar, background=T.CHROME)
        left.grid(row=0, column=0, sticky="w", padx=(PAD, 0), pady=14)

        tk.Label(left, text=t("app_name"), font=F["section"], background=T.CHROME,
                 foreground=T.MUTED).grid(row=0, column=0, sticky="w")

        target = tk.Frame(left, background=T.CHROME)
        target.grid(row=1, column=0, sticky="w", pady=(9, 0))

        self.engine_button = tk.Menubutton(
            target, font=F["meta"], background=T.RAISED, foreground=T.TEXT,
            activebackground=T.BORDER, activeforeground=T.TEXT,
            relief="flat", padx=10, pady=4, cursor="hand2", **BORDERED)
        self.engine_button.grid(row=0, column=0)
        self.engine_menu = tk.Menu(self.engine_button, tearoff=0, bd=0,
                                   background=T.RAISED, foreground=T.TEXT,
                                   activebackground=T.ACCENT, activeforeground="white",
                                   font=F["meta"])
        for i, engine in enumerate(self.engines):
            self.engine_menu.add_command(label=f"UE {engine.version}      {engine.root}",
                                         command=lambda i=i: self.pick_engine(i))
        self.engine_button.configure(menu=self.engine_menu)

        tk.Label(target, text="\u00b7", font=F["meta"], background=T.CHROME,
                 foreground=T.BORDER).grid(row=0, column=1, padx=10)

        self.pills = {}
        for i, name in enumerate(PLATFORMS):
            pill = Pill(target, name, name == "Win64", self._sync)
            pill.grid(row=0, column=2 + i, padx=(0, 4))
            self.pills[name] = pill

        other = "ru" if CURRENT_LANG == "en" else "en"
        Link(bar, other.upper(), lambda: self.set_language(other), T.CHROME,
             T.MUTED, T.TEXT).grid(row=0, column=1, sticky="e",
                                                 padx=(0, 18))

        self.primary = Primary(bar, self.start_build)
        self.primary.grid(row=0, column=2, sticky="e", padx=(0, PAD))

    def _runline(self):
        self.runline = tk.Canvas(self.root, height=1, background=T.BORDER,
                                 highlightthickness=0, bd=0)
        self.runline.grid(row=1, column=0, sticky="ew")
        # a lighter segment for the plugin in flight, so the line is never blank
        # while the first (and possibly only) plugin compiles for three minutes
        self.runflight = self.runline.create_rectangle(0, 0, 0, 3, fill=T.ACCENT_SOFT,
                                                       width=0)
        self.runbar = self.runline.create_rectangle(0, 0, 0, 3, fill=T.ACCENT, width=0)
        self.done = 0
        # the canvas has no width until it is mapped, so redraw when it gets one
        self.runline.bind("<Configure>", lambda e: self._progress(self.done))

    def _section(self, panel):
        """A panel header, not a free-floating band: it lives inside the border."""
        bar = tk.Frame(panel, background=T.PANEL_HEAD)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(2, weight=1)
        inner = tk.Frame(bar, background=T.PANEL_HEAD)
        inner.pack(fill="x", padx=PAD - 4, pady=9)
        inner.columnconfigure(2, weight=1)

        self.section = tk.Label(inner, font=F["section"], background=T.PANEL_HEAD,
                                foreground=T.TEXT)
        self.section.grid(row=0, column=0, sticky="w", padx=(4, 0))

        # the failure count is the filter - the number people look for IS the control
        tally = tk.Frame(inner, background=T.PANEL_HEAD)
        tally.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.tally = {
            "ok": tk.Label(tally, font=F["meta"], background=T.PANEL_HEAD,
                           foreground=T.OK),
            "failed": Link(tally, "", self.toggle_filter, T.PANEL_HEAD, T.FAIL, T.FAIL),
            "copied": tk.Label(tally, font=F["meta"], background=T.PANEL_HEAD,
                               foreground=T.SKIP),
        }

        self.message = tk.Label(inner, font=F["meta"], background=T.PANEL_HEAD,
                                foreground=T.MUTED, anchor="w")
        self.message.grid(row=0, column=2, sticky="ew", padx=(18, 18))

        for column, (key, command) in enumerate(
                (("add_folder", self.pick_folder), ("add_zip", self.pick_zip),
                 ("clear", self.clear)), start=3):
            Link(inner, t(key), command, T.PANEL_HEAD, T.MUTED).grid(
                row=0, column=column, padx=(10, 0))
        tk.Frame(inner, background=T.BORDER, width=1).grid(row=0, column=6, sticky="ns",
                                                          padx=12, pady=2)
        Link(inner, t("build_folder"), lambda: open_path(OUT_DIR), T.PANEL_HEAD,
             T.MUTED).grid(row=0, column=7)

        tk.Frame(panel, background=T.BORDER, height=1).grid(row=1, column=0,
                                                            columnspan=2, sticky="ew")

    def _workspace(self):
        panel = tk.Frame(self.root, background=T.PANEL, **BORDERED)
        panel.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=(PAD - 6, PAD))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)

        self._section(panel)

        self.list = PluginList(panel, self.engine_label, self.on_select,
                               self.open_console)
        self.list.grid(row=2, column=0, sticky="nsew")
        bar = ttk.Scrollbar(panel, command=self.list.yview,
                            style="Dark.Vertical.TScrollbar")
        bar.grid(row=2, column=1, sticky="ns")
        self.list.configure(yscrollcommand=bar.set)

        self.empty = tk.Frame(panel, background=T.PANEL)
        tk.Label(self.empty, text=t("empty_title"), font=F["big"], background=T.PANEL,
                 foreground=T.TEXT).pack()
        tk.Label(self.empty, text=t("empty_body"), font=F["meta"], background=T.PANEL,
                 foreground=T.MUTED).pack(pady=(8, 20))
        buttons = tk.Frame(self.empty, background=T.PANEL)
        buttons.pack()
        for column, (key, command) in enumerate(
                (("empty_folder", self.pick_folder), ("empty_zip", self.pick_zip))):
            button = tk.Label(buttons, text=t(key), font=F["meta"], background=T.RAISED,
                              foreground=T.TEXT, padx=18, pady=8, cursor="hand2",
                              **BORDERED)
            button.grid(row=0, column=column, padx=6)
            button.bind("<Button-1>", lambda e, c=command: c())

    # ------------------------------------------------------------------- state
    def set_language(self, code):
        """The window is built entirely from state, so rebuilding it is the
        cheapest correct way to switch language - no widget bookkeeping."""
        global CURRENT_LANG
        if self.building:
            return
        CURRENT_LANG = code
        load_language(code)
        rows = [row for row in self.list.rows]
        for child in self.root.winfo_children():
            child.destroy()
        self._build()
        self.list.rows = rows
        self.list.redraw()
        self._sync()

    def engine_label(self):
        return self.engines[self.engine_index].version if self.engines else "?"

    def pick_engine(self, index):
        self.engine_index = index
        self._sync()
        self.list.redraw()

    def platforms(self):
        return [name for name, pill in self.pills.items() if pill.value]

    def toggle_filter(self):
        self.list.filter_failed = not self.list.filter_failed
        self._sync()
        self.list.redraw()

    def _sync(self):
        """One place where the chrome reflects the state it configures."""
        if self.engines:
            engine = self.engines[self.engine_index]
            self.engine_button.configure(
                text=f"UE {engine.version}   {fit(str(engine.root), F['meta'], 260)}   \u25be")
        else:
            self.engine_button.configure(text=t("no_engine_short"))

        live = [row for row in self.list.rows if row] if hasattr(self, "list") else []
        if self.building:
            self.primary.set_state(t("rebuilding"), False)
        elif live and self.engines and self.platforms():
            self.primary.set_state(t("rebuild_n", len(live)), True)
        else:
            self.primary.set_state(t("rebuild"), False)

        tally = self.list.counts() if live else {}
        if self.list.filter_failed and tally.get("failed"):
            label = t("section_filtered", tally["failed"], len(live))
        else:
            label = t("section_plugins", len(live))
        self.section.configure(text=label.upper())
        for key, widget in self.tally.items():
            if tally.get(key):
                label = t("tally_" + key, tally[key])
                widget.configure(text=label)
                widget.pack(side="left", padx=(0, 16))
            else:
                widget.pack_forget()

        if live:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.42, anchor="center")

    def notice(self, text, error=False):
        self.message.configure(text=text, foreground=T.FAIL if error else T.MUTED)
        if self.notice_job:
            self.root.after_cancel(self.notice_job)
            self.notice_job = None
        if not error:
            self.notice_job = self.root.after(
                6000, lambda: self.message.configure(text=""))

    # ----------------------------------------------------------------- sources
    def pick_folder(self):
        folder = filedialog.askdirectory(title=t("select_folder_title"))
        if folder:
            self.ingest([Path(folder)])

    def pick_zip(self):
        files = filedialog.askopenfilenames(title=t("select_zip_title"),
                                            filetypes=[(t("zip_files"), "*.zip")])
        if not files:
            return
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        roots = []
        for f in files:
            dest = WORK_DIR / Path(f).stem
            ue.unlock_tree(dest)
            shutil.rmtree(dest, ignore_errors=True)
            try:
                with zipfile.ZipFile(f) as z:
                    z.extractall(dest)
                roots.append(dest)
            except (OSError, zipfile.BadZipFile) as e:
                self.notice(t("unpack_failed", Path(f).name, e), error=True)
        self.ingest(roots, already_copied=True)

    def ingest(self, roots, already_copied=False):
        """Work on a copy; the user's originals are never modified."""
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        added = 0
        for root in roots:
            if not already_copied:
                dest = WORK_DIR / root.name
                ue.unlock_tree(dest)
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(root, dest)
                root = dest
            for plugin in ue.find_plugins(root):
                if any(p and p.path == plugin.path for p in self.plugins):
                    continue
                self.plugins.append(plugin)
                self.list.add(plugin)
                added += 1
        self.notice(t("found_n", added) if added else t("no_plugins_found"),
                    error=not added)
        self._sync()

    def clear(self):
        if self.building:
            return
        self.plugins.clear()
        self.list.clear()
        self.console.hide()
        ue.unlock_tree(WORK_DIR)
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        self._sync()

    def on_select(self, index):
        if index is not None and self.console.index is not None:
            self.open_console(index)

    def open_console(self, index):
        row = self.list.rows[index] if 0 <= index < len(self.list.rows) else None
        if row and row["log"]:
            self.console.show(index, row["plugin"].friendly, row["log"])

    def _pump_console(self):
        if self.console.index is not None and self.building:
            self.console.pump()
        self.root.after(700, self._pump_console)

    # ------------------------------------------------------------------ builds
    def start_build(self):
        todo = [(i, row["plugin"]) for i, row in enumerate(self.list.rows) if row]
        platforms = self.platforms()
        if not (todo and self.engines and platforms):
            return self.notice(t("nothing_to_build") if not todo else t("pick_a_platform"),
                               error=True)
        engine = self.engines[self.engine_index]
        self.building = True
        self.total = len(todo)
        self._sync()
        self.runline.configure(height=3)
        self._progress(0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._worker, args=(todo, engine, platforms),
                         daemon=True).start()

    def _progress(self, done):
        self.done = done
        width = self.runline.winfo_width() or 1
        step = width / max(1, self.total)
        self.runline.coords(self.runbar, 0, 0, step * done, 3)
        in_flight = step * (done + 1) if self.building and done < self.total else step * done
        self.runline.coords(self.runflight, 0, 0, in_flight, 3)

    def _worker(self, todo, engine, platforms):
        """Off the main thread: nothing here touches Tk, only self.events."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        siblings = [p.path for _, p in todo]
        for done, (index, plugin) in enumerate(todo, start=1):
            log_path = LOG_DIR / f"{stamp}_{plugin.name}.log"
            self.events.put(("row", index, {"status": "building", "activity": "",
                                            "log": str(log_path), "hint": ""}))
            beat = [0.0]
            with open(log_path, "w", encoding="utf-8") as fh:
                def on_line(line, fh=fh, index=index, beat=beat):
                    fh.write(line + "\n")
                    now = time.monotonic()
                    if now - beat[0] > 0.25:      # a heartbeat, not a firehose
                        beat[0] = now
                        self.events.put(("row", index, {"activity": line.strip()[:120]}))
                try:
                    result = ue.build_plugin(plugin, engine, OUT_DIR, on_line,
                                             platforms=platforms, siblings=siblings)
                except Exception as e:            # never kill the queue
                    fh.write(f"{type(e).__name__}: {e}\n")
                    result = ue.BuildResult(plugin.friendly, ok=False, errors=[str(e)])
            copied = any("content-only" in f for f in result.fixes)
            self.events.put(("row", index, {
                "status": ("copied" if copied else "ok") if result.ok else "failed",
                "detail": compact_error(result.errors[0]) if result.errors else "",
                "fixes": result.fixes, "activity": ""}))
            self.events.put(("progress", done, None))
        self.events.put(("finished", None, None))

    def _drain(self):
        """The single place where worker events become Tk calls. A bad event must
        never stop the loop - a dead drain is a frozen window."""
        try:
            while True:
                kind, index, payload = self.events.get_nowait()
                if kind == "row":
                    self.list.update_row(index, **payload)
                    if "status" in payload:
                        self._sync()
                elif kind == "progress":
                    self._progress(index)
                elif kind == "finished":
                    self.building = False
                    self.runline.configure(height=1)
                    self._progress(0)
                    self._sync()
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            self.root.after(60, self._drain)

    def on_close(self):
        if self.building and not messagebox.askyesno(t("title"), t("quit_while_building")):
            return
        self.root.destroy()


CURRENT_LANG = "en"

if __name__ == "__main__":
    root = tk.Tk()
    make_fonts()
    make_scrollbar_style(root)
    load_language(CURRENT_LANG)
    try:
        root.iconphoto(True, ImageTk.PhotoImage(Image.open(resource("public", "icon.png"))))
    except Exception:
        pass
    App(root)
    root.mainloop()
