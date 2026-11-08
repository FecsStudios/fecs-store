"""Interfaz de Fecs Store (PyQt6).

Rendimiento: el listado es un QListView virtualizado (solo se pinta lo visible),
las consultas pesadas corren en hilos y los iconos se descargan en segundo plano.
"""
from __future__ import annotations

import hashlib
import math
import time

from PyQt6.QtCore import (QAbstractListModel, QEasingCurve, QModelIndex, QObject, QRectF, QSize, Qt,
                          QThread, QTimer, QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QComboBox, QDialog, QFrame,
                             QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow, QMessageBox,
                             QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton, QScrollArea,
                             QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout,
                             QWidget)

from . import updater
from .backend import Backend, Step
from .catalog import CATEGORIES, FEATURED, App, Catalog, Source

C = dict(bg="#0b0e14", side="#0f131b", card="#151a24", card_hover="#1b2231", border="#232b3a",
         text="#e8ecf4", muted="#8b95a9", accent="#7c6cff", accent2="#3ea6ff",
         ok="#3ddc97", warn="#ffb454", danger="#ff5d73")

AL = Qt.AlignmentFlag
FLAG_WRAP = AL.AlignLeft.value | AL.AlignTop.value | Qt.TextFlag.TextWordWrap.value
FLAG_LINE = AL.AlignLeft.value | AL.AlignVCenter.value
FLAG_CENTER = AL.AlignCenter.value

CHIP_COLORS = {"native": "#3ddc97", "aur": "#ffb454", "flatpak": "#3ea6ff",
               "web": "#c084fc", "download": "#8b95a9"}
CHIP_TEXT = {"flatpak": "Flatpak", "web": "Web", "download": "Descarga"}


def tile_colors(seed: str) -> tuple[QColor, QColor]:
    h = int(hashlib.md5(seed.encode()).hexdigest()[:6], 16)
    hue = h % 360
    return QColor.fromHsl(hue, 150, 95), QColor.fromHsl((hue + 40) % 360, 170, 70)


def paint_tile(p: QPainter, rect: QRectF, app: App):
    """Icono de reserva: degradado con emoji o inicial."""
    c1, c2 = tile_colors(app.name)
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    path = QPainterPath()
    path.addRoundedRect(rect, rect.width() * 0.24, rect.width() * 0.24)
    p.fillPath(path, g)
    f = QFont()
    f.setPixelSize(int(rect.width() * 0.46))
    f.setBold(not app.emoji)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(rect, FLAG_CENTER, app.emoji or (app.name[:1].upper() or "?"))


# --------------------------------------------------------------------------- #
#  Hilos de trabajo
# --------------------------------------------------------------------------- #
class IconProvider(QObject):
    ready = pyqtSignal()
    _got = pyqtSignal(str)

    def __init__(self, backend: Backend):
        super().__init__()
        self.store = backend.icons
        self._pm: dict[str, QPixmap] = {}
        self._appeared: dict[str, float] = {}
        self._last_appear = 0.0
        self.store.on_ready = lambda key: self._got.emit(key)
        self._got.connect(self._on_got)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.ready.emit)

    def _on_got(self, key: str):
        self._pm.pop(key, None)
        self._appeared[key] = self._last_appear = time.monotonic()
        self._timer.start(30)

    def fade(self, app: App) -> float:
        t = self._appeared.get(app.icon_key)
        return 1.0 if t is None else min(1.0, (time.monotonic() - t) / 0.35)

    def fading(self) -> bool:
        return time.monotonic() - self._last_appear < 0.4

    def pixmap(self, app: App):
        key = app.icon_key
        pm = self._pm.get(key)
        if pm is not None:
            return pm
        path = self.store.local(key)
        if path:
            img = QPixmap(path)
            if not img.isNull():
                pm = img.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
                self._pm[key] = pm
                return pm
        self.store.request(key, app.icon_urls)
        return None


class LoaderThread(QThread):
    done = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception:  # noqa: BLE001
            self.done.emit(None)


class UpdateWorker(QThread):
    pct = pyqtSignal(int)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self):
        try:
            updater.download(self.info, self.pct.emit)
            self.finished_ok.emit(True, "")
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(False, str(e))


class TaskWorker(QThread):
    line = pyqtSignal(str)
    pct = pyqtSignal(int)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, backend: Backend, steps: list[Step]):
        super().__init__()
        self.backend = backend
        self.steps = steps

    def run(self):
        ok, msg = self.backend.run_steps(self.steps, self.line.emit, self.pct.emit)
        try:
            self.backend.refresh_installed()
        except Exception:  # noqa: BLE001
            pass
        self.finished_ok.emit(ok, msg)


class TaskManager(QObject):
    """Cola secuencial de tareas (los gestores de paquetes no admiten paralelismo)."""
    changed = pyqtSignal()
    line = pyqtSignal(str)
    pct = pyqtSignal(int)
    task_done = pyqtSignal(str, bool, str)

    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend
        self.queue: list[tuple[str, list[Step]]] = []
        self.current: str = ""
        self.worker: TaskWorker | None = None
        self.log: list[str] = []

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def submit(self, title: str, steps: list[Step]):
        self.queue.append((title, steps))
        self.changed.emit()
        if not self.busy:
            self._next()

    def _next(self):
        if not self.queue:
            self.current = ""
            self.changed.emit()
            return
        title, steps = self.queue.pop(0)
        self.current = title
        self.log.append(f"\n=== {title} ===")
        self.line.emit(f"=== {title} ===")
        w = TaskWorker(self.backend, steps)
        w.line.connect(self._on_line)
        w.pct.connect(self.pct)
        w.finished_ok.connect(lambda ok, msg, t=title: self._on_done(t, ok, msg))
        self.worker = w
        self.changed.emit()
        w.start()

    def _on_line(self, text: str):
        self.log.append(text)
        if len(self.log) > 4000:
            del self.log[:1000]
        self.line.emit(text)

    def _on_done(self, title: str, ok: bool, msg: str):
        w = self.worker
        self.worker = None
        if w:
            w.wait()
            w.deleteLater()
        self.task_done.emit(title, ok, msg)
        self._next()

    def cancel(self):
        self.queue.clear()
        self.backend.cancel()


