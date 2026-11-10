"""Small custom widgets: fields, buttons, timeline, popover, toast."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import math
import time

from PySide6.QtGui import QCursor, QInputDevice, QPointingDevice

import perf_log
import theme


def button(text: str = "", checkable: bool = False, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def dim_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("dim", True)
    return label


# ---------------------------------------------------------------- field


class _FieldEdit(QLineEdit):
    escaped = Signal()
    stepped = Signal(int, bool)      # ↑/↓: steps, coarse

    # Keys the field keeps for itself even if a window hotkey uses them
    # (←/→ move the text cursor instead of jumping frames).
    OWN_KEYS = (
        Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter,
        Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
        Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Backspace, Qt.Key.Key_Delete,
    )

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in self.OWN_KEYS:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            coarse = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.stepped.emit(1 if key == Qt.Key.Key_Up else -1, coarse)
            return
        super().keyPressEvent(event)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clearFocus()


def _is_pen(event) -> bool:
    """Tablet pen (absolute positioning): warping the cursor would fight the pen."""
    try:
        device = event.pointingDevice()
        if device is None:
            return False
        return device.type() in (
            QInputDevice.DeviceType.Stylus, QInputDevice.DeviceType.Airbrush,
            QInputDevice.DeviceType.Puck,
        ) or device.pointerType() in (
            QPointingDevice.PointerType.Pen, QPointingDevice.PointerType.Eraser,
        )
    except Exception:
        return False


class FieldGroup(QWidget):
    """Fields that belong together (In/Out, X/Y/W/H) as one segmented block:
    a small gap in the window color between segments instead of a drawn line
    (1px strokes render unevenly at 125/150%), rounded outer corners only."""

    GAP = 2

    def __init__(self, fields: list, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.GAP)
        for i, field in enumerate(fields):
            seg = "first" if i == 0 else "last" if i == len(fields) - 1 else "mid"
            field.setProperty("seg", seg)
            layout.addWidget(field)


class Field(QFrame):
    """Figma-style input: dim prefix (or icon) + value.

    Click → edit (Enter applies, Esc reverts; both return focus to the video).
    ↑/↓ while editing, drag with LMB/MMB or turn the wheel → `nudged(steps, coarse)`;
    Shift = coarse step. Dragging is endless (Blender-style): the hidden cursor is
    quietly put back to the press point every WARP px.
    """

    committed = Signal(str)
    nudged = Signal(int, bool)
    ctrl_clicked = Signal()        # T39: Ctrl+click (Cmd on macOS) on the ⌛ field
    scrub_started = Signal()
    scrub_finished = Signal()
    left = Signal()

    PX_PER_STEP = 3
    WARP = 100          # px from the press point before the cursor is put back

    def __init__(self, prefix: str, width: int, parent=None, icon: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Field")
        self.setFixedSize(width, theme.H)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)     # T34c: + QLineEdit's own ~2 px → reads as 8
        self.prefix = icon if icon is not None else dim_label(prefix)
        self.prefix.setCursor(Qt.CursorShape.SizeHorCursor)
        self.edit = _FieldEdit()
        self.edit.setFrame(False)
        # Focus only via a plain click (see _release) — otherwise Qt focuses the
        # edit on press and dragging would select text instead of scrubbing.
        self.edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.prefix, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.edit, 1)
        # Only after addWidget: setVisible(True) on a parentless widget opens it
        # as a top-level window for a moment (startup flicker on Windows).
        if not prefix and icon is None:
            self.prefix.hide()
        self._value = ""
        self.ctrl_click = False        # T39: only the ⌛ field reacts to Ctrl+click
        self._origin: QPoint | None = None
        self._last_x = 0.0
        self._button = Qt.MouseButton.NoButton
        self._scrubbing = False
        self._pen = False
        self._acc = 0.0
        for widget in (self.edit, self.prefix):
            widget.installEventFilter(self)
        self.edit.editingFinished.connect(self._finish)
        self.edit.escaped.connect(self._escape)
        self.edit.stepped.connect(self._arrow_step)

    def setText(self, text: str) -> None:
        self._value = text
        if not self.edit.hasFocus():
            self.edit.setText(text)

    def text(self) -> str:
        return self.edit.text()

    def is_busy(self) -> bool:
        """True while the user types or scrubs — don't overwrite the value."""
        return self.edit.hasFocus() or self._scrubbing

    def set_error(self, error: bool) -> None:
        theme.set_flag(self, "error", error)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self.edit.setEnabled(enabled)

    # --- keyboard
    def _arrow_step(self, steps: int, coarse: bool) -> None:
        text = self.edit.text()
        if text != self._value or self.property("error"):
            self.committed.emit(text)        # typed but not applied → apply first (as Enter)
            if self.property("error"):
                return
        self.nudged.emit(steps, coarse)
        self.edit.setText(self._value)
        self.edit.selectAll()

    def _sync_text(self) -> None:
        if not self.edit.hasFocus() and not self.property("error"):
            self.edit.setText(self._value)

    # --- scrubbing
    def _press(self, event) -> bool:
        if not self.edit.isEnabled() or self.edit.hasFocus():
            return False
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and self.ctrl_click):
            self.ctrl_clicked.emit()
            return True
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            return False
        self._origin = event.globalPosition().toPoint()
        self._last_x = float(self._origin.x())
        self._button = event.button()
        self._scrubbing = False
        self._pen = _is_pen(event)
        self._acc = 0.0
        return True

    def _move(self, event) -> bool:
        if self._origin is None:
            return False
        origin = QPointF(self._origin)
        if not self._scrubbing:
            if abs(event.globalPosition().x() - origin.x()) < 3:
                return True
            self._scrubbing = True
            self.scrub_started.emit()
            QApplication.setOverrideCursor(
                Qt.CursorShape.SizeHorCursor if self._pen else Qt.CursorShape.BlankCursor)
        # The real cursor, not the event: events queued while the window was busy
        # still carry positions from before the warp and made the value jump
        # back and forth (4.1.0). Pen: absolute positions, the event is fine.
        pos = event.globalPosition() if self._pen else QPointF(QCursor.pos())
        self._acc += pos.x() - self._last_x
        self._last_x = pos.x()
        steps = int(self._acc / self.PX_PER_STEP)
        if steps:
            self._acc -= steps * self.PX_PER_STEP
            coarse = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.nudged.emit(steps, coarse)
        if not self._pen and max(abs(pos.x() - origin.x()), abs(pos.y() - origin.y())) > self.WARP:
            QCursor.setPos(self._origin)
            self._last_x = origin.x()
        return True

    def _release(self, event) -> bool:
        if self._origin is None:
            return False
        was_scrubbing = self._scrubbing
        origin, self._origin = self._origin, None
        self._scrubbing = False
        if was_scrubbing:
            if not self._pen:
                QCursor.setPos(origin)       # cursor reappears where the scrub started
            QApplication.restoreOverrideCursor()
            self.scrub_finished.emit()
        elif self._button == Qt.MouseButton.LeftButton:
            self.edit.setFocus(Qt.FocusReason.MouseFocusReason)
        return True

    def mousePressEvent(self, event) -> None:
        if not self._press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if not self._release(event):
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if not self.edit.isEnabled():
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        steps = round(delta / 120) or (1 if delta > 0 else -1 if delta < 0 else 0)
        if steps:
            coarse = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self.edit.hasFocus():
                # Editing: like ↑/↓ — apply typed text, step, show and select the value
                # (otherwise Enter would later re-apply the stale text and undo the wheel).
                self._arrow_step(steps, coarse)
            else:
                self.nudged.emit(steps, coarse)
        event.accept()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            return self._press(event)
        if kind == QEvent.Type.MouseMove:
            return self._move(event)
        if kind == QEvent.Type.MouseButtonRelease:
            return self._release(event)
        if kind == QEvent.Type.MouseButtonDblClick and not self.edit.hasFocus():
            return True
        if kind == QEvent.Type.Wheel:
            self.wheelEvent(event)
            return True
        if obj is self.edit:
            if kind == QEvent.Type.FocusIn:
                QTimer.singleShot(0, self.edit.selectAll)
            elif kind == QEvent.Type.FocusOut:
                # After editingFinished has been handled: show the applied value.
                QTimer.singleShot(0, self._sync_text)
                self.left.emit()
        return False

    def _finish(self) -> None:
        if self.edit.text() != self._value or self.property("error"):
            self.committed.emit(self.edit.text())

    def _escape(self) -> None:
        self.edit.setText(self._value)
        self.set_error(False)
        self.edit.clearFocus()


