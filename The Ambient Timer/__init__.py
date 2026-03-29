"""
Ambient Review Timer
====================
Two circular tick-mark clocks in the corners of the Anki reviewer.
  LEFT  — how long you took last time (frozen)
  RIGHT — live count-up, with optional colour progression

Options via Tools > Ambient Timer Options.
Toggle on/off via Tools > Ambient Timer (checkmark).
"""

import math
import time

from aqt import mw, gui_hooks
from aqt.qt import (
    QWidget, QPainter, QPen, QColor, QFont, QTimer,
    Qt, QRectF, QPointF, QPalette, QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QScrollArea,
    QSlider, QLabel, QPushButton, QColorDialog, QSpinBox,
    QGroupBox, QDialogButtonBox, QComboBox, QCheckBox, QAction,
    QFrame, QSizePolicy,
)


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULTS = {
    "enabled":          True,
    "size":             60,
    "margin_top":        7,
    "margin_bottom":    50,
    "position":        "top",
    "num_size":         16,
    "opacity":          25,
    "show_ticks":       True,
    "show_label":       True,
    "tick_style":      "lines",
    "color_lit":        [220, 220, 220],
    "color_dim":        [190, 190, 190],
    "color_label":      [180, 180, 180],
    "color_lit_dark":   [ 50,  50,  50],
    "color_dim_dark":   [100, 100, 100],
    "color_label_dark": [ 80,  80,  80],
    "auto_invert":      True,
    "color_mode":      "progression",
    "threshold_easy":   2,
    "threshold_good":   5,
    "threshold_hard":  10,
    "threshold_again": 15,
    "color_easy":   [ 80, 200,  80],
    "color_good":   [220, 210,  60],
    "color_hard":   [230, 130,  30],
    "color_again":  [210,  50,  50],
}

def _cfg():
    stored = mw.addonManager.getConfig(__name__) or {}
    return {**DEFAULTS, **stored}

def _save_cfg(cfg: dict):
    mw.addonManager.writeConfig(__name__, cfg)


# ---------------------------------------------------------------------------
# Dark mode detection
# ---------------------------------------------------------------------------

def _is_dark_mode() -> bool:
    """Return True if Anki is currently using a dark palette."""
    try:
        bg = QApplication.instance().palette().color(QPalette.ColorRole.Window)
        return bg.lightness() < 128
    except Exception:
        return True

import colorsys as _colorsys

def _lighten(rgb: list) -> list:
    """Return a lightened version of the colour (for light mode display)."""
    r, g, b = rgb[0]/255, rgb[1]/255, rgb[2]/255
    h, l, s = _colorsys.rgb_to_hls(r, g, b)
    l_new = min(0.92, l + (1.0 - l) * 0.55)
    r2, g2, b2 = _colorsys.hls_to_rgb(h, l_new, s)
    return [round(r2*255), round(g2*255), round(b2*255)]

def _darken(rgb: list) -> list:
    """Return a darkened version of the colour (for dark mode display)."""
    r, g, b = rgb[0]/255, rgb[1]/255, rgb[2]/255
    h, l, s = _colorsys.rgb_to_hls(r, g, b)
    l_new = max(0.08, l * 0.45)
    r2, g2, b2 = _colorsys.hls_to_rgb(h, l_new, s)
    return [round(r2*255), round(g2*255), round(b2*255)]

def _dark_equivalent(rgb: list) -> list:
    """Kept for backwards compat — returns darkened colour."""
    return _darken(rgb)

