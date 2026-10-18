"""Fenêtres interactives : Rogner et Ajout de fond."""
import ctypes
import subprocess
from ctypes import wintypes
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QLinearGradient
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

import actions

ACCENT = "#ef4b2b"

STYLE = f"""
QWidget {{ color: #3a2118; font-family: 'Segoe UI Variable Text', 'Segoe UI'; font-size: 12px; }}
QLabel#title {{ font-weight: 600; font-size: 13px; }}
QLabel#muted {{ color: #7a5548; }}
QPushButton {{ background: rgba(255,255,255,150); border: 1px solid rgba(255,255,255,200);
               border-radius: 7px; padding: 4px 10px; }}
QPushButton:hover {{ background: rgba(255,255,255,210); }}
QPushButton:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: 600; padding: 6px 16px; }}
QPushButton#primary:hover {{ background: #ff5d3b; }}
QPushButton#close {{ background: transparent; border: none; font-size: 15px; padding: 0 6px; }}
QSpinBox {{ background: rgba(255,255,255,170); border: 1px solid rgba(255,255,255,220); border-radius: 6px;
            padding: 3px 6px; min-width: 64px; }}
QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,170); border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; margin: -6px 0; border-radius: 8px;
                              border: 1px solid rgba(0,0,0,40); }}
"""


def force_foreground(hwnd):
    """Windows refuse le premier plan à une appli qui ne l'a pas : on s'attache brièvement au
    thread du premier plan actuel (souvent l'Explorateur) pour obtenir le droit de le prendre."""
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    fg_tid = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
    me = k.GetCurrentThreadId()
    attach = fg_tid and fg_tid != me
    if attach:
        u.AttachThreadInput(me, fg_tid, True)
    u.BringWindowToTop(wintypes.HWND(hwnd))
    u.SetForegroundWindow(wintypes.HWND(hwnd))
    if attach:
        u.AttachThreadInput(me, fg_tid, False)


def load_qimage(path):
    img = QImage(str(path))
    if img.isNull():  # HEIC, AVIF… -> passage par magick
        tmp = Path(tempfile.gettempdir()) / "dragshift-preview.png"
        subprocess.run([actions.MAGICK, f"{path}[0]", "-auto-orient", str(tmp)], creationflags=actions.NO_WINDOW)
        img = QImage(str(tmp))
    return img


class Panel(QDialog):
    """Dialogue sans bordure, fond dégradé chaud arrondi, déplaçable par le titre."""

    def __init__(self, title):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self._drag = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 16)
        root.setSpacing(10)
        head = QHBoxLayout()
        close = QPushButton("✕", objectName="close", clicked=self.reject)
        t = QLabel(title, objectName="title", alignment=Qt.AlignCenter)
        head.addWidget(close)
        head.addWidget(t, 1)
        head.addSpacing(close.sizeHint().width())
        root.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        root.addLayout(self.body)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(255, 214, 160, 250))
        g.setColorAt(0.55, QColor(255, 176, 150, 250))
        g.setColorAt(1, QColor(240, 160, 190, 250))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 190), 1))
        p.drawRoundedRect(r, 16, 16)

    def mousePressEvent(self, e):
        if e.position().y() < 40:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept_action()
        else:
            super().keyPressEvent(e)

    def accept_action(self):
        pass

    def show_centered(self):
        self.adjustSize()
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication
        scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()
        force_foreground(int(self.winId()))


# ======================================================================= Rogner

RATIOS = [("Libre", None), ("1:1", 1.0), ("16:9", 16 / 9), ("9:16", 9 / 16), ("4:3", 4 / 3), ("3:4", 3 / 4)]