# --------------------------------------------------------------------------- #
#  Modelo, delegate y vista (virtualizados)
# --------------------------------------------------------------------------- #
def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


class AppModel(QAbstractListModel):
    """Cada fila agrupa ``cols`` apps: las tarjetas reparten SIEMPRE todo el ancho disponible."""

    def __init__(self):
        super().__init__()
        self.apps: list[App] = []
        self.cols = 4

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else -(-len(self.apps) // self.cols)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.UserRole:
            return index.row()
        return None

    def row_apps(self, row: int) -> list[App]:
        return self.apps[row * self.cols:(row + 1) * self.cols]

    def set_apps(self, apps: list[App]):
        self.beginResetModel()
        self.apps = apps
        self.endResetModel()

    def set_cols(self, cols: int):
        if cols != self.cols:
            self.beginResetModel()
            self.cols = cols
            self.endResetModel()


class Ctx:
    """Objetos compartidos entre widgets."""

    def __init__(self, backend: Backend, catalog: Catalog, icons: IconProvider):
        self.backend = backend
        self.catalog = catalog
        self.icons = icons


def chips_for(ctx: Ctx, app: App) -> list[tuple[str, str]]:
    out = []
    for s in ctx.backend.ordered_sources(app):
        if s.kind in ("native", "aur"):
            out.append((ctx.backend.distro.pm_label if s.kind == "native" else "AUR", s.kind))
        else:
            out.append((CHIP_TEXT.get(s.kind, s.kind), s.kind))
    return out


class CardDelegate(QStyledItemDelegate):
    CARD_H = 134

    def __init__(self, ctx: Ctx, view):
        super().__init__(view)
        self.ctx = ctx
        self.view = view

    def sizeHint(self, option, index):
        return QSize(max(self.view.viewport().width(), 200), self.CARD_H)

    def paint(self, p: QPainter, option, index):
        model = index.model()
        apps = model.row_apps(index.row())
        cols = model.cols
        cw = option.rect.width() / cols
        for i, app in enumerate(apps):
            cell = QRectF(option.rect.left() + i * cw, option.rect.top(), cw, self.CARD_H)
            self._paint_card(p, option.font, cell, app, index.row() * cols + i, index.row(), i)

    def _paint_card(self, p: QPainter, font, cell: QRectF, app: App, flat: int, row: int, col: int):
        v = self.view
        hp = ease_out(v.hover_amount(flat))
        ef = v.entry_factor(row, col)
        if ef <= 0.003:
            return
        pressed = v.press_flat == flat
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(ef)
        rect = cell.adjusted(7, 7, -7, -7).translated(0, (1 - ef) * 24 - 5 * hp)
        if pressed:
            rect = rect.adjusted(2, 2, -2, -2)

        if hp > 0.02:                                   # halo de luz al pasar el ratón
            for k in (3, 2, 1):
                glow = QColor(C["accent"])
                glow.setAlpha(int(20 * hp))
                gp = QPainterPath()
                gp.addRoundedRect(rect.adjusted(-k * 2, -k * 2, k * 2, k * 2), 14 + k * 2, 14 + k * 2)
                p.fillPath(gp, glow)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, lerp_color(QColor(C["card"]), QColor(C["card_hover"]), hp))
        if hp > 0.02:                                   # brillo superior
            sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            top = QColor(C["accent"])
            top.setAlpha(int(46 * hp))
            bot = QColor(C["accent"])
            bot.setAlpha(0)
            sheen.setColorAt(0, top)
            sheen.setColorAt(0.7, bot)
            p.fillPath(path, sheen)
        p.setPen(QPen(lerp_color(QColor(C["border"]), QColor(C["accent"]), hp), 1.0 + 0.4 * hp))
        p.drawPath(path)

        size = 58 + 5 * hp
        ir = QRectF(rect.left() + 14 - 2.5 * hp, rect.top() + 14 - 2.5 * hp, size, size)
        pm = self.ctx.icons.pixmap(app)
        fade = self.ctx.icons.fade(app)
        if pm:
            if fade < 1:
                paint_tile(p, ir, app)
            clip = QPainterPath()
            clip.addRoundedRect(ir, 14, 14)
            p.save()
            p.setClipPath(clip)
            p.setOpacity(ef * fade)
            p.drawPixmap(ir.toRect(), pm)
            p.restore()
        else:
            paint_tile(p, ir, app)

        x = rect.left() + 14 + 58 + 12
        w = rect.right() - x - 14
        f = QFont(font)
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        name = QFontMetrics(f).elidedText(app.name, Qt.TextElideMode.ElideRight, int(max(10, w - 24)))
        p.drawText(QRectF(x, rect.top() + 14, max(10, w - 24), 22), FLAG_LINE, name)

        f2 = QFont(font)
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        p.save()
        clip_rect = QRectF(x, rect.top() + 38, max(10, w), 34)
        p.setClipRect(clip_rect)
        p.drawText(clip_rect, FLAG_WRAP, app.desc)
        p.restore()

        cx = rect.left() + 14
        cy = rect.bottom() - 32
        f3 = QFont(font)
        f3.setPixelSize(11)
        f3.setBold(True)
        p.setFont(f3)
        fm = QFontMetrics(f3)
        for text, kind in chips_for(self.ctx, app):
            cwid = fm.horizontalAdvance(text) + 18
            if cx + cwid > rect.right() - 12:
                break
            col_ = QColor(CHIP_COLORS.get(kind, C["muted"]))
            bg = QColor(col_)
            bg.setAlpha(36 + int(30 * hp))
            cp = QPainterPath()
            cp.addRoundedRect(QRectF(cx, cy, cwid, 20), 10, 10)
            p.fillPath(cp, bg)
            p.setPen(col_)
            p.drawText(QRectF(cx, cy, cwid, 20), FLAG_CENTER, text)
            cx += cwid + 6

        if self.ctx.backend.app_installed(app):
            r = QRectF(rect.right() - 30, rect.top() + 12, 20, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["ok"]))
            p.drawEllipse(r)
            p.setPen(QColor("#04210f"))
            fb = QFont(font)
            fb.setPixelSize(12)
            fb.setBold(True)
            p.setFont(fb)
            p.drawText(r, FLAG_CENTER, "✓")
        p.restore()