def _auto_colors(cfg: dict) -> tuple:
    """Return (color_lit, color_dim, color_label) for current light/dark mode.
    Base colour is the user-chosen colour. Light mode = lightened, dark mode = darkened.
    When auto is off, uses the manually set dark colours."""
    dark = _is_dark_mode()
    if dark:
        if cfg.get("auto_invert", True):
            # Dark mode = lightened version of base (visible on dark background)
            lit   = _lighten(cfg.get("color_lit",   DEFAULTS["color_lit"]))
            dim   = _lighten(cfg.get("color_dim",   DEFAULTS["color_dim"]))
            label = _lighten(cfg.get("color_label", DEFAULTS["color_label"]))
        else:
            lit   = cfg.get("color_lit_dark",   DEFAULTS["color_lit_dark"])
            dim   = cfg.get("color_dim_dark",   DEFAULTS["color_dim_dark"])
            label = cfg.get("color_label_dark", DEFAULTS["color_label_dark"])
    else:
        if cfg.get("auto_invert", True):
            # Light mode = darkened version of base (visible on white background)
            lit   = _darken(cfg.get("color_lit",   DEFAULTS["color_lit"]))
            dim   = _darken(cfg.get("color_dim",   DEFAULTS["color_dim"]))
            label = _darken(cfg.get("color_label", DEFAULTS["color_label"]))
        else:
            lit   = cfg.get("color_lit",   DEFAULTS["color_lit"])
            dim   = cfg.get("color_dim",   DEFAULTS["color_dim"])
            label = cfg.get("color_label", DEFAULTS["color_label"])
    return lit, dim, label

def _progression_color(elapsed_ms: int, cfg: dict) -> list:
    sec     = elapsed_ms / 1000
    t_easy  = cfg["threshold_easy"]
    t_good  = cfg["threshold_good"]
    t_hard  = cfg["threshold_hard"]
    t_again = cfg["threshold_again"]
    if sec < t_easy:
        return None   # use base lit colour
    elif sec < t_good:
        return cfg["color_easy"]
    elif sec < t_hard:
        return cfg["color_good"]
    elif sec < t_again:
        return cfg["color_hard"]
    else:
        return cfg["color_again"]


# ---------------------------------------------------------------------------
# Fetch last answer time
# ---------------------------------------------------------------------------

def _get_last_answer_ms(card_id: int):
    try:
        row = mw.col.db.first(
            "SELECT time FROM revlog WHERE cid = ? ORDER BY id DESC LIMIT 1",
            card_id,
        )
        return int(row[0]) if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Clock widget
# ---------------------------------------------------------------------------

TICK_COUNT = 60