class CropView(QWidget):
    changed = Signal()
    H = 9  # tolérance poignées (px écran)

    def __init__(self, img):
        super().__init__()
        self.img = img
        self.pix = QPixmap.fromImage(img)
        self.full = QRectF(0, 0, img.width(), img.height())
        self.sel = QRectF(self.full)
        self.ratio = None
        self.mode = None
        self.setMinimumSize(540, 460)
        self.setMouseTracking(True)

    # -- géométrie
    def _frame(self):
        s = min((self.width() - 20) / self.full.width(), (self.height() - 20) / self.full.height())
        w, h = self.full.width() * s, self.full.height() * s
        return s, QPointF((self.width() - w) / 2, (self.height() - h) / 2)

    def to_screen(self, r):
        s, o = self._frame()
        return QRectF(o.x() + r.x() * s, o.y() + r.y() * s, r.width() * s, r.height() * s)

    def _hit(self, pos):
        r = self.to_screen(self.sel)
        H = self.H
        left, right = abs(pos.x() - r.left()) < H, abs(pos.x() - r.right()) < H
        top, bottom = abs(pos.y() - r.top()) < H, abs(pos.y() - r.bottom()) < H
        inx = r.left() - H < pos.x() < r.right() + H
        iny = r.top() - H < pos.y() < r.bottom() + H
        edges = ""
        if inx and iny:
            edges += "l" if left else "r" if right else ""
            edges += "t" if top else "b" if bottom else ""
        if edges:
            return edges
        return "move" if r.contains(pos) else None

    def set_ratio(self, ratio):
        self.ratio = ratio
        if ratio:
            c = self.sel.center()
            w = self.sel.width()
            h = w / ratio
            if h > self.full.height():
                h = self.full.height()
                w = h * ratio
            if w > self.full.width():
                w = self.full.width()
                h = w / ratio
            r = QRectF(0, 0, w, h)
            r.moveCenter(c)
            self.sel = self._clamp_move(r)
        self.update()
        self.changed.emit()

    def _clamp_move(self, r):
        r = QRectF(r)
        r.moveLeft(min(max(r.left(), 0), self.full.width() - r.width()))
        r.moveTop(min(max(r.top(), 0), self.full.height() - r.height()))
        return r

    def set_size(self, w, h):
        r = QRectF(self.sel.left(), self.sel.top(), min(w, self.full.width()), min(h, self.full.height()))
        self.sel = self._clamp_move(r)
        self.update()

    # -- souris
    def mousePressEvent(self, e):
        self.mode = self._hit(e.position())
        self.start = e.position()
        self.orig = QRectF(self.sel)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self.mode:
            h = self._hit(pos)
            cur = {"lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor, "rt": Qt.SizeBDiagCursor,
                   "lb": Qt.SizeBDiagCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                   "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "move": Qt.SizeAllCursor}.get(h, Qt.ArrowCursor)
            self.setCursor(cur)
            return
        s, _ = self._frame()
        dx, dy = (pos.x() - self.start.x()) / s, (pos.y() - self.start.y()) / s
        o = self.orig
        if self.mode == "move":
            self.sel = self._clamp_move(o.translated(dx, dy))
        else:
            l, t, r, b = o.left(), o.top(), o.right(), o.bottom()
            W, Hh = self.full.width(), self.full.height()
            if "l" in self.mode: l = min(max(0, l + dx), r - 10)
            if "r" in self.mode: r = max(min(W, r + dx), l + 10)
            if "t" in self.mode: t = min(max(0, t + dy), b - 10)
            if "b" in self.mode: b = max(min(Hh, b + dy), t + 10)
            if self.ratio:
                w, h = r - l, b - t
                if self.mode in ("t", "b"):
                    w = h * self.ratio
                else:
                    h = w / self.ratio
                # ancre = côté opposé, puis on borne à l'image
                ax = o.right() if "l" in self.mode else o.left()
                ay = o.bottom() if "t" in self.mode else o.top()
                maxw = ax if "l" in self.mode else W - ax
                maxh = ay if "t" in self.mode else Hh - ay
                if self.mode in ("t", "b"):
                    cx = o.center().x()
                    maxw = 2 * min(cx, W - cx)
                if self.mode in ("l", "r"):
                    cy = o.center().y()
                    maxh = 2 * min(cy, Hh - cy)
                k = min(1.0, maxw / w, maxh / h)
                w, h = w * k, h * k
                if self.mode in ("t", "b"):
                    l = o.center().x() - w / 2
                elif "l" in self.mode:
                    l = ax - w
                else:
                    l = ax
                if self.mode in ("l", "r"):
                    t = o.center().y() - h / 2
                elif "t" in self.mode:
                    t = ay - h
                else:
                    t = ay
                r, b = l + w, t + h
            self.sel = QRectF(QPointF(l, t), QPointF(r, b))
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _):
        self.mode = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        img_r = self.to_screen(self.full)
        p.drawPixmap(img_r, self.pix, QRectF(self.pix.rect()))
        sel = self.to_screen(self.sel)
        path = QPainterPath()
        path.addRect(img_r)
        inner = QPainterPath()
        inner.addRect(sel)
        p.fillPath(path.subtracted(inner), QColor(40, 30, 30, 150))
        p.setPen(QPen(QColor(ACCENT), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(sel)
        # tiers
        p.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for i in (1, 2):
            x = sel.left() + sel.width() * i / 3
            y = sel.top() + sel.height() * i / 3
            p.drawLine(QPointF(x, sel.top()), QPointF(x, sel.bottom()))
            p.drawLine(QPointF(sel.left(), y), QPointF(sel.right(), y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        for pt in (sel.topLeft(), sel.topRight(), sel.bottomLeft(), sel.bottomRight()):
            p.drawEllipse(pt, 5, 5)
        for pt in (QPointF(sel.center().x(), sel.top()), QPointF(sel.center().x(), sel.bottom()),
                   QPointF(sel.left(), sel.center().y()), QPointF(sel.right(), sel.center().y())):
            p.drawRoundedRect(QRectF(pt.x() - 9, pt.y() - 3, 18, 6) if pt.y() in (sel.top(), sel.bottom())
                              else QRectF(pt.x() - 3, pt.y() - 9, 6, 18), 3, 3)


class CropDialog(Panel):
    def __init__(self, path, on_done):
        super().__init__("Rogner l'image")
        self.path = Path(path)
        self.on_done = on_done
        img = load_qimage(path)
        if img.isNull():
            raise RuntimeError("Impossible d'ouvrir l'image")
        self.view = CropView(img)
        self.body.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Ratio", objectName="muted"))
        self.group = QButtonGroup(self)
        for i, (name, val) in enumerate(RATIOS):
            b = QPushButton(name, checkable=True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, v=val: self.view.set_ratio(v))
            self.group.addButton(b)
            row.addWidget(b)
        row.addStretch()
        self.body.addLayout(row)

        row2 = QHBoxLayout()
        self.w = QSpinBox(maximum=img.width(), minimum=10, suffix=" px")
        self.h = QSpinBox(maximum=img.height(), minimum=10, suffix=" px")
        row2.addWidget(QLabel("L"))
        row2.addWidget(self.w)
        row2.addWidget(QLabel("H"))
        row2.addWidget(self.h)
        row2.addStretch()
        reset = QPushButton("Réinitialiser", clicked=self.reset)
        apply = QPushButton("Appliquer", objectName="primary", clicked=self.accept_action)
        row2.addWidget(reset)
        row2.addWidget(apply)
        self.body.addLayout(row2)

        self.view.changed.connect(self._sync)
        self.w.editingFinished.connect(self._edit_size)
        self.h.editingFinished.connect(self._edit_size)
        self._sync()

    def _sync(self):
        for sb, v in ((self.w, self.view.sel.width()), (self.h, self.view.sel.height())):
            sb.blockSignals(True)
            sb.setValue(round(v))
            sb.blockSignals(False)

    def _edit_size(self):
        w, h = self.w.value(), self.h.value()
        if self.view.ratio:
            h = w / self.view.ratio if self.sender() is self.w else h
            w = h * self.view.ratio
        self.view.set_size(w, h)
        self._sync()

    def reset(self):
        self.group.buttons()[0].setChecked(True)
        self.view.ratio = None
        self.view.sel = QRectF(self.view.full)
        self.view.update()
        self._sync()

    def accept_action(self):
        r = self.view.sel.toAlignedRect().intersected(self.view.img.rect())
        e = actions.ext_of(self.path)
        ext = e if e in ("png", "jpg", "jpeg", "bmp", "webp", "tif", "tiff") else "png"
        out = actions.out_path(self.path, " Cropped", ext)
        ok = self.view.img.copy(r).save(str(out), quality=95)
        self.accept()
        self.on_done([out] if ok else [], None if ok else "Échec de l'enregistrement")


# ======================================================================= Fond

GRADIENTS = [
    [(255, 196, 92), (255, 94, 98), (176, 107, 255)],   # sunset
    [(255, 236, 160), (255, 154, 139), (255, 106, 136)],
    [(132, 250, 176), (143, 211, 244)],
    [(161, 196, 253), (194, 233, 251)],
    [(102, 126, 234), (118, 75, 162)],
    [(250, 208, 196), (255, 209, 255)],
    [(15, 32, 39), (44, 83, 100)],
    [(252, 92, 125), (106, 130, 251)],
]
SOLIDS = [(255, 255, 255), (241, 239, 236), (255, 214, 102), (110, 207, 120), (56, 178, 172),
          (59, 130, 246), (30, 41, 59), (0, 0, 0)]


def make_background(spec, w, h):
    kind, val = spec
    if kind == "none":
        return Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if kind == "solid":
        return Image.new("RGBA", (w, h), (*val, 255))
    stops = np.array(val, dtype=np.float32)
    # dégradé diagonal + léger halo pour un rendu "mesh"
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (xx / max(w - 1, 1) * 0.6 + yy / max(h - 1, 1) * 0.4)
    pos = np.linspace(0, 1, len(stops))
    rgb = np.stack([np.interp(t, pos, stops[:, c]) for c in range(3)], -1)
    d = np.sqrt(((xx - w * 0.8) / w) ** 2 + ((yy - h * 0.15) / h) ** 2)
    glow = np.clip(1 - d / 0.6, 0, 1)[..., None] ** 2 * 0.35
    rgb = rgb * (1 - glow) + 255 * glow
    a = np.full((h, w, 1), 255, np.float32)
    return Image.fromarray(np.concatenate([rgb, a], -1).astype(np.uint8), "RGBA")


def compose(img, spec, pad, radius, shadow, ratio):
    w, h = img.size
    cw, ch = w + 2 * pad, h + 2 * pad
    if ratio:
        if cw / ch < ratio:
            cw = round(ch * ratio)
        else:
            ch = round(cw / ratio)
    canvas = make_background(spec, cw, ch)
    x, y = (cw - w) // 2, (ch - h) // 2
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    if shadow > 0:
        blur = max(1, int(min(w, h) * 0.05 * shadow / 100))
        sh = Image.new("L", (cw, ch), 0)
        off = int(blur * 0.4)
        ImageDraw.Draw(sh).rounded_rectangle((x, y + off, x + w, y + h + off), radius=radius,
                                             fill=int(150 * shadow / 100))
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
        black = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
        black.putalpha(sh)
        canvas = Image.alpha_composite(canvas, black)
    img = img.convert("RGBA")
    alpha = Image.fromarray(np.minimum(np.array(img.getchannel("A")), np.array(mask)))
    img.putalpha(alpha)
    canvas.alpha_composite(img, (x, y))
    return canvas


def pil_to_qpixmap(im):
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(q)


def _swatch_css(spec):
    kind, val = spec
    if kind == "solid":
        return f"background: rgb{tuple(val)};"
    if kind == "none":
        return ("background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 white, stop:0.45 white, "
                "stop:0.5 #e33, stop:0.55 white, stop:1 white);")
    stops = ", ".join(f"stop:{i / (len(val) - 1):.2f} rgb{tuple(c)}" for i, c in enumerate(val))
    return f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, {stops});"


class AddBgDialog(Panel):
    def __init__(self, path, on_done):
        super().__init__("Ajouter un fond")
        self.path = Path(path)
        self.on_done = on_done
        q = load_qimage(path)
        if q.isNull():
            raise RuntimeError("Impossible d'ouvrir l'image")
        q = q.convertToFormat(QImage.Format_RGBA8888)
        self.src = Image.frombuffer("RGBA", (q.width(), q.height()), bytes(q.constBits()), "raw", "RGBA",
                                    q.bytesPerLine(), 1).copy()
        self.small = self.src.copy()
        self.small.thumbnail((560, 380), Image.LANCZOS)
        self.scale = self.small.width / self.src.width
        self.spec = ("grad", GRADIENTS[0])
        self.ratio = None

        self.preview = QLabel(alignment=Qt.AlignCenter)
        self.preview.setMinimumSize(600, 420)
        self.body.addWidget(self.preview, 1)

        grid = QGridLayout()
        grid.setSpacing(6)
        specs = [("grad", g) for g in GRADIENTS] + [("solid", s) for s in SOLIDS] + [("none", None)]
        self.sw_group = QButtonGroup(self)
        for i, spec in enumerate(specs):
            b = QPushButton(checkable=True)
            b.setFixedSize(26, 26)
            b.setStyleSheet(f"QPushButton {{ {_swatch_css(spec)} border-radius: 7px; "
                            f"border: 2px solid rgba(255,255,255,200); }}"
                            f"QPushButton:checked {{ border: 2px solid {ACCENT}; }}")
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, s=spec: self._set_spec(s))
            self.sw_group.addButton(b)
            grid.addWidget(b, i // 9, i % 9)
        pick = QPushButton("…", clicked=self._pick)
        pick.setFixedSize(26, 26)
        grid.addWidget(pick, 1, 8)
        bgrow = QHBoxLayout()
        bgrow.addWidget(QLabel("Fond", objectName="muted"))
        bgrow.addLayout(grid)
        bgrow.addStretch()
        self.body.addLayout(bgrow)

        m = max(self.src.size)
        self.sl = {}
        for key, label, mx, val in (("pad", "Marge", m // 2, int(m * 0.08)),
                                    ("rad", "Coins", min(self.src.size) // 4, int(m * 0.025)),
                                    ("shadow", "Ombre", 100, 45)):
            row = QHBoxLayout()
            lab = QLabel(label, objectName="muted")
            lab.setFixedWidth(48)
            s = QSlider(Qt.Horizontal, maximum=mx, value=val)
            v = QLabel()
            v.setFixedWidth(60)
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            s.valueChanged.connect(lambda x, v=v, k=key: (v.setText(f"{x} {'%' if k == 'shadow' else 'px'}"),
                                                          self.refresh()))
            v.setText(f"{val} {'%' if key == 'shadow' else 'px'}")
            row.addWidget(lab)
            row.addWidget(s, 1)
            row.addWidget(v)
            self.body.addLayout(row)
            self.sl[key] = s

        row = QHBoxLayout()
        row.addWidget(QLabel("Ratio", objectName="muted"))
        self.rgroup = QButtonGroup(self)
        for i, (name, val) in enumerate([("Auto", None), ("1:1", 1.0), ("16:9", 16 / 9), ("9:16", 9 / 16),
                                         ("4:5", 4 / 5)]):
            b = QPushButton(name, checkable=True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, v=val: (setattr(self, "ratio", v), self.refresh()))
            self.rgroup.addButton(b)
            row.addWidget(b)
        row.addStretch()
        row.addWidget(QPushButton("Enregistrer avec le fond", objectName="primary", clicked=self.accept_action))
        self.body.addLayout(row)
        self.refresh()

    def _set_spec(self, s):
        self.spec = s
        self.refresh()

    def _pick(self):
        c = QColorDialog.getColor(QColor(255, 255, 255), self)
        if c.isValid():
            for b in self.sw_group.buttons():
                b.setChecked(False)
            self._set_spec(("solid", (c.red(), c.green(), c.blue())))

    def _params(self, k):
        return (int(self.sl["pad"].value() * k), int(self.sl["rad"].value() * k), self.sl["shadow"].value())

    def refresh(self):
        pad, rad, sh = self._params(self.scale)
        im = compose(self.small, self.spec, pad, rad, sh, self.ratio)
        pm = pil_to_qpixmap(im)
        self.preview.setPixmap(pm.scaled(self.preview.minimumSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def accept_action(self):
        pad, rad, sh = self._params(1)
        out = actions.out_path(self.path, " With Background", "png")
        try:
            compose(self.src, self.spec, pad, rad, sh, self.ratio).save(out)
            err = None
        except Exception as e:  # noqa
            err = str(e)
        self.accept()
        self.on_done([] if err else [out], err)


# ======================================================================= Choix PDF

class PdfChoiceDialog(Panel):
    """Plusieurs images -> PDF : un PDF par image, ou toutes les images dans un seul PDF."""

    def __init__(self, n, on_choice):
        super().__init__(f"Convertir {n} images en PDF")
        self.on_choice = on_choice
        info = QLabel("Comment veux-tu les regrouper ?", objectName="muted", alignment=Qt.AlignCenter)
        self.body.addWidget(info)
        row = QHBoxLayout()
        row.setSpacing(12)
        for label, sub, single in ((f"Un seul PDF", f"{n} pages, une image par page", True),
                                   (f"Un PDF par image", f"{n} fichiers PDF séparés", False)):
            b = QPushButton(f"{label}\n{sub}", objectName="primary" if single else "")
            b.setMinimumSize(210, 64)
            b.clicked.connect(lambda _=False, s=single: self._choose(s))
            row.addWidget(b)
            if single:
                b.setDefault(True)
                b.setFocus()
        self.body.addLayout(row)

    def _choose(self, single):
        self.accept()
        self.on_choice(single)

    def accept_action(self):
        self._choose(True)
