"""Transparent launch intro animation shown before the main window appears."""

from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QElapsedTimer,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QImageReader, QLinearGradient, QMovie, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget


def _ease(curve_type: QEasingCurve.Type, progress: float) -> float:
    """Return a clamped eased value for the supplied progress."""
    clamped = max(0.0, min(1.0, progress))
    return QEasingCurve(curve_type).valueForProgress(clamped)


def _lerp(start: float, end: float, progress: float) -> float:
    """Linearly interpolate between two values."""
    return start + (end - start) * progress


def centered_window_rect(window: QWidget, screen=None) -> QRect:
    """Return a centered target rect for the supplied window on the given screen."""
    app = QApplication.instance()
    active_screen = screen or window.screen() or (app.primaryScreen() if app is not None else None)
    if active_screen is None:
        return QRect(QPoint(80, 80), window.size())

    full_geometry = active_screen.geometry()
    available_geometry = active_screen.availableGeometry()

    size = window.size()
    if size.isEmpty():
        size = window.sizeHint()
    if size.isEmpty():
        size = QSize(536, 548)

    target_rect = QRect(QPoint(0, 0), size)
    target_rect.moveCenter(full_geometry.center())

    max_x = max(available_geometry.left(), available_geometry.right() - size.width() + 1)
    max_y = max(available_geometry.top(), available_geometry.bottom() - size.height() + 1)
    x = min(max(target_rect.x(), available_geometry.left()), max_x)
    y = min(max(target_rect.y(), available_geometry.top()), max_y)
    return QRect(x, y, size.width(), size.height())