# ------------------------------------------------------- special buttons


class PlayButton(QPushButton):
    """▶ / ❚❚ drawn with QPainter (emoji fonts would render them in color)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(48, theme.H)
        self.setToolTip("Play / Pause")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.TEXT if self.isEnabled() else theme.DIM))
        c = QPointF(self.rect().center()) + QPointF(0.5, 0.5)
        if self.isChecked():  # playing → pause icon
            p.drawRoundedRect(QRectF(c.x() - 4, c.y() - 4.5, 3, 9), 0.5, 0.5)
            p.drawRoundedRect(QRectF(c.x() + 1, c.y() - 4.5, 3, 9), 0.5, 0.5)
        else:
            p.drawPolygon(QPolygonF([
                QPointF(c.x() - 3.5, c.y() - 5),
                QPointF(c.x() + 5, c.y()),
                QPointF(c.x() - 3.5, c.y() + 5),
            ]))
        p.end()


class ProgressButton(QPushButton):
    """Export button: while busy shows the progress with a fill; on hover it turns
    red and reads "Cancel" (T19c) — a click cancels."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.idle_text = text
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.busy = False
        self.percent = 0

    def set_idle_text(self, text: str) -> None:
        """T42: the label follows Save To (Export / Export to…)."""
        self.idle_text = text
        # padding 10 px + 4 px air per side: the label never gets clipped
        self.setMinimumWidth(max(64, self.fontMetrics().horizontalAdvance(text) + 28))
        if not self.busy:
            self.setText(text)
        self.updateGeometry()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.percent = 0
        self.setText("0%" if busy else self.idle_text)
        self.update()

    def set_percent(self, percent: int) -> None:
        self.set_progress(0, 1, percent)

    def set_progress(self, stage: int, total: int, percent: int) -> None:
        """Fill = overall progress; text shows the stage when there are several."""
        self.percent = round((stage + percent / 100) / max(1, total) * 100)
        prefix = f"{stage + 1}/{total} · " if total > 1 else ""
        self.setText(f"{prefix}{percent}%")
        self.update()

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:
        if not self.busy:
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), theme.RADIUS, theme.RADIUS)
        if self.underMouse() and self.isEnabled():
            p.fillPath(path, QColor(theme.ERROR_BG))
            p.setPen(QColor(theme.ERROR_FG))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Cancel")
            p.end()
            return
        p.fillPath(path, QColor(theme.BUTTON))
        p.setClipPath(path)
        fill = QRectF(self.rect())
        fill.setWidth(fill.width() * self.percent / 100)
        p.fillRect(fill, QColor(theme.ACTIVE))
        p.setClipping(False)
        p.setPen(QColor(theme.TEXT))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class CheckBox(QWidget):
    """Small rounded box with ✓ + label (Figma "Frame 47"). Emits `toggled(bool)`."""

    toggled = Signal(bool)
    BOX = 18

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.checked = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(theme.H)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(self.BOX + 8 + self.fontMetrics().horizontalAdvance(self.text), theme.H)

    def minimumSizeHint(self):  # noqa: N802
        return self.sizeHint()

    def set_checked(self, value: bool) -> None:
        if self.checked != value:
            self.checked = value
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.set_checked(not self.checked)
            self.toggled.emit(self.checked)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = (self.height() - self.BOX) / 2
        box = QRectF(0, top, self.BOX, self.BOX)
        path = QPainterPath()
        path.addRoundedRect(box, 4, 4)
        p.fillPath(path, QColor(theme.BUTTON_HOVER if self.underMouse() else theme.BUTTON))
        p.setPen(QColor(theme.TEXT))
        if self.checked:
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "✓")
        text_rect = self.rect().adjusted(self.BOX + 8, 0, 0, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
        p.end()


class Segmented(QWidget):
    """Row of exclusive buttons. Emits `selected(value)`."""

    selected = Signal(str)

    def __init__(self, options: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for value, text, tip in options:
            b = button(text, checkable=True, tooltip=tip)
            b.setProperty("seg", True)
            b.clicked.connect(lambda _=False, v=value: self.selected.emit(v))
            self.group.addButton(b)
            self.buttons[value] = b
            layout.addWidget(b)
        layout.addStretch(1)

    def set_value(self, value: str) -> None:
        if value in self.buttons:
            self.buttons[value].setChecked(True)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        for b in self.buttons.values():
            b.setEnabled(enabled)


# ------------------------------------------------------------- timeline


class Timeline(QWidget):
    """Playhead + In/Out range + zoom. Click/drag to scrub, drag range edges to trim.

    Click without moving → one exact seek. Dragging → fast (keyframe) seeks through
    the player's queue; release → exact seek on the frame under the cursor.
    Wheel = zoom around the mouse cursor, middle button / horizontal wheel = pan.
    A passive minimap at the top shows the visible window and the playhead.
    """

    seek_requested = Signal(float, bool)   # seconds, exact
    in_dragged = Signal(float)
    out_dragged = Signal(float)
    edge_preview = Signal(float, bool)     # In/Out edge drag: time, final (release)

    EDGE = 6          # px grab zone around In/Out edges
    DRAG_START = 3    # px before a click becomes a drag
    MINIMAP_H = 2     # px
    ZOOM_STEP = 1.25  # per wheel notch
    MAX_PX_PER_FRAME = 10
    LABEL_SPACING = 110   # px, minimum distance between ruler labels
    NICE_STEPS = (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
    NICE_FRAME_STEPS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000,
                        25000, 50000, 100000, 250000)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.H)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.frame_duration = 1 / 24
        self.position = 0.0
        self.trim_in = 0.0
        self.trim_out = 0.0
        self.trim_enabled = False
        self.view_start = 0.0
        self.view_end = 0.0
        self._drag: str | None = None   # "seek" | "in" | "out"
        self._press_x = 0.0
        self._moved = False
        self._last_move = 0.0
        self._pan: tuple[float, float] | None = None   # (press x, view_start at press)
        self.frames_mode = False        # T39: ruler in frame numbers

    # --- state
    def set_duration(self, duration: float, frame_duration: float | None = None) -> None:
        """New file: whole file visible again (zoom resets)."""
        self.duration = max(0.0, duration)
        if frame_duration:
            self.frame_duration = frame_duration
        self.view_start, self.view_end = 0.0, self.duration
        self.update()

    def set_position(self, position: float) -> None:
        if self._drag is None and abs(position - self.position) > 1e-4:
            self.position = position
            self.update()

    def show_position(self, position: float) -> None:
        """Set the playhead even during a drag (edge preview / return)."""
        self.position = position
        self.update()

    def set_range(self, trim_in: float, trim_out: float) -> None:
        self.trim_in, self.trim_out = trim_in, trim_out
        self.update()

    def set_trim_enabled(self, enabled: bool) -> None:
        self.trim_enabled = enabled
        self.update()

    def set_frames_mode(self, enabled: bool) -> None:
        self.frames_mode = enabled
        self.update()

    def is_dragging(self) -> bool:
        return self._drag is not None

    def is_zoomed(self) -> bool:
        return self.duration > 0 and self.view_end - self.view_start < self.duration - 1e-9

    # --- mapping (visible window)
    def _span(self) -> float:
        return max(1e-9, self.view_end - self.view_start)

    def _x(self, t: float) -> float:
        if self.duration <= 0:
            return 0.0
        return (t - self.view_start) / self._span() * (self.width() - 1)

    def _t(self, x: float) -> float:
        if self.width() <= 1:
            return 0.0
        t = self.view_start + x / (self.width() - 1) * self._span()
        return max(0.0, min(self.duration, t))

    def _set_view(self, start: float, span: float) -> None:
        span = max(self._min_span(), min(self.duration, span))
        start = max(0.0, min(self.duration - span, start))
        self.view_start, self.view_end = start, start + span
        self.update()

    def _min_span(self) -> float:
        frames_visible = max(1.0, (self.width() - 1) / self.MAX_PX_PER_FRAME)
        return min(self.duration, frames_visible * self.frame_duration)

    def _edge_at(self, x: float) -> str | None:
        if self.duration <= 0 or not self.trim_enabled:
            return None
        if abs(x - self._x(self.trim_in)) <= self.EDGE:
            return "in"
        if abs(x - self._x(self.trim_out)) <= self.EDGE:
            return "out"
        return None

    # --- ruler
    def _label_step(self) -> float:
        px_per_s = (self.width() - 1) / self._span()
        if self.frames_mode:
            for frames in self.NICE_FRAME_STEPS:
                if frames * self.frame_duration * px_per_s >= self.LABEL_SPACING:
                    return frames * self.frame_duration
            return self.NICE_FRAME_STEPS[-1] * self.frame_duration
        for step in self.NICE_STEPS:
            if step * px_per_s >= self.LABEL_SPACING:
                return step
        return self.NICE_STEPS[-1]

    def _label(self, t: float, step: float) -> str:
        if self.frames_mode:
            return str(round(t / self.frame_duration))
        hours = self.duration >= 3600
        if step < 1:
            minutes, seconds = divmod(t, 60)
            text = f"{int(minutes):02d}:{seconds:04.1f}"
            if hours:
                h, m = divmod(int(minutes), 60)
                text = f"{h}:{m:02d}:{seconds:04.1f}"
            return text
        total = int(round(t))
        h, rest = divmod(total, 3600)
        m, sec = divmod(rest, 60)
        return f"{h}:{m:02d}:{sec:02d}" if hours else f"{m:02d}:{sec:02d}"

    # --- paint
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.FIELD))
        if self.duration > 0:
            if self.trim_enabled:
                x1, x2 = self._x(self.trim_in), self._x(self.trim_out)
                p.fillRect(QRectF(x1, 0, max(2.0, x2 - x1 + 1), h), QColor(theme.TRIM_RANGE))
            # ruler: tick at the bottom + label, no minor ticks
            step = self._label_step()
            first = math.floor(self.view_start / step) * step
            metrics = p.fontMetrics()
            baseline = (h + self.MINIMAP_H + metrics.ascent() - metrics.descent()) / 2
            t = first
            while t <= self.view_end + step:
                if self.frames_mode:            # exact multiples: no drift over long files
                    t = round(t / self.frame_duration) * self.frame_duration
                x = self._x(t)
                if -metrics.horizontalAdvance("00:00:00") <= x <= w:
                    tx = round(x)
                    p.fillRect(QRect(tx, h - 5, 1, 5), QColor(theme.RULER))
                    p.setPen(QColor(theme.DIM))
                    p.drawText(QPointF(tx + 4, baseline), self._label(t, step))
                t = round(t + step, 6)
            x = round(self._x(self.position))
            if 0 <= x < w:
                p.fillRect(QRect(x, 0, 1, h), QColor(theme.PLAYHEAD))
            # minimap: whole file, visible window, playhead tick
            p.fillRect(QRect(0, 0, w, self.MINIMAP_H), QColor(theme.MINIMAP_TRACK))
            scale = (w - 1) / self.duration
            vx1, vx2 = self.view_start * scale, self.view_end * scale
            p.fillRect(QRectF(vx1, 0, max(2.0, vx2 - vx1 + 1), self.MINIMAP_H), QColor(theme.MINIMAP_VIEW))
            p.fillRect(QRect(round(self.position * scale), 0, 1, self.MINIMAP_H + 1), QColor(theme.PLAYHEAD))
        p.end()

    # --- zoom / pan
    def wheelEvent(self, event) -> None:
        if self.duration <= 0:
            return
        dx, dy = event.angleDelta().x(), event.angleDelta().y()
        if dy:
            x = event.position().x()
            anchor = self._t(x)
            span = self._span() / (self.ZOOM_STEP ** (dy / 120))
            span = max(self._min_span(), min(self.duration, span))
            self._set_view(anchor - x / max(1, self.width() - 1) * span, span)
        elif dx:   # horizontal wheel / trackpad → pan
            self._set_view(self.view_start - dx / 120 * self._span() * 0.1, self._span())
        event.accept()

    # --- mouse
    def mousePressEvent(self, event) -> None:
        if self.duration <= 0:
            return
        x = event.position().x()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan = (x, self.view_start)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_x = x
        self._moved = False
        self._drag = self._edge_at(x) or "seek"
        if self._drag == "seek":
            # Exact seek right away — no keyframe preview that flickers back.
            self.position = self._t(x)
            self.update()
            self.seek_requested.emit(self.position, True)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._pan is not None:
            press_x, start = self._pan
            self._set_view(start - (x - press_x) / max(1, self.width() - 1) * self._span(), self._span())
            return
        if not self._drag:
            edge = self._edge_at(x)
            self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.ArrowCursor)
            return
        if not self._moved and abs(x - self._press_x) < self.DRAG_START:
            return
        self._moved = True
        started = time.perf_counter()
        self._apply(x, final=False)
        if perf_log.ENABLED:
            now = time.perf_counter()
            gap = (now - self._last_move) * 1000 if self._last_move else 0.0
            self._last_move = now
            perf_log.log(f"move   gap={gap:7.1f} ms  handler={(now - started) * 1000:6.2f} ms")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if not self._drag or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._moved:
            self._apply(event.position().x(), final=True)
        self._drag = None
        self._last_move = 0.0

    def _apply(self, x: float, final: bool) -> None:
        t = self._t(x)
        if self._drag == "seek":
            self.position = t
            self.update()
            self.seek_requested.emit(t, final)
        elif self._drag == "in":
            self.in_dragged.emit(t)
            self.edge_preview.emit(self.trim_in, final)
        elif self._drag == "out":
            self.out_dragged.emit(t)
            self.edge_preview.emit(self.trim_out, final)