class AppView(QListView):
    """Lista virtualizada con hover animado, entrada escalonada y scroll suave."""
    app_clicked = pyqtSignal(object)
    MIN_CARD = 250

    def __init__(self, ctx: Ctx, model: AppModel):
        super().__init__()
        self.ctx = ctx
        self.app_model = model
        self.setModel(model)
        self.delegate = CardDelegate(ctx, self)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(28)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("appView")
        self.hover_flat = -1
        self.press_flat = -1
        self.hp: dict[int, float] = {}
        self.entry_t0 = 0.0
        self.scroll_target = None
        self._last = time.monotonic()
        self.tick = QTimer(self)
        self.tick.setInterval(16)
        self.tick.timeout.connect(self._step)
        self.verticalScrollBar().sliderPressed.connect(lambda: setattr(self, "scroll_target", None))
        self.sync_cols()

    # ---- columnas: siempre llenan el ancho exacto ----
    def sync_cols(self):
        usable = self.width() - 14
        self.app_model.set_cols(max(1, int(usable // self.MIN_CARD)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sync_cols()

    def set_apps(self, apps: list[App], animate: bool = True):
        self.sync_cols()
        self.app_model.set_apps(apps)
        self.hover_flat = self.press_flat = -1
        self.hp.clear()
        self.scroll_target = None
        self.entry_t0 = time.monotonic() if animate else 0.0
        self.kick()

    # ---- animación ----
    def kick(self):
        if not self.tick.isActive():
            self._last = time.monotonic()
            self.tick.start()

    def hover_amount(self, flat: int) -> float:
        return self.hp.get(flat, 0.0)

    def _entry_len(self) -> float:
        rows = min(self.app_model.rowCount(), 12)
        return 0.38 + rows * 0.05 + self.app_model.cols * 0.035 + 0.05

    def entry_factor(self, row: int, col: int) -> float:
        if not self.entry_t0:
            return 1.0
        t = time.monotonic() - self.entry_t0 - (min(row, 14) * 0.05 + col * 0.035)
        return ease_out(t / 0.38)

    def _step(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        active = False
        if self.hover_flat >= 0:
            self.hp.setdefault(self.hover_flat, 0.0)
        for flat in list(self.hp):
            target = 1.0 if flat == self.hover_flat else 0.0
            v = self.hp[flat]
            v += (target - v) * min(1.0, dt * 14)
            if abs(target - v) < 0.01:
                v = target
            if v == 0.0:
                del self.hp[flat]
                continue
            self.hp[flat] = v
            if v != target:
                active = True
        if self.entry_t0:
            if now - self.entry_t0 > self._entry_len():
                self.entry_t0 = 0.0
            else:
                active = True
        if self.ctx.icons.fading():
            active = True
        if self.scroll_target is not None:
            bar = self.verticalScrollBar()
            cur = bar.value()
            diff = self.scroll_target - cur
            if abs(diff) < 1:
                bar.setValue(int(self.scroll_target))
                self.scroll_target = None
            else:
                step = diff * min(1.0, dt * 14)
                if abs(step) < 1:
                    step = 1 if diff > 0 else -1
                bar.setValue(int(cur + step))
                active = True
        self.viewport().update()
        if not active:
            self.tick.stop()

    # ---- ratón ----
    def cell_at(self, pos) -> int:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return -1
        rect = self.visualRect(idx)
        cols = self.app_model.cols
        col = int((pos.x() - rect.left()) / max(1.0, rect.width() / cols))
        col = min(max(col, 0), cols - 1)
        flat = idx.row() * cols + col
        return flat if flat < len(self.app_model.apps) else -1

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        f = self.cell_at(e.position().toPoint())
        if f != self.hover_flat:
            self.hover_flat = f
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if f >= 0
                                      else Qt.CursorShape.ArrowCursor)
            self.kick()

    def leaveEvent(self, e):
        self.hover_flat = -1
        self.kick()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press_flat = self.cell_at(e.position().toPoint())
            self.kick()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        was = self.press_flat
        self.press_flat = -1
        self.kick()
        if e.button() == Qt.MouseButton.LeftButton and was >= 0 \
                and self.cell_at(e.position().toPoint()) == was:
            self.app_clicked.emit(self.app_model.apps[was])
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if not e.pixelDelta().isNull():          # touchpad: ya es suave
            return super().wheelEvent(e)
        dy = e.angleDelta().y()
        if dy == 0:
            return super().wheelEvent(e)
        bar = self.verticalScrollBar()
        base = self.scroll_target if self.scroll_target is not None else bar.value()
        self.scroll_target = max(bar.minimum(), min(bar.maximum(), base - dy * 0.9))
        e.accept()
        self.kick()


# --------------------------------------------------------------------------- #
#  Banner destacado
# --------------------------------------------------------------------------- #
class Hero(QFrame):
    """Banner con fondo de luces animadas, transición de color y cambio con fundido."""
    open_app = pyqtSignal(object)

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("hero")
        self.setFixedHeight(176)
        self.apps = [a for a in (ctx.catalog.find(n) for n in FEATURED) if a]
        self.idx = 0
        self.prev_idx = 0
        self.mix = 1.0
        self.dot_rects: list[QRectF] = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        lay = QHBoxLayout(self.body)
        lay.setContentsMargins(28, 20, 28, 28)
        lay.setSpacing(22)
        self.icon = QLabel()
        self.icon.setFixedSize(96, 96)
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.kicker = QLabel("DESTACADA")
        self.kicker.setObjectName("heroKicker")
        self.title = QLabel()
        self.title.setObjectName("heroTitle")
        self.desc = QLabel()
        self.desc.setObjectName("heroDesc")
        self.desc.setWordWrap(True)
        self.btn = QPushButton("Ver opciones de instalación")
        self.btn.setObjectName("heroBtn")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(lambda: self.open_app.emit(self.apps[self.idx]))
        col.addWidget(self.kicker)
        col.addWidget(self.title)
        col.addWidget(self.desc)
        col.addStretch()
        row = QHBoxLayout()
        row.addWidget(self.btn)
        row.addStretch()
        col.addLayout(row)
        lay.addLayout(col, 1)
        outer.addWidget(self.body)
        self.eff = QGraphicsOpacityEffect(self.body)
        self.eff.setOpacity(1.0)
        self.body.setGraphicsEffect(self.eff)
        self.fade = QVariantAnimation(self)
        self.fade.setDuration(650)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.valueChanged.connect(self._on_fade)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next)
        self.timer.start(7000)
        self.anim = QTimer(self)               # luces de fondo (solo mientras es visible)
        self.anim.setInterval(33)
        self.anim.timeout.connect(self.update)
        self._show(animate=False)

    def showEvent(self, e):
        self.anim.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.anim.stop()
        super().hideEvent(e)

    def _on_fade(self, v):
        self.mix = float(v)
        self.eff.setOpacity(0.15 + 0.85 * self.mix)

    def next(self):
        if self.apps:
            self.goto((self.idx + 1) % len(self.apps))

    def goto(self, i: int):
        if not self.apps or i == self.idx:
            return
        self.prev_idx = self.idx
        self.idx = i
        self.timer.start(7000)
        self._show(animate=True)

    def _show(self, animate: bool = True):
        if not self.apps:
            return
        app = self.apps[self.idx]
        self.title.setText(app.name)
        self.desc.setText(app.desc)
        self.icon.setPixmap(icon_pixmap(self.ctx, app, 96))
        if animate:
            self.fade.stop()
            self.fade.start()
        self.update()

    def refresh_icon(self):
        if self.apps:
            self.icon.setPixmap(icon_pixmap(self.ctx, self.apps[self.idx], 96))

    def mousePressEvent(self, e):
        pt = e.position()
        for i, r in enumerate(self.dot_rects):
            if r.adjusted(-6, -8, 6, 8).contains(pt):
                self.goto(i)
                return
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 20, 20)
        if self.apps:
            new_c = tile_colors(self.apps[self.idx].name)[0]
            old_c = tile_colors(self.apps[self.prev_idx].name)[0]
            base = lerp_color(old_c, new_c, ease_out(self.mix))
        else:
            base = QColor(C["accent"])
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, base.darker(190))
        g.setColorAt(1, QColor(C["card"]))
        p.fillPath(path, g)
        p.save()
        p.setClipPath(path)
        t = time.monotonic()
        p.setPen(Qt.PenStyle.NoPen)
        tints = (base.lighter(160), QColor(C["accent2"]), QColor(C["accent"]))
        for i, tint in enumerate(tints):
            cx = w * (0.15 + 0.35 * i) + math.sin(t * 0.55 + i * 2.1) * w * 0.07
            cy = h * (0.30 + 0.35 * (i % 2)) + math.cos(t * 0.45 + i * 1.7) * h * 0.22
            r = h * (1.15 + 0.25 * math.sin(t * 0.4 + i))
            rg = QRadialGradient(cx, cy, r)
            c0 = QColor(tint)
            c0.setAlpha(62)
            c1 = QColor(tint)
            c1.setAlpha(0)
            rg.setColorAt(0, c0)
            rg.setColorAt(1, c1)
            p.setBrush(QBrush(rg))
            p.drawRect(self.rect())
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(C["border"]), 1))
        p.drawPath(path)
        # puntos de navegación
        n = len(self.apps)
        self.dot_rects = []
        x = w - 28
        for i in range(n - 1, -1, -1):
            active = i == self.idx
            dw = 8 + (16 * ease_out(self.mix) if active else 0)
            r = QRectF(x - dw, h - 20, dw, 8)
            x -= dw + 6
            self.dot_rects.insert(0, r)
            col = QColor("#ffffff")
            col.setAlpha(230 if active else 90)
            dp = QPainterPath()
            dp.addRoundedRect(r, 4, 4)
            p.fillPath(dp, col)