class ClockWidget(QWidget):
    def __init__(self, parent, label: str, is_live: bool = False):
        super().__init__(parent)
        self.label       = label
        self._elapsed_ms = 0
        self._is_live    = is_live
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._apply_config()

    def _apply_config(self):
        cfg  = _cfg()
        size = cfg["size"]
        self.setFixedSize(size, size + 22)
        self.setWindowOpacity(cfg["opacity"] / 100)

    def set_elapsed(self, ms):
        self._elapsed_ms = ms
        self.update()

    def paintEvent(self, event):
        cfg  = _cfg()
        size = cfg["size"]
        p    = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx      = size / 2
        cy      = size / 2
        outer_r = size / 2 - 4

        total_sec = max(0, int(self._elapsed_ms / 1000))
        sec       = total_sec % 60
        minutes   = total_sec // 60

        # Auto light/dark base colours
        lit_rgb, dim_rgb, label_rgb = _auto_colors(cfg)

        # Colour progression for live clock
        if self._is_live and cfg.get("color_mode") == "progression" and self._elapsed_ms >= 0:
            prog = _progression_color(self._elapsed_ms, cfg)
            if prog:
                lit_rgb = prog

        color_lit   = QColor(*lit_rgb,   210)
        color_dim   = QColor(*dim_rgb,    50)
        color_text  = QColor(*lit_rgb,   200)
        color_label = QColor(*label_rgb, 140)

        # Ticks
        tick_style = cfg.get("tick_style", "lines")
        if tick_style != "none":
            for i in range(TICK_COUNT):
                angle    = (i / TICK_COUNT) * 2 * math.pi - math.pi / 2
                is_major = (i % 5 == 0)
                lit      = (i < sec) and (self._elapsed_ms >= 0)
                c        = color_lit if lit else color_dim

                if tick_style == "dots":
                    r_dot = size * (0.028 if is_major else 0.018)
                    dot_r = outer_r - r_dot
                    cx2   = cx + math.cos(angle) * dot_r
                    cy2   = cy + math.sin(angle) * dot_r
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(c)
                    p.drawEllipse(QPointF(cx2, cy2), r_dot, r_dot)
                    p.setBrush(Qt.BrushStyle.NoBrush)

                elif tick_style == "minimal":
                    if not is_major:
                        continue
                    tick_len = size * 0.13
                    inner_r  = outer_r - tick_len
                    x1 = cx + math.cos(angle) * inner_r
                    y1 = cy + math.sin(angle) * inner_r
                    x2 = cx + math.cos(angle) * outer_r
                    y2 = cy + math.sin(angle) * outer_r
                    pen = QPen(c)
                    pen.setWidthF(1.4 if lit else 0.8)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

                elif tick_style == "arc":
                    tick_len = size * (0.12 if is_major else 0.08)
                    inner_r  = outer_r - tick_len
                    x1 = cx + math.cos(angle) * inner_r
                    y1 = cy + math.sin(angle) * inner_r
                    x2 = cx + math.cos(angle) * outer_r
                    y2 = cy + math.sin(angle) * outer_r
                    pen = QPen(c)
                    pen.setWidthF(2.4 if lit else 1.4)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

                else:  # "lines"
                    tick_len = size * (0.10 if is_major else 0.065)
                    inner_r  = outer_r - tick_len
                    x1 = cx + math.cos(angle) * inner_r
                    y1 = cy + math.sin(angle) * inner_r
                    x2 = cx + math.cos(angle) * outer_r
                    y2 = cy + math.sin(angle) * outer_r
                    pen = QPen(c)
                    pen.setWidthF(0.9 if lit else 0.6)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        # Centre number
        p.setPen(QPen(color_text))
        if self._elapsed_ms < 0:
            num_text = "\u2014"
        elif minutes > 0:
            num_text = f"{minutes}m"
        else:
            num_text = str(sec)

        font = QFont("Helvetica Neue", max(6, cfg["num_size"]))
        font.setWeight(QFont.Weight.Light)
        p.setFont(font)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, num_text)

        # Label
        if cfg.get("show_label", True):
            p.setPen(QPen(color_label))
            lf = QFont("Helvetica Neue", max(5, int(size * 0.12)))
            lf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
            p.setFont(lf)
            p.drawText(
                QRectF(0, size, size, 22),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self.label,
            )
        p.end()


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class AmbientTimerManager:
    def __init__(self):
        self._left   = None
        self._right  = None
        self._timer  = None
        self._start  = None
        self._active = False

    def _ensure_widgets(self):
        if self._left is None:
            self._left  = ClockWidget(mw, "LAST TIME", is_live=False)
            self._right = ClockWidget(mw, "NOW",       is_live=True)

    def rebuild_widgets(self):
        if self._left:
            self._left.hide();  self._left.deleteLater();  self._left  = None
        if self._right:
            self._right.hide(); self._right.deleteLater(); self._right = None
        self._ensure_widgets()
        self._position_widgets()

    def _position_widgets(self):
        if not self._left:
            return
        cfg      = _cfg()
        size     = cfg["size"]
        win_w    = mw.width()
        win_h    = mw.height()
        widget_h = size + 22
        INSET    = 4
        if cfg.get("position", "top") == "bottom":
            y = win_h - widget_h - cfg.get("margin_bottom", 50)
        else:
            y = cfg.get("margin_top", 7)
        self._left.move(INSET, y)
        self._right.move(win_w - size - INSET, y)
        self._left.raise_()
        self._right.raise_()

    def start(self, card):
        if not _cfg().get("enabled", True):
            return
        self._ensure_widgets()
        self._left._apply_config()
        self._right._apply_config()
        self._position_widgets()

        prev_ms = _get_last_answer_ms(card.id)
        self._left.set_elapsed(prev_ms if prev_ms is not None else -1)

        self._start = time.monotonic()
        self._right.set_elapsed(0)

        self._left.show()
        self._right.show()
        self._active = True

        if self._timer is None:
            self._timer = QTimer(mw)
            self._timer.timeout.connect(self._tick)
        self._timer.start(200)

    def _tick(self):
        if not self._active or self._start is None:
            return
        self._right.set_elapsed(int((time.monotonic() - self._start) * 1000))

    def stop(self):
        self._active = False
        if self._timer:
            self._timer.stop()
        for w in (self._left, self._right):
            if w:
                w.hide()


