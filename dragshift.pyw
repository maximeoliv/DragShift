"""DragShift — convertis un fichier en le glissant puis en appuyant sur Shift.

Pendant un glisser-déposer (Explorateur, Bureau…), appuie sur Shift : un menu radial
apparaît sous le curseur. Lâche le fichier sur un format pour le convertir à côté de
l'original. Réappuie sur Shift (ou survole le centre) pour basculer « Formats » / « Outils ».
Le même menu s'ouvre aussi au clic droit sur un fichier (entrée « DragShift »).
"""
import ctypes
from ctypes import wintypes
import math
import os
import subprocess
import sys
import time
import traceback
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QElapsedTimer, QEvent, QObject, QPointF, QRectF,
                            QRunnable, Qt, QThreadPool, QTimer, QUrl, QVariantAnimation, Signal)
from PySide6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient, QLinearGradient)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

import actions
import shellmenu

APP = "DragShift"
REPO_URL = "https://github.com/maximeoliv/DragShift"  # ouverte par « Comment ça marche ? »
HERE = Path(__file__).resolve().parent
user32 = ctypes.windll.user32
VK_SHIFT = 0x10
EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND = 0x000A, 0x000B
WINEVENT_OUTOFCONTEXT = 0
GUI_INMOVESIZE = 0x2
OLE_DRAG_CLASS = "CLIPBRDWNDCLASS"
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.SetWinEventHook.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HMODULE, WINEVENTPROC,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.UINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# ============================================================ menu radial