def icon_pixmap(ctx: Ctx, app: App, size: int) -> QPixmap:
    pm = ctx.icons.pixmap(app)
    if pm:
        return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_tile(p, QRectF(0, 0, size, size), app)
    p.end()
    return out


# --------------------------------------------------------------------------- #
#  Diálogo de detalle: elegir Flatpak / nativo / web
# --------------------------------------------------------------------------- #
class SourceCard(QFrame):
    selected = pyqtSignal()

    def __init__(self, src: Source, installed: bool, recommended: bool):
        super().__init__()
        self.src = src
        self.setObjectName("srcCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        self.radio = QRadioButton()
        self.radio.toggled.connect(lambda on: self.selected.emit() if on else None)
        lay.addWidget(self.radio)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(src.label)
        title.setObjectName("srcTitle")
        sub = QLabel(src.note + (f"  ·  {src.pkg}" if src.pkg and src.kind != "web" else ""))
        sub.setObjectName("srcSub")
        sub.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        tags = QVBoxLayout()
        tags.setSpacing(4)
        if recommended:
            t = QLabel("Recomendado")
            t.setObjectName("tagRec")
            tags.addWidget(t, 0, AL.AlignRight)
        if installed:
            t = QLabel("Instalado")
            t.setObjectName("tagOk")
            tags.addWidget(t, 0, AL.AlignRight)
        lay.addLayout(tags)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_selected(self, on: bool):
        self.setProperty("sel", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        self.radio.setChecked(True)


class DetailDialog(QDialog):
    def __init__(self, ctx: Ctx, app: App, window: "StoreWindow"):
        super().__init__(window)
        self.ctx, self.app, self.window_ = ctx, app, window
        self.setWindowTitle(app.name)
        self.setModal(True)
        self.setMinimumWidth(680)
        self.setObjectName("detail")
        self.sources = ctx.backend.ordered_sources(app)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(16)
        ic = QLabel()
        ic.setPixmap(icon_pixmap(ctx, app, 84))
        ic.setFixedSize(84, 84)
        head.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(3)
        n = QLabel(app.name)
        n.setObjectName("dTitle")
        d = QLabel(app.desc)
        d.setObjectName("dDesc")
        d.setWordWrap(True)
        self.creator = QLabel(f"Creador: {app.creator or 'Buscando información…'}")
        self.creator.setObjectName("dHint")
        cat = QLabel(CATEGORIES.get(app.cat, "Otras apps").upper())
        cat.setObjectName("heroKicker")
        col.addWidget(cat)
        col.addWidget(n)
        col.addWidget(d)
        col.addWidget(self.creator)
        col.addStretch()
        head.addLayout(col, 1)
        root.addLayout(head)

        gallery_title = QLabel("IMÁGENES DE LA APLICACIÓN")
        gallery_title.setObjectName("heroKicker")
        root.addWidget(gallery_title)
        self.gallery = QWidget()
        self.gallery_layout = QHBoxLayout(self.gallery)
        self.gallery_layout.setContentsMargins(0, 0, 0, 0)
        self.gallery_layout.setSpacing(10)
        self.gallery_status = QLabel("Cargando imágenes…")
        self.gallery_status.setObjectName("dHint")
        self.gallery_layout.addWidget(self.gallery_status)
        gallery_scroll = QScrollArea()
        gallery_scroll.setWidgetResizable(True)
        gallery_scroll.setFixedHeight(150)
        gallery_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        gallery_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        gallery_scroll.setFrameShape(QFrame.Shape.NoFrame)
        gallery_scroll.setWidget(self.gallery)
        root.addWidget(gallery_scroll)

        dist = ctx.backend.distro
        hint = QLabel(f"Tu sistema: {dist.name}" + ("  ·  inmutable (solo Flatpak)" if dist.immutable else ""))
        hint.setObjectName("dHint")
        root.addWidget(hint)

        self.cards: list[SourceCard] = []
        self.group = QButtonGroup(self)
        if not self.sources:
            lbl = QLabel("No hay una versión disponible para tu distribución. Prueba con Flatpak o una web app.")
            lbl.setWordWrap(True)
            lbl.setObjectName("dDesc")
            root.addWidget(lbl)
        for i, s in enumerate(self.sources):
            card = SourceCard(s, ctx.backend.source_installed(app, s), recommended=(i == 0 and len(self.sources) > 1))
            self.group.addButton(card.radio, i)
            self.cards.append(card)
            root.addWidget(card)
        if self.cards:
            self.cards[0].radio.setChecked(True)

        if app.note:
            note = QLabel("ℹ️  " + app.note)
            note.setObjectName("dNote")
            note.setWordWrap(True)
            root.addWidget(note)

        self.row = QHBoxLayout()
        self.row.setSpacing(10)
        self.btn_close = QPushButton("Cerrar")
        self.btn_close.setObjectName("ghost")
        self.btn_close.clicked.connect(self.reject)
        self.btn_remove = QPushButton("Desinstalar")
        self.btn_remove.setObjectName("danger")
        self.btn_second = QPushButton()
        self.btn_second.setObjectName("ghost")
        self.btn_main = QPushButton()
        self.btn_main.setObjectName("primary")
        self.row.addWidget(self.btn_close)
        self.row.addStretch()
        for b in (self.btn_remove, self.btn_second, self.btn_main):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.row.addWidget(b)
        root.addLayout(self.row)
        self._act_main = self._act_second = ""
        self.btn_main.clicked.connect(lambda: self._do(self._act_main))
        self.btn_second.clicked.connect(lambda: self._do(self._act_second))
        self.btn_remove.clicked.connect(lambda: self._do("uninstall"))
        for card in self.cards:
            card.selected.connect(self._sync)
        self._sync()

    def set_preview(self, preview: tuple[str, list[bytes]] | None):
        creator, images = preview if preview else (self.app.creator, [])
        self.creator.setText(f"Creador: {creator or 'No especificado'}")
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for data in images:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                continue
            image = QLabel()
            image.setObjectName("dScreenshot")
            image.setAlignment(AL.AlignCenter)
            image.setFixedSize(230, 128)
            image.setPixmap(pixmap.scaled(image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
            self.gallery_layout.addWidget(image)

        if not self.gallery_layout.count():
            self.gallery_status = QLabel("No hay capturas disponibles para esta aplicación.")
            self.gallery_status.setObjectName("dHint")
            self.gallery_layout.addWidget(self.gallery_status)
        self.gallery_layout.addStretch()

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, "_faded", False):
            return
        self._faded = True
        self.setWindowOpacity(0.0)
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        self._fade_anim.finished.connect(lambda: self.setWindowOpacity(1.0))
        self._fade_anim.start()

    def current(self) -> Source | None:
        i = self.group.checkedId()
        return self.sources[i] if 0 <= i < len(self.sources) else None

    def _sync(self):
        for i, c in enumerate(self.cards):
            c.set_selected(self.group.checkedId() == i)
        s = self.current()
        self.btn_remove.hide()
        self.btn_second.hide()
        self.btn_main.setVisible(s is not None)
        if not s:
            return
        inst = self.ctx.backend.source_installed(self.app, s)
        if s.kind in ("native", "aur", "flatpak"):
            if inst:
                self.btn_main.setText("Abrir")
                self._act_main = "launch"
                self.btn_remove.show()
            else:
                self.btn_main.setText("Instalar")
                self._act_main = "install"
        elif s.kind == "web":
            self.btn_main.setText("Abrir web app")
            self._act_main = "launch"
            self.btn_second.show()
            self.btn_second.setText("Quitar acceso directo" if inst else "Crear acceso directo")
            self._act_second = "uninstall" if inst else "install"
        else:
            self.btn_main.setText("Ir a la descarga oficial")
            self._act_main = "open_url"

    def _do(self, action: str):
        s = self.current()
        if s and action:
            self.accept()
            self.window_.run_action(self.app, s, action)


# --------------------------------------------------------------------------- #
#  Barra de tareas y registro
# --------------------------------------------------------------------------- #
class TaskBar(QFrame):
    def __init__(self, tasks: TaskManager):
        super().__init__()
        self.tasks = tasks
        self.setObjectName("taskbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)
        self.label = QLabel()
        self.label.setObjectName("taskLabel")
        self.bar = QProgressBar()
        self.bar.setFixedHeight(8)
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(260)
        self.msg = QLabel()
        self.msg.setObjectName("taskMsg")
        self.btn_log = QPushButton("Registro")
        self.btn_log.setObjectName("ghostSmall")
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setObjectName("ghostSmall")
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addWidget(self.msg, 1)
        lay.addWidget(self.btn_log)
        lay.addWidget(self.btn_cancel)
        self.btn_cancel.clicked.connect(tasks.cancel)
        tasks.pct.connect(self.set_pct)
        tasks.line.connect(self.set_msg)
        tasks.changed.connect(self.update_state)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._maybe_hide)
        self.hide()

    def set_pct(self, v: int):
        if v < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(v)

    def set_msg(self, text: str):
        fm = QFontMetrics(self.msg.font())
        self.msg.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, max(200, self.msg.width())))

    def update_state(self):
        if self.tasks.busy:
            self.hide_timer.stop()
            extra = f"  (+{len(self.tasks.queue)} en cola)" if self.tasks.queue else ""
            self.label.setText("⏳ " + self.tasks.current + extra)
            self.btn_cancel.show()
            self.bar.show()
            self.show()

    def _flash(self, ok: bool):
        rgb = "61,220,151" if ok else "255,93,115"
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(1200)
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        def apply(v):
            a = float(v)
            self.setStyleSheet(
                f"QFrame#taskbar {{ background: rgba({rgb},{0.16 * a:.3f}); "
                f"border: 1px solid rgba({rgb},{0.2 + 0.8 * a:.3f}); border-radius: 12px; }}")
        self._flash_anim.valueChanged.connect(apply)
        self._flash_anim.finished.connect(lambda: self.setStyleSheet(""))
        self._flash_anim.start()

    def finished(self, title: str, ok: bool, msg: str):
        if not self.tasks.busy:
            self._flash(ok)
        self.btn_cancel.setVisible(self.tasks.busy)
        self.bar.setVisible(self.tasks.busy)
        if not self.tasks.busy:
            self.label.setText(("✅ " if ok else "⚠️ ") + title)
            self.msg.setText(msg)
            self.show()
            self.hide_timer.start(9000 if ok else 20000)

    def _maybe_hide(self):
        if not self.tasks.busy:
            self.hide()


class LogDialog(QDialog):
    def __init__(self, tasks: TaskManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Registro de operaciones")
        self.resize(760, 460)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setPlainText("\n".join(tasks.log))
        lay.addWidget(self.text)
        tasks.line.connect(self.text.appendPlainText)


# --------------------------------------------------------------------------- #
#  Ventana principal
# --------------------------------------------------------------------------- #
NAV = [
    ("inicio", "🏠", "Inicio"), ("explorar", "🧭", "Explorar todo"), None,
    ("juegos", "🎮", "Juegos"), ("oficina", "📄", "Ofimática"), ("multimedia", "🎬", "Video y audio"),
    ("diseno", "🎨", "Diseño y 3D"), ("dev", "💻", "Desarrollo"), ("redes", "🌐", "Redes"),
    ("internet", "💬", "Internet y chat"), ("educacion", "🎓", "Educación"), ("utiles", "🧰", "Utilidades"),
    None, ("web", "🔗", "Web y descargas"), ("instaladas", "✅", "Instaladas"),
]
TITLES = {"inicio": "Descubre", "explorar": "Todas las aplicaciones", "web": "Web apps y descargas oficiales",
          "instaladas": "Instaladas", **CATEGORIES}


class StoreWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.backend = Backend()
        self.catalog = Catalog()
        self.icons = IconProvider(self.backend)
        self.ctx = Ctx(self.backend, self.catalog, self.icons)
        self.tasks = TaskManager(self.backend)
        self.model = AppModel()
        self.nav = "inicio"
        self.query = ""
        self._threads: list[QThread] = []
        self._detail_dialogs: list[DetailDialog] = []
        self._log_dialog: LogDialog | None = None
        self._build()
        self.icons.ready.connect(self._icons_ready)
        self.tasks.task_done.connect(self._task_done)
        self.apply_filter()
        self._spawn(self.backend.refresh_installed, lambda _: self._viewport_update())
        self._spawn(self.backend.load_flathub, self._flathub_loaded)
        self.status.setText("Cargando catálogo completo de Flathub…")
        updater.ensure_menu_entry()
        QTimer.singleShot(4000, lambda: self._spawn(updater.check, self._update_found))

    # ---------- actualización de la tienda ----------
    def _update_found(self, info):
        if not info:
            return
        ver = info["version"]
        if not (info["url"] and updater.can_self_update()):
            if QMessageBox.question(self, "Fecs Store", f"Hay una versión nueva ({ver}). ¿Abrir la página de descarga?"
                                    ) == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(info["page"]))
            return
        notes = info["notes"][:600]
        box = QMessageBox(self)
        box.setWindowTitle("Fecs Store")
        box.setText(f"Hay una versión nueva de Fecs Store: {ver}")
        box.setInformativeText((notes + "\n\n" if notes else "") + "¿Actualizar ahora? Se reinicia sola al terminar.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Actualizar ahora")
        box.button(QMessageBox.StandardButton.No).setText("Más tarde")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        dlg = QProgressDialog(f"Descargando Fecs Store {ver}…", "", 0, 100, self)
        dlg.setWindowTitle("Fecs Store")
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.show()
        worker = UpdateWorker(info, self)
        worker.pct.connect(dlg.setValue)
        worker.finished_ok.connect(lambda ok, err: self._update_done(dlg, ok, err))
        self._threads.append(worker)
        worker.finished.connect(lambda th=worker: self._reap(th))
        worker.start()

    def _update_done(self, dlg, ok: bool, err: str):
        dlg.close()
        if not ok:
            QMessageBox.warning(self, "Fecs Store", f"No se pudo actualizar: {err}")
            return
        path = updater.appimage_path()
        if path:
            updater.restart(path)
        QApplication.quit()

    # ---------- construcción ----------
    def _build(self):
        self.setWindowTitle("Fecs Store")
        self.resize(1220, 780)
        self.setMinimumSize(920, 620)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        main = QWidget()
        main.setObjectName("main")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(28, 22, 28, 14)
        ml.setSpacing(14)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Buscar apps, juegos, editores de video, suites ofimáticas…   (Ctrl+K)")
        self.search.setClearButtonEnabled(True)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._search_now)
        self.search.textChanged.connect(lambda _t: self._debounce.start(130))
        top.addWidget(self.search)
        ml.addLayout(top)

        self.hero = Hero(self.ctx)
        self.hero.open_app.connect(self.open_detail)
        ml.addWidget(self.hero)

        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        self.count = QLabel()
        self.count.setObjectName("count")
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(self.count)
        ml.addLayout(head)

        self.stack = QStackedWidget()
        self.view = AppView(self.ctx, self.model)
        self.view.app_clicked.connect(self.open_detail)
        self.empty = QLabel("No encontramos nada con esa búsqueda.\nPrueba con otro nombre o explora todo Flathub.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(AL.AlignCenter)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty)
        ml.addWidget(self.stack, 1)

        self.taskbar = TaskBar(self.tasks)
        self.taskbar.btn_log.clicked.connect(self._show_log)
        ml.addWidget(self.taskbar)
        self.status = QLabel("")
        self.status.setObjectName("status")
        ml.addWidget(self.status)
        root.addWidget(main, 1)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(236)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 20, 14, 16)
        lay.setSpacing(4)
        logo = QLabel("⚡ Fecs Store")
        logo.setObjectName("logo")
        lay.addWidget(logo)
        lay.addSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("navInner")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(2)
        self.nav_btns: dict[str, QPushButton] = {}
        for item in NAV:
            if item is None:
                il.addSpacing(8)
                continue
            key, icon, label = item
            b = QPushButton(f"{icon}   {label}")
            b.setCheckable(True)
            b.setObjectName("nav")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c, k=key: self.set_nav(k))
            il.addWidget(b)
            self.nav_btns[key] = b
        il.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        self.nav_btns["inicio"].setChecked(True)

        d = self.backend.distro
        info = QLabel(f"🐧 {d.name}\n{'Inmutable · solo Flatpak' if d.immutable else 'Nativo: ' + d.pm_label + (' + AUR' if d.family == 'arch' else '')}"
                      f"\nFlatpak: {'✓' if self.backend.has_flatpak else '✗ no instalado'}")
        info.setObjectName("distro")
        info.setWordWrap(True)
        lay.addWidget(info)

        pref_lbl = QLabel("Preferir versión")
        pref_lbl.setObjectName("miniLabel")
        lay.addWidget(pref_lbl)
        self.pref = QComboBox()
        self.pref.addItem("Automática (recomendada)", "auto")
        self.pref.addItem("Flatpak", "flatpak")
        self.pref.addItem("Nativa del sistema", "native")
        cur = self.backend.settings.get("prefer", "auto")
        self.pref.setCurrentIndex(max(0, self.pref.findData(cur)))
        self.pref.currentIndexChanged.connect(self._pref_changed)
        lay.addWidget(self.pref)

        upd = QPushButton("⟳  Actualizar todo")
        upd.setObjectName("ghost")
        upd.setCursor(Qt.CursorShape.PointingHandCursor)
        upd.clicked.connect(self._update_all)
        lay.addWidget(upd)
        if self.backend.speedup_steps():
            sp = QPushButton("⚡  Acelerar descargas")
            sp.setObjectName("ghost")
            sp.setCursor(Qt.CursorShape.PointingHandCursor)
            sp.clicked.connect(self._speed_up)
            lay.addWidget(sp)
        if not self.backend.has_flatpak and not d.immutable and d.family != "unknown":
            fp = QPushButton("Instalar Flatpak")
            fp.setObjectName("primary")
            fp.clicked.connect(lambda: self.tasks.submit("Instalando Flatpak", self.backend.install_flatpak_steps()))
            lay.addWidget(fp)
        return side

    # ---------- filtrado ----------
    def set_nav(self, key: str):
        self.nav = key
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.query = ""
        self.apply_filter()

    def _search_now(self):
        self.query = self.search.text().strip().lower()
        self.apply_filter()

    def apply_filter(self):
        cat = self.catalog
        be = self.backend
        q = self.query
        if q:
            scored = []
            for a in cat.apps:
                s = a._search
                if q not in s:
                    continue
                n = a.name.lower()
                score = 0 if n == q else 1 if n.startswith(q) else 2 if q in n else 3
                scored.append((score, 0 if a.curated else 1, n, a))
            scored.sort(key=lambda t: t[:3])
            apps = [t[3] for t in scored]
            title = f"Resultados para «{self.search.text().strip()}»"
        else:
            nav = self.nav
            if nav == "inicio":
                apps = list(cat.curated)
            elif nav == "explorar":
                apps = list(cat.apps)
            elif nav == "web":
                apps = [a for a in cat.curated if a.web or a.dl]
            elif nav == "instaladas":
                apps = [a for a in cat.apps if be.app_installed(a)]
            else:
                apps = [a for a in cat.apps if a.cat == nav]
            title = TITLES.get(nav, "")
        self.hero.setVisible(self.nav == "inicio" and not q)
        self.title.setText(title)
        self.count.setText(f"{len(apps):,} apps".replace(",", "."))
        self.view.set_apps(apps)
        self.stack.setCurrentIndex(0 if apps else 1)
        if self.nav == "instaladas" and not apps and not q:
            self.empty.setText("Todavía no hay apps instaladas desde aquí.")
        elif not apps:
            self.empty.setText("No encontramos nada con esa búsqueda.\nPrueba con otro nombre o explora todo Flathub.")
        self.view.verticalScrollBar().setValue(0)

    # ---------- hilos ----------
    def _spawn(self, fn, cb):
        t = LoaderThread(fn, self)
        t.done.connect(cb)
        t.finished.connect(lambda th=t: self._reap(th))
        self._threads.append(t)
        t.start()

    def _reap(self, th):
        if th in self._threads:
            self._threads.remove(th)

    def _flathub_loaded(self, data):
        if data:
            added = self.catalog.merge_flathub(data)
            self.status.setText(f"Catálogo listo: {len(self.catalog.apps):,} aplicaciones "
                                f"({added:,} extra de Flathub).".replace(",", "."))
            self.apply_filter()
        else:
            self.status.setText("Sin conexión a Flathub: mostrando el catálogo local.")
        QTimer.singleShot(8000, lambda: self.status.setText(""))

    def _icons_ready(self):
        self.view.kick()
        self.hero.refresh_icon()

    def _viewport_update(self):
        if self.nav == "instaladas":
            self.apply_filter()
        self.view.viewport().update()

    # ---------- acciones ----------
    def open_detail(self, app: App):
        if app:
            dialog = DetailDialog(self.ctx, app, self)
            self._detail_dialogs.append(dialog)
            self._spawn(lambda: self.backend.load_preview(app),
                        lambda preview, target=dialog: self._set_preview(target, preview))
            try:
                dialog.exec()
            finally:
                self._detail_dialogs.remove(dialog)

    def _set_preview(self, dialog: DetailDialog, preview):
        if dialog in self._detail_dialogs:
            dialog.set_preview(preview)

    def run_action(self, app: App, src: Source, action: str):
        be = self.backend
        if action == "open_url":
            QDesktopServices.openUrl(QUrl(src.url))
            return
        if action == "launch":
            ok, msg = be.launch(app, src)
            self.status.setText(msg)
            return
        steps, err = be.plan(app, src, action)
        if err:
            QMessageBox.warning(self, "Fecs Store", err)
            return
        if src.kind == "web":      # operación instantánea de archivos
            ok, msg = be.run_steps(steps, lambda _l: None, lambda _p: None)
            self.status.setText(("Acceso directo actualizado: " if ok else "Error: ") + (app.name if ok else msg))
            self._viewport_update()
            return
        verb = "Instalando" if action == "install" else "Desinstalando"
        self.tasks.submit(f"{verb} {app.name}", steps)

    def _task_done(self, title: str, ok: bool, msg: str):
        self.taskbar.finished(title, ok, msg)
        self._viewport_update()

    def _update_all(self):
        steps = self.backend.update_all_steps()
        if not steps:
            QMessageBox.information(self, "Fecs Store", "No hay gestores de paquetes disponibles para actualizar.")
            return
        self.tasks.submit("Actualizando el sistema y las apps", steps)

    def _speed_up(self):
        ok = QMessageBox.question(
            self, "Fecs Store",
            "Esto activa 10 descargas en paralelo en /etc/pacman.conf y, si tu sistema lo trae, "
            "ordena los espejos por velocidad. Pide permisos de administrador. ¿Continuar?")
        if ok == QMessageBox.StandardButton.Yes:
            self.tasks.submit("Acelerando las descargas", self.backend.speedup_steps())

    def _pref_changed(self, _i):
        self.backend.settings.set("prefer", self.pref.currentData())
        self.view.viewport().update()

    def _show_log(self):
        if self._log_dialog is None:
            self._log_dialog = LogDialog(self.tasks, self)
        self._log_dialog.show()
        self._log_dialog.raise_()