_manager = AmbientTimerManager()


# ---------------------------------------------------------------------------
# Options dialog  — scrollable, cleaner layout
# ---------------------------------------------------------------------------

class OptionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Ambient Timer Options")
        self.setMinimumSize(440, 400)
        self.resize(550, 450)
        self._cfg = _cfg()
        self._build_ui()

    # ── widget helpers ────────────────────────────────────────────────────────

    def _color_btn(self, key):
        btn = QPushButton()
        btn.setFixedSize(56, 28)
        self._refresh_btn(btn, key)
        btn.clicked.connect(lambda _, b=btn, k=key: self._pick_color(b, k))
        return btn

    def _refresh_btn(self, btn, key):
        r, g, b = self._cfg[key]
        btn.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); "
            f"border: 1px solid #666; border-radius: 4px;"
        )

    def _pick_color(self, btn, key):
        r, g, b = self._cfg[key]
        c = QColorDialog.getColor(QColor(r, g, b), self, "Pick colour")
        if c.isValid():
            self._cfg[key] = [c.red(), c.green(), c.blue()]
            self._refresh_btn(btn, key)

    def _slider_row(self, key, lo, hi, suffix=""):
        row  = QHBoxLayout()
        sl   = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setSingleStep(1)
        sl.setPageStep(1)
        sl.setValue(int(self._cfg[key]))
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(int(self._cfg[key]))
        spin.setSuffix(suffix)
        spin.setFixedWidth(68)
        sl.valueChanged.connect(lambda v, s=spin, k=key: (
            self._cfg.__setitem__(k, v), s.blockSignals(True),
            s.setValue(v), s.blockSignals(False)
        ))
        spin.valueChanged.connect(lambda v, s=sl, k=key: (
            self._cfg.__setitem__(k, v), s.blockSignals(True),
            s.setValue(v), s.blockSignals(False)
        ))
        row.addWidget(sl)
        row.addWidget(spin)
        return row

    def _section(self, title: str) -> tuple:
        """Return (QGroupBox, QFormLayout) for a named section."""
        box  = QGroupBox(title)
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(12, 8, 12, 8)
        return box, form

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _thresh_spin(self, key, default):
        sp = QSpinBox()
        sp.setRange(1, 300)
        sp.setValue(int(self._cfg.get(key, default)))
        sp.setSuffix("s")
        sp.setFixedWidth(68)
        sp.valueChanged.connect(lambda v, k=key: self._cfg.__setitem__(k, v))
        return sp

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(0)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        main = QVBoxLayout(content)
        main.setContentsMargins(12, 12, 12, 4)
        main.setSpacing(10)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ── Row 1: Position + Clock Adjustment side by side ─────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Position
        pos_box, pform = self._section("Position")
        pos_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._pos_combo = QComboBox()
        self._pos_combo.addItem("Top corners",    "top")
        self._pos_combo.addItem("Bottom corners", "bottom")
        cur = self._cfg.get("position", "top")
        self._pos_combo.setCurrentIndex(0 if cur == "top" else 1)
        pform.addRow("Corner:", self._pos_combo)

        self._top_w = QWidget()
        tl = QHBoxLayout(self._top_w); tl.setContentsMargins(0,0,0,0)
        tl.addLayout(self._slider_row("margin_top", 0, 300, "px"))
        self._top_lbl = QLabel("Top clearance:")
        pform.addRow(self._top_lbl, self._top_w)

        self._bot_w = QWidget()
        bl = QHBoxLayout(self._bot_w); bl.setContentsMargins(0,0,0,0)
        bl.addLayout(self._slider_row("margin_bottom", 0, 300, "px"))
        self._bot_lbl = QLabel("Bottom clearance:")
        pform.addRow(self._bot_lbl, self._bot_w)

        def _update_pos(idx=None):
            is_top = self._pos_combo.currentData() == "top"
            self._cfg["position"] = self._pos_combo.currentData()
            self._top_lbl.setVisible(is_top);  self._top_w.setVisible(is_top)
            self._bot_lbl.setVisible(not is_top); self._bot_w.setVisible(not is_top)

        self._pos_combo.currentIndexChanged.connect(_update_pos)
        _update_pos()
        row1.addWidget(pos_box)

        # Clock Adjustment
        sz_box, sform = self._section("Clock Adjustment")
        sz_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sform.addRow("Clock diameter:", self._slider_row("size",     30, 150, "px"))
        sform.addRow("Number size:",    self._slider_row("num_size",  6,  40, "pt"))
        row1.addWidget(sz_box)
        main.addLayout(row1)

        # ── Row 2: Visibility ─────────────────────────────────────────────────
        top_two = QHBoxLayout()
        top_two.setSpacing(10)
        top_two.setAlignment(Qt.AlignmentFlag.AlignTop)

        # dummy placeholder so the next addWidget(sz_box) call below is removed
        vis_box, vform = self._section("Visibility")
        vis_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Checkboxes + opacity on one horizontal row
        vis_row_w = QWidget()
        vis_row_l = QHBoxLayout(vis_row_w)
        vis_row_l.setContentsMargins(0, 0, 0, 0)
        vis_row_l.setSpacing(12)

        ticks_chk = QCheckBox("Show ticks")
        ticks_chk.setChecked(self._cfg.get("show_ticks", True))
        ticks_chk.toggled.connect(lambda v: self._cfg.__setitem__("show_ticks", v))
        label_chk = QCheckBox("Show labels")
        label_chk.setChecked(self._cfg.get("show_label", True))
        label_chk.toggled.connect(lambda v: self._cfg.__setitem__("show_label", v))
        vis_row_l.addWidget(ticks_chk)
        vis_row_l.addWidget(label_chk)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        vis_row_l.addWidget(sep)

        op_lbl = QLabel("Opacity:")
        vis_row_l.addWidget(op_lbl)
        vis_row_l.addLayout(self._slider_row("opacity", 5, 100, "%"))
        vform.addRow(vis_row_w)

        top_two.addWidget(vis_box)
        main.addLayout(top_two)  # row2: visibility

        # ── Colours + Progression side by side ───────────────────────────────
        two_col = QHBoxLayout()
        two_col.setSpacing(10)

        # Left: Clock Colours
        col_box = QGroupBox("Clock Colours")
        col_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        col_vlay = QVBoxLayout(col_box)
        col_vlay.setSpacing(8)
        col_vlay.setContentsMargins(10, 8, 10, 8)

        # Auto toggle
        self._invert_chk = QCheckBox("Auto dark-mode colours")
        self._invert_chk.setChecked(self._cfg.get("auto_invert", True))
        col_vlay.addWidget(self._invert_chk)

        # Base picker — visible only when auto is ON
        self._pick_row_w = QWidget()
        pick_rl = QHBoxLayout(self._pick_row_w)
        pick_rl.setContentsMargins(0, 0, 0, 0)
        pick_rl.setSpacing(8)
        pick_rl.addWidget(QLabel("Base colour:"))
        self._base_colour_btn = QPushButton()
        self._base_colour_btn.setFixedSize(72, 32)
        col_vlay.addWidget(self._pick_row_w)
        pick_rl.addWidget(self._base_colour_btn)
        pick_rl.addStretch()

        # Light / Dark swatches
        from aqt.qt import QGridLayout
        prev_w = QWidget()
        prev_grid = QGridLayout(prev_w)
        prev_grid.setContentsMargins(0, 0, 0, 0)
        prev_grid.setSpacing(6)
        for ci, txt in [(0, "Light"), (1, "Dark")]:
            hl = QLabel(txt)
            hl.setFixedWidth(72)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.setStyleSheet("font-size: 11px; color: gray;")
            prev_grid.addWidget(hl, 0, ci)
        self._light_swatch = QPushButton()
        self._light_swatch.setFixedSize(72, 36)
        self._dark_swatch  = QPushButton()
        self._dark_swatch.setFixedSize(72, 36)
        prev_grid.addWidget(self._light_swatch, 1, 0)
        prev_grid.addWidget(self._dark_swatch,  1, 1)
        col_vlay.addWidget(prev_w)

        def _swatch_style(rgb):
            r, g, b = rgb
            return f"background-color: rgb({r},{g},{b}); border:1px solid #666; border-radius:4px;"

        def _refresh_colour_previews():
            auto = self._invert_chk.isChecked()
            self._cfg["auto_invert"] = auto
            base = self._cfg.get("color_lit", DEFAULTS["color_lit"])

            # Show/hide base picker
            self._pick_row_w.setVisible(auto)

            # Light mode = darkened (readable on white bg)
            lc = _darken(base)
            self._light_swatch.setStyleSheet(_swatch_style(lc))
            self._light_swatch.setEnabled(not auto)

            # Dark mode = lightened (readable on dark bg)
            if auto:
                dc = _lighten(base)
                self._cfg["color_lit_dark"]   = dc
                self._cfg["color_dim_dark"]   = dc
                self._cfg["color_label_dark"] = dc
            else:
                dc = self._cfg.get("color_lit_dark", _lighten(base))
            self._dark_swatch.setStyleSheet(_swatch_style(dc))
            self._dark_swatch.setEnabled(not auto)

            # Keep base btn styled
            self._base_colour_btn.setStyleSheet(_swatch_style(base))

        def _pick_base():
            base = self._cfg.get("color_lit", DEFAULTS["color_lit"])
            c = QColorDialog.getColor(QColor(*base), self, "Pick base colour")
            if c.isValid():
                self._cfg["color_lit"] = self._cfg["color_dim"] = self._cfg["color_label"] =                     [c.red(), c.green(), c.blue()]
                _refresh_colour_previews()

        def _pick_light():
            lc = _darken(self._cfg.get("color_lit", DEFAULTS["color_lit"]))
            c = QColorDialog.getColor(QColor(*lc), self, "Pick light mode colour")
            if c.isValid():
                self._cfg["color_lit"] = self._cfg["color_dim"] = self._cfg["color_label"] =                     [c.red(), c.green(), c.blue()]
                _refresh_colour_previews()

        def _pick_dark():
            dc = self._cfg.get("color_lit_dark", _lighten(self._cfg.get("color_lit", DEFAULTS["color_lit"])))
            c = QColorDialog.getColor(QColor(*dc), self, "Pick dark mode colour")
            if c.isValid():
                dark_c = [c.red(), c.green(), c.blue()]
                self._cfg["color_lit_dark"] = self._cfg["color_dim_dark"] = self._cfg["color_label_dark"] = dark_c
                _refresh_colour_previews()

        self._base_colour_btn.clicked.connect(_pick_base)
        self._light_swatch.clicked.connect(_pick_light)
        self._dark_swatch.clicked.connect(_pick_dark)
        self._invert_chk.toggled.connect(lambda _: _refresh_colour_previews())
        _refresh_colour_previews()

        two_col.addWidget(col_box)

        # Right: colour progression
        prog_box, prog_form = self._section("Progression Colours")
        prog_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        prog_chk = QCheckBox("Enable")
        prog_chk.setChecked(self._cfg.get("color_mode") == "progression")
        prog_form.addRow(prog_chk)

        prog_content = QWidget()
        pcont_form = QFormLayout(prog_content)
        pcont_form.setContentsMargins(0, 4, 0, 0)
        pcont_form.setSpacing(6)

        for tier, t_key, c_key, t_def in [
            ("Easy",  "threshold_easy",   "color_easy",   2),
            ("Good",  "threshold_good",   "color_good",   5),
            ("Hard",  "threshold_hard",   "color_hard",  10),
            ("Again", "threshold_again",  "color_again", 15),
        ]:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(6)
            row_l.addLayout(self._make_thresh_row(t_key, t_def))
            row_l.addWidget(self._color_btn(c_key))
            pcont_form.addRow(f"{tier}:", row_w)

        prog_form.addRow(prog_content)

        def _toggle_prog(checked):
            self._cfg["color_mode"] = "progression" if checked else "static"
            prog_content.setVisible(checked)

        prog_chk.toggled.connect(_toggle_prog)
        prog_content.setVisible(prog_chk.isChecked())
        two_col.addWidget(prog_box)

        main.addLayout(two_col)

        main.addStretch()

        # ── Bottom bar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 0, 12, 0)

        reset_btn = QPushButton("Reset to defaults")
        reset_btn.setFixedHeight(28)
        reset_btn.clicked.connect(self._reset)
        bar.addWidget(reset_btn)
        bar.addStretch()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        bar.addWidget(bb)
        outer.addLayout(bar)

    def _make_thresh_row(self, key, default):
        """A compact spinbox for threshold — no slider, just the spinbox."""
        row = QHBoxLayout()
        sp  = QSpinBox()
        sp.setRange(1, 300)
        sp.setValue(int(self._cfg.get(key, default)))
        sp.setSuffix("s")
        sp.setFixedWidth(68)
        sp.valueChanged.connect(lambda v, k=key: self._cfg.__setitem__(k, v))
        row.addWidget(sp)
        row.addStretch()
        return row

    def _reset(self):
        self._cfg = dict(DEFAULTS)
        self.close()
        OptionsDialog(mw).exec()

    def _accept(self):
        _save_cfg(self._cfg)
        _manager.rebuild_widgets()
        self.accept()