# -------------------------------------------------------------- popover


class Popover(QFrame):
    """Floating panel; closes on click outside (Qt.Popup)."""

    def __init__(self, title: str, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setObjectName("Panel")
        outer.addWidget(self.panel)
        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(theme.GAP2, 12, theme.GAP2, theme.GAP2)
        self.body.setSpacing(6)
        header = dim_label(title)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(header)
        self.body.addSpacing(10)

    def popup_over(self, widget: QWidget) -> None:
        self.adjustSize()
        center = widget.mapToGlobal(widget.rect().center())
        self.move(center - QPoint(self.width() // 2, self.height() // 2))
        self.show()
        self.activateWindow()


# ---------------------------------------------------------------- toast


class Toast(QFrame):
    action_clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 6, 6)
        layout.setSpacing(theme.GAP2)
        self.label = QLabel()
        self.action = button("Show in Folder")
        self.action.clicked.connect(self.action_clicked)
        self.action.clicked.connect(self.hide)
        layout.addWidget(self.label)
        layout.addWidget(self.action)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, action: bool, error: bool = False, ms: int = 6000) -> None:
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {theme.ERROR_FG};" if error else "")
        self.action.setVisible(action)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def reposition(self) -> None:
        parent = self.parentWidget()
        anchor = getattr(parent, "toast_anchor", None)
        area = anchor() if anchor else parent.rect()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - theme.GAP2)