class Radial(QWidget):
    R_OUT, R_IN, R_CENTER, GAP = 165, 64, 54, 6
    SIDE = 440

    def __init__(self, app, interactive=False):
        """interactive=False : cible de drop pendant un drag (ne prend jamais le focus).
        interactive=True : menu cliquable ouvert depuis le clic droit de l'Explorateur."""
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        super().__init__(None, flags if interactive else flags | Qt.WindowDoesNotAcceptFocus)
        self.app = app
        self.interactive = interactive
        self.setAttribute(Qt.WA_TranslucentBackground)
        if interactive:
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.StrongFocus)
        else:
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setAcceptDrops(True)
        self.setFixedSize(self.SIDE, self.SIDE)
        self.files, self.segments, self.mode, self.hover, self.tools = [], [], "formats", None, []
        self.status = "Bouge le fichier…"
        self.center_timer = QElapsedTimer()
        self.center_armed = True
        fams = QFontDatabase.families()
        self.icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        self.anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=170,
                                      easingCurve=QEasingCurve.OutBack)
        self.anim.valueChanged.connect(lambda _: self.update())
        self.swap = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=180,
                                      easingCurve=QEasingCurve.OutCubic)
        self.swap.valueChanged.connect(lambda _: self.update())
        self.dwell = QTimer(self, interval=40, timeout=self._check_dwell)

    # ---------------------------------------------------------- cycle de vie
    def popup(self, paths=None):
        self.files, self.segments, self.mode, self.hover, self.tools = [], [], "formats", None, []
        self.status = "Bouge le fichier…"
        if paths:
            self._load(paths)
        self.center_armed = False  # le menu s'ouvre sous le curseur : pas de bascule tant qu'on n'est pas sorti du centre
        pos = QCursor.pos()
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = min(max(pos.x() - self.SIDE // 2, g.left()), g.right() - self.SIDE)
        y = min(max(pos.y() - self.SIDE // 2, g.top()), g.bottom() - self.SIDE)
        self.move(x, y)
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.start()
        if self.interactive:
            self.activateWindow()
            import dialogs
            dialogs.force_foreground(int(self.winId()))
            self.setFocus()
            self._set_hover(self._locate(self.mapFromGlobal(QCursor.pos()).toPointF()))
        else:
            self.dwell.start()

    def dismiss(self):
        self.dwell.stop()
        self.hide()

    # ---------------------------------------------------------- données
    def _load(self, paths):
        if paths and paths == self.files:
            return  # nouveau DragEnter pour les mêmes fichiers : on garde le mode courant
        self.files = paths
        if not paths:
            self.status = "Aucun fichier local"
            self.segments = []
            return
        if actions.category(paths[0]) is None:
            self.status = f".{actions.ext_of(paths[0])} non géré"
            self.segments = []
            return
        self.formats = [("convert", f, f.upper(), None) for f in actions.formats_for(paths[0])]
        self.tools = [("tool", t, actions.TOOL_LABELS[t], actions.ICONS.get(t)) for t in actions.tools_for(paths)]
        self.mode = "formats"
        self.segments = self.formats
        size = sum(Path(p).stat().st_size for p in paths if Path(p).is_file())
        self.status = actions.human(size)

    def _toggle_mode(self):
        if not self.files or not self.tools:
            return
        self.mode = "tools" if self.mode == "formats" else "formats"
        self.segments = self.tools if self.mode == "tools" else self.formats
        self.hover = None
        self.swap.stop()
        self.swap.start()

    # ---------------------------------------------------------- géométrie
    @property
    def c(self):
        return QPointF(self.SIDE / 2, self.SIDE / 2)

    def _locate(self, pos):
        dx, dy = pos.x() - self.c.x(), pos.y() - self.c.y()
        d = math.hypot(dx, dy)
        if d < self.R_CENTER + 4:
            return "center"
        if d > self.R_OUT + 45 or d < self.R_IN - 4 or not self.segments:
            return None
        step = 360 / len(self.segments)
        a = math.degrees(math.atan2(dx, -dy)) % 360
        return int(((a + step / 2) % 360) // step)

    def _check_dwell(self):
        if self.hover == "center" and self.center_armed and self.center_timer.isValid() \
                and self.center_timer.elapsed() > 420:
            self.center_armed = False
            self._toggle_mode()
            self.update()

    def _set_hover(self, h):
        if h == "center" and self.hover != "center":
            self.center_timer.start()
        if h != "center":
            self.center_armed = True
        if h != self.hover:
            self.hover = h
            self.update()

    # ---------------------------------------------------------- drag & drop
    # IMPORTANT : on renvoie toujours CopyAction. Shift pendant un drag = "déplacer" pour
    # l'Explorateur ; renvoyer Move lui ferait supprimer le fichier source.
    def _accept(self, e):
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragEnterEvent(self, e):
        md = e.mimeData()
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        self._load([p for p in paths if os.path.isfile(p)])
        self._accept(e)
        self._set_hover(self._locate(e.position()))
        self.update()

    def dragMoveEvent(self, e):
        self._accept(e)
        self._set_hover(self._locate(e.position()))

    def dragLeaveEvent(self, e):
        self._set_hover(None)

    def dropEvent(self, e):
        self._accept(e)
        self._run(self._locate(e.position()))

    def _run(self, h):
        files = list(self.files)
        seg = self.segments[h] if isinstance(h, int) and h < len(self.segments) else None
        if seg and files and self.interactive:
            # on lance l'action AVANT de fermer le menu : une fenêtre (Rogner, Fond) hérite
            # ainsi du premier plan, sinon Windows la laisse derrière l'Explorateur
            kind, key, label, _ = seg
            self.app.execute(kind, key, label, files)
            self.dismiss()
            return
        self.dismiss()
        if seg and files:
            kind, key, label, _ = seg
            QTimer.singleShot(0, lambda: self.app.execute(kind, key, label, files))

    # ---------------------------------------------------------- mode clic (menu clic droit)
    def mouseMoveEvent(self, e):
        self._set_hover(self._locate(e.position()))

    def mousePressEvent(self, e):
        h = self._locate(e.position())
        if e.button() != Qt.LeftButton or h is None:
            self.dismiss()
        elif h == "center":
            self._toggle_mode()
            self.update()
        else:
            self._run(h)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.dismiss()
        elif e.key() in (Qt.Key_Shift, Qt.Key_Tab, Qt.Key_Space):
            self._toggle_mode()
            self.update()

    def changeEvent(self, e):
        # clic ailleurs (la fenêtre perd le focus) = fermeture, comme un menu contextuel
        if self.interactive and e.type() == QEvent.ActivationChange and self.isVisible() \
                and not self.isActiveWindow():
            QTimer.singleShot(0, self.dismiss)
        super().changeEvent(e)

    # ---------------------------------------------------------- dessin
    def _wedge(self, a1, a2, r_in, r_out, gap):
        """Secteur entre a1 et a2 (degrés, sens horaire depuis midi), espacement constant `gap` px."""
        def pt(r, a):
            t = math.radians(a)
            return QPointF(self.c.x() + r * math.sin(t), self.c.y() - r * math.cos(t))

        do = math.degrees(math.asin(min(1, gap / 2 / r_out)))
        di = math.degrees(math.asin(min(1, gap / 2 / r_in)))
        o1, o2, i1, i2 = a1 + do, a2 - do, a1 + di, a2 - di
        ro = QRectF(self.c.x() - r_out, self.c.y() - r_out, 2 * r_out, 2 * r_out)
        ri = QRectF(self.c.x() - r_in, self.c.y() - r_in, 2 * r_in, 2 * r_in)
        p = QPainterPath(pt(r_out, o1))
        p.arcTo(ro, 90 - o1, -(o2 - o1))
        p.lineTo(pt(r_in, i2))
        p.arcTo(ri, 90 - i2, i2 - i1)
        p.closeSubpath()
        return p

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        k = self.anim.currentValue() if self.anim.state() == QAbstractAnimation.Running else 1.0
        p.setOpacity(min(1.0, max(0.0, k)))
        p.translate(self.c)
        p.scale(0.75 + 0.25 * k, 0.75 + 0.25 * k)
        p.translate(-self.c)
        c, RO, RI, RC = self.c, self.R_OUT, self.R_IN, self.R_CENTER

        # ombre portée + disque givré
        for i in range(8):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 20, 70, 10))
            p.drawEllipse(c + QPointF(0, 6), RO + 14 + i * 2.2, RO + 14 + i * 2.2)
        g = QRadialGradient(c, RO + 14)
        g.setColorAt(0, QColor(244, 245, 255, 155))
        g.setColorAt(1, QColor(226, 229, 255, 195))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.2))
        p.drawEllipse(c, RO + 12, RO + 12)

        # secteurs
        n = len(self.segments)
        s = self.swap.currentValue() if self.swap.state() == QAbstractAnimation.Running else 1.0
        rot = (1 - s) * -25
        f_label = QFont("Segoe UI Variable Display", 10, QFont.DemiBold)
        f_label.setLetterSpacing(QFont.PercentageSpacing, 104)
        f_icon = QFont(self.icon_family or "Segoe UI", 15)
        for i, (kind, key, label, icon) in enumerate(self.segments):
            step = 360 / n
            a1, a2 = i * step - step / 2 + rot, i * step + step / 2 + rot
            hovered = self.hover == i
            r_out = RO + (8 if hovered else 0)
            path = self._wedge(a1, a2, RI, r_out, self.GAP)
            if hovered:
                gr = QLinearGradient(c, c + QPointF(math.sin(math.radians(i * step)) * RO,
                                                    -math.cos(math.radians(i * step)) * RO))
                gr.setColorAt(0, QColor(129, 120, 255))
                gr.setColorAt(1, QColor(79, 70, 229))
                p.setBrush(gr)
                p.setPen(QPen(QColor(255, 255, 255, 230), 1.4))
            else:
                p.setBrush(QColor(250, 250, 255, int(218 * s)))
                p.setPen(QPen(QColor(255, 255, 255, 220), 1))
            p.drawPath(path)
            mid = math.radians(i * step + rot)
            rm = (RI + RO) / 2 + 4
            pc = QPointF(c.x() + rm * math.sin(mid), c.y() - rm * math.cos(mid))
            col = QColor(255, 255, 255) if hovered else QColor(49, 46, 110)
            col.setAlphaF(s)
            p.setPen(col)
            if icon and self.icon_family:
                p.setFont(f_icon)
                p.drawText(QRectF(pc.x() - 30, pc.y() - 24, 60, 24), Qt.AlignCenter, icon)
                p.setFont(QFont("Segoe UI Variable Display", 8, QFont.DemiBold))
                p.drawText(QRectF(pc.x() - 50, pc.y() + 1, 100, 16), Qt.AlignCenter, label)
            else:
                p.setFont(f_label)
                p.drawText(QRectF(pc.x() - 50, pc.y() - 10, 100, 20), Qt.AlignCenter, label)

        if not n:  # anneau vide en attendant le DragEnter
            p.setBrush(QColor(250, 250, 255, 120))
            p.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.DashLine))
            p.drawEllipse(c, (RO + RI) / 2 + 30, (RO + RI) / 2 + 30)

        # centre
        center_hot = self.hover == "center"
        g = QRadialGradient(c - QPointF(0, RC * 0.4), RC * 1.4)
        g.setColorAt(0, QColor(150, 142, 255) if not center_hot else QColor(124, 112, 255))
        g.setColorAt(1, QColor(99, 90, 240) if not center_hot else QColor(67, 56, 202))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.6))
        p.drawEllipse(c, RC, RC)

        if isinstance(self.hover, int) and self.hover < n:
            kind, key, label, _ = self.segments[self.hover]
            title = f"→ {label}" if kind == "convert" else label.capitalize()
            sub = self.status
        else:
            title = self.status
            sub = ""
            if self.files:
                sub = f"{len(self.files)} fichiers" if len(self.files) > 1 else \
                    f".{actions.ext_of(self.files[0])}"
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI Variable Display", 12 if len(title) < 10 else 9, QFont.Bold))
        if sub:
            p.drawText(QRectF(c.x() - RC, c.y() - 20, 2 * RC, 22), Qt.AlignCenter, title)
        else:
            p.drawText(QRectF(c.x() - RC + 8, c.y() - 22, 2 * RC - 16, 44), Qt.AlignCenter | Qt.TextWordWrap,
                       title)
        p.setFont(QFont("Segoe UI Variable Text", 8))
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(QRectF(c.x() - RC, c.y() + 2, 2 * RC, 16), Qt.AlignCenter, sub)
        if self.files and self.tools:
            # indice visible : comment passer à l'autre anneau (c'est là que sont Rogner, Fond…)
            other = "Outils" if self.mode == "formats" else "Formats"
            hint = f"clic : {other}" if self.interactive else f"⇧ {other}"
            p.setFont(QFont("Segoe UI Variable Text", 7, QFont.DemiBold))
            w = p.fontMetrics().horizontalAdvance(hint) + 14
            pill = QRectF(c.x() - w / 2, c.y() + 20, w, 15)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 70))
            p.drawRoundedRect(pill, 7.5, 7.5)
            p.setPen(QColor(255, 255, 255))
            p.drawText(pill, Qt.AlignCenter, hint)