# ---------------------------------------------------------------------------
# Tools menu  — checkable toggle + options
# ---------------------------------------------------------------------------

def _open_options():
    OptionsDialog(mw).exec()

def _toggle_enabled():
    cfg = _cfg()
    cfg["enabled"] = not cfg.get("enabled", True)
    _save_cfg(cfg)
    _toggle_action.setChecked(cfg["enabled"])
    _toggle_action.setText("Ambient Timer: ON" if cfg["enabled"] else "Ambient Timer: OFF")
    if not cfg["enabled"]:
        _manager.stop()

_options_action = QAction("Ambient Timer Options\u2026", mw)
_options_action.triggered.connect(_open_options)
mw.form.menuTools.addAction(_options_action)

_toggle_action = QAction("Ambient Timer: ON", mw)
_toggle_action.setCheckable(True)
_enabled_now = _cfg().get("enabled", True)
_toggle_action.setChecked(_enabled_now)
_toggle_action.setText("Ambient Timer: ON" if _enabled_now else "Ambient Timer: OFF")
_toggle_action.triggered.connect(_toggle_enabled)
mw.form.menuTools.addAction(_toggle_action)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def on_show_question(card):
    _manager.start(card)

def on_show_answer(card):
    _manager.stop()

def on_review_cleanup():
    _manager.stop()

def _on_resize(event):
    _manager._position_widgets()
    _orig_resize(event)

_orig_resize = mw.resizeEvent
mw.resizeEvent = _on_resize

gui_hooks.reviewer_did_show_question.append(on_show_question)
gui_hooks.reviewer_did_show_answer.append(on_show_answer)
gui_hooks.reviewer_will_end.append(on_review_cleanup)