class LaunchIntroOverlay(QWidget):
    """Transparent cinematic overlay with a real splash and ripple-style UI reveal."""

    finished = pyqtSignal()

    def __init__(self, target_window: QWidget, screen=None):
        super().__init__(None)
        self.target_window = target_window
        app = QApplication.instance()
        self.screen = screen or target_window.screen() or (app.primaryScreen() if app is not None else None)
        if self.screen is None:
            raise RuntimeError("No available screen for launch intro.")

        self.screen_geometry = self.screen.geometry()
        self.target_rect_global = centered_window_rect(target_window, self.screen)
        self.target_rect_local = QRect(
            self.target_rect_global.topLeft() - self.screen_geometry.topLeft(),
            self.target_rect_global.size(),
        )

        self.target_center = QPointF(self.target_rect_local.center())
        self.motion_focus = QPointF(
            self.target_rect_local.center().x(),
            self.target_rect_local.bottom() - 188.0,
        )
        self.motion_bottom_anchor = self.target_rect_local.bottom() + 84.0

        self._finished = False
        self._motion_frozen = False
        self._frozen_motion_frame = QImage()
        self._current_motion_frame = QImage()
        self._motion_frame_cache: dict[int, QImage] = {}
        self._motion_frames = self._load_motion_frames()
        self._window_snapshot = QPixmap()
        self._reveal_snapshot_locked = False

        self._timeline = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self._motion_movie = None if self._motion_frames else self._build_motion_movie()
        self._motion_frame_count = len(self._motion_frames) if self._motion_frames else self._read_motion_frame_count()
        self._motion_frame_duration_ms = 18.0 if self._motion_frames else 16.0
        self.motion_duration_ms = max(460.0, (self._motion_frame_count * self._motion_frame_duration_ms) + 28.0)
        # Start the window-fill during the last half-second of the splash fade.
        self.window_reveal_start_ms = self.motion_duration_ms + 40.0
        self.motion_fade_start_ms = self.window_reveal_start_ms
        self.motion_fade_duration_ms = max(140.0, 1000.0 - self.motion_fade_start_ms)
        self.window_reveal_duration_ms = 320.0
        self.total_duration_ms = max(
            self.motion_fade_start_ms + self.motion_fade_duration_ms,
            self.window_reveal_start_ms + self.window_reveal_duration_ms,
        ) + 140.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setGeometry(self.screen_geometry)

    def _assets_dir(self) -> Path:
        """Return the intro assets directory."""
        return Path(__file__).resolve().parent.parent / "assets" / "launch_intro"

    def _motion_path(self) -> Path:
        """Return the real moving water intro asset path."""
        return self._assets_dir() / "intro_drop_crown.gif"

    def _motion_sequence_dir(self) -> Path:
        """Return the transparent intro sequence directory."""
        return self._assets_dir() / "intro_drop_sequence"

    def _load_motion_frames(self) -> list[QImage]:
        """Load the transparent intro frame sequence when available."""
        frames_dir = self._motion_sequence_dir()
        if not frames_dir.exists():
            return []

        frames: list[QImage] = []
        for frame_path in sorted(frames_dir.glob("frame_*.png")):
            image = QImage(str(frame_path))
            if image.isNull():
                continue
            frames.append(image)
        return frames

    def _read_motion_frame_count(self) -> int:
        """Return the number of frames in the motion GIF."""
        reader = QImageReader(str(self._motion_path()))
        if reader.canRead():
            image_count = reader.imageCount()
            if image_count > 0:
                return image_count
        return 45

    def _build_motion_movie(self) -> QMovie | None:
        """Build the real water motion player."""
        motion_path = self._motion_path()
        if not motion_path.exists():
            return None

        movie = QMovie(str(motion_path))
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.frameChanged.connect(self._on_motion_frame_changed)
        return movie

    def _update_motion_sequence_frame(self, elapsed: float) -> None:
        """Advance the transparent frame sequence based on elapsed time."""
        if not self._motion_frames:
            return

        frame_index = min(
            self._motion_frame_count - 1,
            max(0, int(elapsed / max(1.0, self._motion_frame_duration_ms))),
        )
        self._current_motion_frame = self._motion_frames[frame_index]

        if frame_index >= self._motion_frame_count - 1 and not self._motion_frozen:
            self._frozen_motion_frame = self._current_motion_frame.copy()
            self._motion_frozen = True

    def _on_motion_frame_changed(self, frame_number: int) -> None:
        """Process and freeze the motion asset frame instead of looping."""
        if self._motion_movie is None:
            return

        self._current_motion_frame = self._motion_frame_cache.get(frame_number, QImage())
        if self._current_motion_frame.isNull():
            processed = self._matte_motion_frame(self._motion_movie.currentImage())
            self._motion_frame_cache[frame_number] = processed
            self._current_motion_frame = processed

        if frame_number >= self._motion_frame_count - 1 and not self._motion_frozen:
            final_image = self._current_motion_frame
            if not final_image.isNull():
                self._frozen_motion_frame = final_image.copy()
            self._motion_movie.stop()
            self._motion_frozen = True

        self.update()

    def start(self) -> None:
        """Show the overlay and begin the cinematic intro."""
        self.target_window.setGeometry(self.target_rect_global)
        self.target_window.move(self.target_rect_global.topLeft())
        self.target_window.setWindowOpacity(0.0)
        self.target_window.show()

        self.show()
        self.activateWindow()
        self.raise_()
        QApplication.processEvents()
        self._refresh_window_snapshot()
        self._reveal_snapshot_locked = not self._window_snapshot.isNull()
        self._timeline.start()
        if self._motion_frames:
            self._current_motion_frame = self._motion_frames[0]
        elif self._motion_movie is not None:
            self._motion_movie.start()
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        """Advance the intro and reveal the main window at the right time."""
        elapsed = float(self._timeline.elapsed())

        if self._motion_frames:
            self._update_motion_sequence_frame(elapsed)
        elif not self._motion_frozen and elapsed >= self.motion_duration_ms and self._motion_movie is not None:
            frozen = self._current_motion_frame
            if not frozen.isNull():
                self._frozen_motion_frame = frozen.copy()
            self._motion_movie.stop()
            self._motion_frozen = True

        if elapsed >= self.total_duration_ms:
            self._finish_intro()
            return

        if elapsed >= self.window_reveal_start_ms:
            if not self._reveal_snapshot_locked:
                self._refresh_window_snapshot()
                self._reveal_snapshot_locked = True
            reveal_progress = self._window_reveal_progress(elapsed)
            final_window_opacity = max(0.0, (reveal_progress - 0.88) / 0.12)
            self.target_window.setWindowOpacity(min(1.0, final_window_opacity))

        self.update()

    def _finish_intro(self) -> None:
        """Tear down the overlay and leave the main window fully visible."""
        if self._finished:
            return

        self._finished = True
        self._timer.stop()
        if self._motion_movie is not None:
            self._motion_movie.stop()
        self.target_window.setWindowOpacity(1.0)
        self.target_window.move(self.target_rect_global.topLeft())
        self.target_window.raise_()
        self.target_window.activateWindow()
        self.hide()
        self.finished.emit()
        self.deleteLater()

    def _phase_progress(
        self,
        elapsed: float,
        start_ms: float,
        duration_ms: float,
        curve_type: QEasingCurve.Type = QEasingCurve.Type.InOutCubic,
    ) -> float:
        """Return eased progress for a timeline phase."""
        return _ease(curve_type, (elapsed - start_ms) / max(1.0, duration_ms))

    def _window_reveal_progress(self, elapsed: float) -> float:
        """Return the progress for the window reveal sweep."""
        return self._phase_progress(
            elapsed,
            self.window_reveal_start_ms,
            self.window_reveal_duration_ms,
            QEasingCurve.Type.OutCubic,
        )

    def _matte_motion_frame(self, image: QImage) -> QImage:
        """Boost the water highlights while keeping near-black backgrounds transparent."""
        if image.isNull():
            return image

        frame = image.convertToFormat(QImage.Format.Format_RGBA8888)
        bits = frame.bits()
        if bits is None:
            return frame

        byte_count = frame.width() * frame.height() * 4
        bits.setsize(byte_count)
        data = memoryview(bits)
        low_cut = 2
        lift = 1.9
        cyan_lift = 1.16

        for index in range(0, byte_count, 4):
            red = data[index]
            green = data[index + 1]
            blue = data[index + 2]
            value = max(red, green, blue)

            if value <= low_cut:
                alpha = 0
            else:
                alpha = 255

            if alpha > 0:
                boosted_red = min(255, int(red * lift))
                boosted_green = min(255, int(green * lift * cyan_lift))
                boosted_blue = min(255, int(blue * lift * cyan_lift))
                data[index] = boosted_red
                data[index + 1] = boosted_green
                data[index + 2] = boosted_blue

            data[index + 3] = alpha

        return frame

    def _draw_asset(
        self,
        painter: QPainter,
        image: QImage,
        *,
        width: float,
        bottom_anchor: float,
        opacity: float,
        scale: float = 1.0,
        y_shift: float = 0.0,
        x_shift: float = 0.0,
    ) -> None:
        """Draw a photoreal asset with screen-style compositing."""
        if image.isNull() or opacity <= 0.001:
            return

        scaled_width = width * scale
        aspect = image.height() / max(1.0, float(image.width()))
        height = scaled_width * aspect
        rect = QRectF(
            self.target_center.x() - scaled_width * 0.5 + x_shift,
            bottom_anchor - height + y_shift,
            scaled_width,
            height,
        )

        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.drawImage(rect, image)
        painter.restore()

    def _refresh_window_snapshot(self) -> None:
        """Capture the current window appearance for the ripple reveal."""
        snapshot = self.target_window.grab()
        if not snapshot.isNull():
            self._window_snapshot = snapshot

    def _draw_motion_phase(self, painter: QPainter, elapsed: float) -> None:
        """Draw the real moving water asset."""
        motion_image = self._frozen_motion_frame if self._motion_frozen else QImage()
        if motion_image.isNull():
            motion_image = self._current_motion_frame
        if motion_image.isNull():
            return

        motion_progress = self._phase_progress(elapsed, 0.0, self.motion_duration_ms, QEasingCurve.Type.OutCubic)
        fade_progress = self._phase_progress(
            elapsed,
            self.motion_fade_start_ms,
            self.motion_fade_duration_ms,
            QEasingCurve.Type.OutQuad,
        )
        opacity = max(0.0, 1.0 - fade_progress)
        width = max(1380.0, float(self.target_rect_local.width()) * 2.22)
        scale = _lerp(1.12, 1.03, motion_progress)
        y_shift = _lerp(-18.0, 22.0, motion_progress)
        x_shift = 0.0

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        self._draw_asset(
            painter,
            motion_image,
            width=width,
            bottom_anchor=self.motion_bottom_anchor,
            opacity=opacity,
            scale=scale,
            y_shift=y_shift,
            x_shift=x_shift,
        )
        painter.restore()

    def _wave_edge_y(self, x: float, progress: float, elapsed: float) -> float:
        """Return the current ripple reveal edge at the supplied x-position."""
        rect = QRectF(self.target_rect_local)
        clamped_progress = max(0.0, min(1.0, progress))
        normalized_x = 0.0 if rect.width() <= 0 else (x - rect.left()) / rect.width()
        base_y = rect.bottom() - clamped_progress * (rect.height() + 56.0) + 24.0
        amplitude = _lerp(22.0, 5.5, clamped_progress)
        phase = clamped_progress * 10.4 + (elapsed * 0.0068)
        secondary_phase = clamped_progress * 6.8 - (elapsed * 0.0034)
        tertiary_phase = clamped_progress * 4.1 + (elapsed * 0.0022)
        primary = math.sin((normalized_x * math.tau * 2.1) - phase) * amplitude
        secondary = math.sin((normalized_x * math.tau * 4.6) + secondary_phase) * amplitude * 0.26
        tertiary = math.sin((normalized_x * math.tau * 1.05) + tertiary_phase) * amplitude * 0.16
        vertical_bob = math.sin((elapsed * 0.0054) + (normalized_x * math.tau * 0.6)) * amplitude * (0.16 * (1.0 - clamped_progress))
        return base_y + primary + secondary + tertiary + vertical_bob

    def _build_surface_path(self, progress: float, elapsed: float) -> QPainterPath:
        """Build the current water surface path."""
        rect = QRectF(self.target_rect_local)
        clamped_progress = max(0.0, min(1.0, progress))
        wave_samples = max(18, int(rect.width() / 20.0))
        path = QPainterPath()
        path.moveTo(rect.left(), self._wave_edge_y(rect.left(), clamped_progress, elapsed))
        for sample in range(1, wave_samples + 1):
            x = rect.left() + (rect.width() * sample / wave_samples)
            path.lineTo(x, self._wave_edge_y(x, clamped_progress, elapsed))
        return path

    def _build_reveal_path(self, progress: float, elapsed: float) -> QPainterPath:
        """Build the current wave-shaped reveal mask."""
        rect = QRectF(self.target_rect_local)
        surface_path = self._build_surface_path(progress, elapsed)
        path = QPainterPath(surface_path)
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
        return path

    def _draw_reveal_band(
        self,
        painter: QPainter,
        path: QPainterPath,
        *,
        opacity: float,
        y_offset: float = 0.0,
        x_offset: float = 0.0,
    ) -> None:
        """Draw one ripple band of the window reveal."""
        if self._window_snapshot.isNull() or opacity <= 0.001:
            return

        painter.save()
        painter.setOpacity(opacity)
        painter.setClipPath(path)
        if abs(x_offset) > 0.001 or abs(y_offset) > 0.001:
            painter.translate(x_offset, y_offset)
        painter.drawPixmap(self.target_rect_local, self._window_snapshot)
        painter.restore()

    def _draw_window_ripple_reveal(self, painter: QPainter, elapsed: float) -> None:
        """Reveal the real UI as if the window is filling upward with water."""
        if self._window_snapshot.isNull() or elapsed < self.window_reveal_start_ms:
            return

        rect = QRectF(self.target_rect_local)
        progress = self._window_reveal_progress(elapsed)
        soft_progress = _ease(QEasingCurve.Type.InOutSine, progress)
        fill_strength = 1.0 - soft_progress
        reveal_opacity = 0.10 + (0.72 * soft_progress)
        lead_path = self._build_reveal_path(progress, elapsed)
        mid_path = self._build_reveal_path(max(0.0, progress - 0.10), elapsed - 26.0)
        deep_path = self._build_reveal_path(max(0.0, progress - 0.22), elapsed - 52.0)
        horizontal_drift = math.sin(elapsed * 0.0061) * (1.25 * (1.0 - progress))

        self._draw_reveal_band(
            painter,
            deep_path,
            opacity=reveal_opacity * 0.78,
            y_offset=0.0,
            x_offset=0.0,
        )
        self._draw_reveal_band(
            painter,
            mid_path,
            opacity=reveal_opacity * 0.38,
            y_offset=2.8 * fill_strength,
            x_offset=horizontal_drift * 0.55,
        )
        self._draw_reveal_band(
            painter,
            lead_path,
            opacity=reveal_opacity * 0.18,
            y_offset=6.2 * fill_strength,
            x_offset=-horizontal_drift,
        )

        wave_center_y = self._wave_edge_y(rect.center().x(), progress, elapsed)
        gradient = QLinearGradient(
            QPointF(rect.center().x(), wave_center_y - 10.0),
            QPointF(rect.center().x(), rect.bottom()),
        )
        gradient.setColorAt(0.0, QColor(132, 238, 255, int(56 * fill_strength)))
        gradient.setColorAt(0.16, QColor(52, 144, 196, int(62 * fill_strength)))
        gradient.setColorAt(0.52, QColor(16, 50, 78, int(54 * fill_strength)))
        gradient.setColorAt(1.0, QColor(8, 18, 34, int(38 * fill_strength)))

        painter.save()
        painter.setClipPath(lead_path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(rect)
        painter.restore()

        if progress <= 0.0:
            return

        surface_path = self._build_surface_path(progress, elapsed)
        surface_strength = 1.0 - min(1.0, progress * 0.88)
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(74, 214, 255, int(34 * surface_strength)),
                _lerp(4.0, 1.6, progress),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(surface_path)
        painter.setPen(
            QPen(
                QColor(232, 248, 255, int(24 * surface_strength)),
                _lerp(1.8, 0.8, progress),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(surface_path)
        painter.restore()

    def paintEvent(self, event) -> None:
        """Paint the transparent cinematic intro over the live desktop."""
        del event
        elapsed = float(self._timeline.elapsed()) if self._timeline.isValid() else 0.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self._draw_motion_phase(painter, elapsed)
        self._draw_window_ripple_reveal(painter, elapsed)

        painter.end()

    def mousePressEvent(self, event) -> None:
        """Allow a click to skip the intro if the user does not feel patient today."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._finish_intro()
            event.accept()
            return
        super().mousePressEvent(event)


def play_launch_intro(window: QWidget, screen=None) -> LaunchIntroOverlay:
    """Create and start the launch intro for the supplied window."""
    overlay = LaunchIntroOverlay(window, screen=screen)
    overlay.start()
    return overlay