# ============================================================ détecteur de drag

class DragWatcher(QObject):
    """Sonde l'état de la souris/Shift : bouton gauche tenu + déplacement + Shift appuyé
    après le début du clic => un glisser-déposer est en cours, on ouvre le menu."""

    def __init__(self, radial):
        super().__init__()
        self.radial = radial
        self.enabled = True
        self.prev = self.prev_sh = False
        self.moving = False  # une fenêtre est en cours de déplacement/redimensionnement
        # Même signal que FancyZones : Windows notifie le début/la fin d'un déplacement de fenêtre.
        # Pendant un déplacement (Shift = zones FancyZones), on ne doit surtout pas ouvrir le menu.
        self._hook_cb = WINEVENTPROC(self._on_winevent)
        self._hook = user32.SetWinEventHook(EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND, None,
                                            self._hook_cb, 0, 0, WINEVENT_OUTOFCONTEXT)
        self.timer = QTimer(self, interval=20, timeout=self.tick)
        self.timer.start()

    def _on_winevent(self, hook, event, hwnd, obj, child, thread, time_ms):
        self.moving = event == EVENT_SYSTEM_MOVESIZESTART
        if self.moving and self.radial.isVisible():
            self.radial.dismiss()

    @staticmethod
    def _thread_state(tid):
        """(flags, classe de la fenêtre qui a la capture souris) pour un thread GUI."""
        g = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(g)):
            return 0, ""
        buf = ctypes.create_unicode_buffer(128)
        if g.hwndCapture:
            user32.GetClassNameW(g.hwndCapture, buf, 128)
        return g.flags, buf.value

    def _is_file_drag(self):
        """Vrai glisser-déposer OLE en cours ? Pendant DoDragDrop, la souris est capturée par la
        fenêtre interne d'OLE (classe CLIPBRDWNDCLASS) dans le thread source. Un déplacement de
        fenêtre (FancyZones), une sélection de texte ou un jeu n'ont pas cette capture."""
        hwnd = user32.WindowFromPoint(self.start_phys)
        states = [self._thread_state(user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0),
                  self._thread_state(0)]  # 0 = thread au premier plan
        ole = any(cap == OLE_DRAG_CLASS for _, cap in states)
        in_move = self.moving or any(f & GUI_INMOVESIZE for f, _ in states)
        print(f"shift pendant drag : ole={ole} move={in_move} {states}", file=sys.stderr, flush=True)
        return ole and not in_move

    def tick(self):
        btn = 0x02 if user32.GetSystemMetrics(23) else 0x01  # boutons inversés ?
        lb, sh = key_down(btn), key_down(VK_SHIFT)
        pos = QCursor.pos()
        if lb and not self.prev:
            self.start, self.moved, self.shown = pos, False, False
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self.start_phys = pt
            self.shift_blocked = sh  # Shift déjà tenu au clic = sélection, pas notre geste
        if lb:
            if not self.moved and (pos - self.start).manhattanLength() > 12:
                self.moved = True
            if not sh:
                self.shift_blocked = False
            if self.enabled and self.moved and sh and not self.shift_blocked and not self.shown:
                if self._is_file_drag():
                    self.shown = True
                    self.radial.popup()
                else:
                    self.shift_blocked = True  # on réessaiera au prochain appui sur Shift
            elif self.shown and sh and not self.prev_sh and self.radial.isVisible():
                self.radial._toggle_mode()  # 2e appui sur Shift = Formats <-> Outils
                self.radial.update()
        elif self.prev and self.radial.isVisible():
            QTimer.singleShot(300, self.radial.dismiss)  # laisse le temps au drop d'arriver
        self.prev, self.prev_sh = lb, sh


# ============================================================ exécution

class Job(QRunnable):
    class Sig(QObject):
        done = Signal(str, list, str)

    def __init__(self, label, fn):
        super().__init__()
        self.label, self.fn, self.sig = label, fn, Job.Sig()

    def run(self):
        try:
            outs = self.fn()
            self.sig.done.emit(self.label, [str(o) for o in outs], "")
        except Exception as e:  # noqa
            traceback.print_exc()
            self.sig.done.emit(self.label, [], str(e))


def draw_icon(size):
    """Icône : disque dégradé orange avec l'anneau segmenté du menu radial."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 64
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(129, 140, 248))
    g.setColorAt(1, QColor(109, 40, 217))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(1 * k, 1 * k, 62 * k, 62 * k))
    c = QPointF(32 * k, 32 * k)
    n, r_out, r_in, gap = 6, 25 * k, 13 * k, 3.2 * k
    for i in range(n):
        a1, a2 = i * 60 - 30, i * 60 + 30
        do, di = math.degrees(math.asin(gap / 2 / r_out)), math.degrees(math.asin(gap / 2 / r_in))
        path = QPainterPath(QPointF(c.x() + r_out * math.sin(math.radians(a1 + do)),
                                    c.y() - r_out * math.cos(math.radians(a1 + do))))
        path.arcTo(QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out), 90 - (a1 + do), -(60 - 2 * do))
        path.lineTo(QPointF(c.x() + r_in * math.sin(math.radians(a2 - di)),
                            c.y() - r_in * math.cos(math.radians(a2 - di))))
        path.arcTo(QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in), 90 - (a2 - di), 60 - 2 * di)
        path.closeSubpath()
        p.setBrush(QColor(255, 255, 255, 255 if i == 1 else 150))
        p.drawPath(path)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(c, 8 * k, 8 * k)
    p.end()
    return pm


class App(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(3)
        self.jobs = []
        self.running = 0
        self.last_out = None
        self.dialogs = []
        self.radial = Radial(self)
        self.click_radial = Radial(self, interactive=True)
        self.watcher = DragWatcher(self.radial)
        self._tray()
        self._ipc()
        QTimer.singleShot(1500, lambda: __import__("dialogs"))  # précharge numpy/PIL : Rogner s'ouvre tout de suite

    # ---------------------------------------------------------- requêtes du clic droit (send.pyw)
    def _ipc(self):
        """send.pyw (lancé par l'Explorateur, une fois par fichier sélectionné) envoie
        « action<TAB>chemin » sur un pipe nommé. On regroupe ce qui arrive en ~0,4 s."""
        self.pending = {}
        self.flush_timer = QTimer(self, singleShot=True, interval=400, timeout=self._flush)
        self.server = QLocalServer(self)
        self.server.listen(shellmenu.PIPE_NAME)
        self.server.newConnection.connect(self._on_conn)

    def _on_conn(self):
        # L'Explorateur lance un send.pyw par fichier, quasi simultanément : un client a souvent déjà
        # écrit ET fermé quand on traite sa connexion (le signal disconnected ne viendra alors jamais).
        # On lit donc tout de suite ce qui est disponible, ligne par ligne.
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))
                while b"\n" in buf:
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf[:] = rest
                    action, _, path = line.decode("utf-8", "replace").partition("\t")
                    if action == "!flush":  # sélection complète (menu moderne) : pas besoin d'attendre
                        self.flush_timer.stop()
                        QTimer.singleShot(0, self._flush)
                        continue
                    if path and os.path.isfile(path):
                        lst = self.pending.setdefault(action, [])
                        if path not in lst:
                            lst.append(path)
                        self.flush_timer.start()

            sock.readyRead.connect(read)
            sock.disconnected.connect(lambda sock=sock, read=read: (read(), sock.deleteLater()))
            read()
            if sock.state() == QLocalSocket.UnconnectedState:
                sock.deleteLater()

    def _flush(self):
        pending, self.pending = self.pending, {}
        for action, paths in pending.items():
            print(f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} clic droit : {action} sur {len(paths)} fichier(s)", file=sys.stderr, flush=True)
            kind, _, key = action.partition(":")
            if kind == "menu":
                self.click_radial.popup(paths)
            elif kind == "convert":
                self.execute("convert", key, key.upper(), paths)
            elif kind == "tool" and key in actions.TOOL_LABELS:
                self.execute("tool", key, actions.TOOL_LABELS[key], paths)

    def _icon(self):
        icon = QIcon()
        for size in (16, 20, 24, 32, 48, 64):
            icon.addPixmap(draw_icon(size))
        return icon

    def _tray(self):
        self.tray = QSystemTrayIcon(self._icon(), self)
        self.tray.setToolTip(f"{APP} — glisse un fichier + Shift")
        m = QMenu()
        self.a_enabled = QAction("Activé", m, checkable=True, checked=True)
        self.a_enabled.toggled.connect(lambda v: setattr(self.watcher, "enabled", v))
        self.a_boot = QAction("Lancer au démarrage de Windows", m, checkable=True, checked=autostart_enabled())
        self.a_boot.toggled.connect(set_autostart)
        self.a_shell = QAction("Entrée « DragShift » au clic droit", m, checkable=True,
                               checked=shellmenu.is_installed())
        self.a_shell.toggled.connect(lambda v: shellmenu.install() if v else shellmenu.uninstall(modern=True))
        m.addAction(self.a_enabled)
        m.addAction(self.a_boot)
        m.addAction(self.a_shell)
        m.addSeparator()
        m.addAction("Comment ça marche ?", self.show_help)
        m.addSeparator()
        m.addAction("Quitter", self.qapp.quit)
        self.menu = m
        self.tray.setContextMenu(m)
        self.tray.messageClicked.connect(self._reveal)
        self.tray.show()

    def show_help(self):
        self.notify("DragShift — mode d'emploi",
                    "1. Glisse un fichier + Shift, lâche-le sur un format.\n"
                    "2. Réappuie sur Shift : outils (Rogner, Fond, Compresser…).\n"
                    "3. Ou clic droit sur un fichier > DragShift.")
        if REPO_URL:
            QDesktopServices.openUrl(QUrl(REPO_URL))
        elif (HERE / "README.md").exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(HERE / "README.md")))

    def _reveal(self):
        if self.last_out and Path(self.last_out).exists():
            subprocess.Popen(["explorer", "/select,", str(Path(self.last_out))])

    def notify(self, title, msg, err=False):
        self.tray.showMessage(title, msg, QSystemTrayIcon.Warning if err else QSystemTrayIcon.Information, 4000)

    def execute(self, kind, key, label, files):
        if kind == "tool" and key in actions.INTERACTIVE:
            import dialogs
            cls = dialogs.CropDialog if key == "crop" else dialogs.AddBgDialog
            try:
                d = cls(files[0], lambda outs, err: self.finished(label, [str(o) for o in outs], err or ""))
            except Exception as e:  # noqa
                self.notify(APP, str(e), True)
                return
            self.dialogs.append(d)
            d.finished.connect(lambda _: self.dialogs.remove(d))
            d.show_centered()
            return
        images = [f for f in files if actions.category(f) == "image"]
        if kind == "convert" and key == "pdf" and len(images) > 1:
            # plusieurs images -> PDF : on demande « un seul PDF » ou « un PDF par image »
            import dialogs
            others = [f for f in files if f not in images]

            def chosen(single):
                if single:
                    self._start_job("PDF", lambda: [actions.images_to_pdf(images)], images)
                else:
                    self._start_job("PDF", lambda: actions.convert_many(images, "pdf"), images)
                if others:
                    self._start_job("PDF", lambda: actions.convert_many(others, "pdf"), others)

            d = dialogs.PdfChoiceDialog(len(images), chosen)
            self.dialogs.append(d)
            d.finished.connect(lambda _: self.dialogs.remove(d))
            d.show_centered()
            return
        if kind == "convert":
            fn = lambda: actions.convert_many(files, key)
        else:
            fn = lambda: actions.tool(files, key)
        self._start_job(label, fn, files, key)

    def _start_job(self, label, fn, files, key=""):
        job = Job(label, fn)
        job.setAutoDelete(False)
        job.sig.done.connect(lambda l, o, e, j=job: (self.jobs.remove(j), self.finished(l, o, e)))
        self.jobs.append(job)
        self.running += 1
        self.tray.setToolTip(f"{APP} — {self.running} tâche(s) en cours…")
        if key in ("transcribe", "docx", "rembg") or actions.category(files[0]) == "video":
            self.notify(APP, f"{label.capitalize()} en cours : {Path(files[0]).name}")
        self.pool.start(job)

    def finished(self, label, outs, err):
        if self.running:
            self.running -= 1
        self.tray.setToolTip(f"{APP} — " + (f"{self.running} tâche(s) en cours…" if self.running
                                            else "glisse un fichier + Shift"))
        if err:
            self.notify(f"{APP} — échec", err[:250], True)
            return
        if not outs:
            return
        self.last_out = outs[-1]
        names = ", ".join(Path(o).name for o in outs[:3]) + (f" (+{len(outs) - 3})" if len(outs) > 3 else "")
        size = actions.human(sum(Path(o).stat().st_size for o in outs if Path(o).exists()))
        self.notify(f"✓ {names}", f"{size} — clique pour afficher dans l'Explorateur")


# ============================================================ démarrage auto

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _cmdline():
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pyw}" "{Path(__file__).resolve()}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, APP)[0])
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, _cmdline())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


def main():
    if "--uninstall" in sys.argv:
        shellmenu.uninstall(modern=True)
        set_autostart(False)
        print("DragShift désinstallé (clic droit + démarrage auto). Le dossier peut être supprimé.")
        return
    if "--make-icon" in sys.argv:  # régénère assets/dragshift.png (converti en .ico par magick)
        qapp = QApplication(sys.argv)
        (HERE / "assets").mkdir(exist_ok=True)
        draw_icon(256).save(str(HERE / "assets" / "dragshift.png"))
        return
    install_status = None
    if "--install" in sys.argv:
        install_status = shellmenu.install()
        set_autostart(True)
        print(f"DragShift installé ({install_status}) + lancement au démarrage.")
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\DragShiftSingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # déjà lancé
        return
    if sys.stderr is None:  # pythonw : pas de console -> log fichier
        log = Path(os.environ["LOCALAPPDATA"], APP)
        log.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(log / "dragshift.log", "a", encoding="utf-8", buffering=1)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName(APP)
    app = App(qapp)  # noqa: F841
    first_run = Path(os.environ["LOCALAPPDATA"], APP, ".asked")
    if "--no-autostart-setup" not in sys.argv and not first_run.exists():
        app.a_boot.setChecked(True)   # déclenche set_autostart
        app.a_shell.setChecked(True)  # déclenche shellmenu.install
        first_run.parent.mkdir(exist_ok=True)
        first_run.touch()
    app.notify(APP, install_status.capitalize() + "." if install_status else
               "Prêt. Glisse un fichier + Shift, ou clic droit sur un fichier > Convertir en.")
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
