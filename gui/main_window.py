"""
Main GUI window module using PyQt6.

Security update:
- Credentials are saved only after Lexis login succeeds.
- Password storage is delegated to config.credentials, which should use the OS keyring.
- Plaintext credentials.json is no longer used.
"""

from __future__ import annotations

import math
import random
import traceback
from datetime import date, timedelta
from pathlib import Path

from PyQt6.QtCore import QDate, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QCursor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
    QShortcut,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDialog,
    QDateEdit,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config.app_settings import (
    HEADER_FILL_COLOR_KEY,
    RECIPIENT_OVERRIDE_CC_KEY,
    RECIPIENT_OVERRIDE_TO_KEY,
    SOURCE_MODE_KEY,
    load_setting,
    remove_setting,
    save_setting,
)
from config.credentials import (
    clear_credentials,
    is_keyring_available,
    load_credentials,
    save_credentials,
)
from utils.bubble_audio import BubblePopAudio


class AnimatedProgressBar(QProgressBar):
    """Progress bar with sheen loading animation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sheen_position = 0
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_sheen)
        self.animation_timer.start(20)

    def update_sheen(self):
        """Update the sheen animation position."""
        if self.maximum() > 0 and self.value() > 0:
            progress_width = int((self.value() / self.maximum()) * self.width())
            max_position = max(progress_width + 100, 200)
            self.sheen_position = (self.sheen_position + 5) % max_position
        else:
            self.sheen_position = 0
        self.update()

    def paintEvent(self, event):
        """Custom paint event for sheen animation."""
        super().paintEvent(event)

        if self.value() > 0 and self.maximum() > 0:
            progress_ratio = self.value() / self.maximum()
            progress_width = int(progress_ratio * self.width())

            if progress_width > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)

                filled_rect = self.rect()
                filled_rect.setWidth(progress_width)

                gradient = QLinearGradient(
                    self.sheen_position - 50,
                    0,
                    self.sheen_position + 50,
                    0,
                )
                gradient.setColorAt(0, QColor(255, 255, 255, 0))
                gradient.setColorAt(0.5, QColor(255, 255, 255, 100))
                gradient.setColorAt(1, QColor(255, 255, 255, 0))

                painter.fillRect(filled_rect, gradient)


class BubbleOverlay(QWidget):
    """Soft drifting bubbles that animate behind the idle controls."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._bubbles: list[dict[str, object]] = []
        self._pop_effects: list[dict[str, object]] = []
        self._pop_audio_callback = None
        self._held_bubble: dict[str, object] | None = None
        self._burst_ticks_remaining = 0
        self._cooldown_ticks_remaining = random.randint(34, 88)
        self._quiet_ticks_remaining = random.randint(14, 30)
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setInterval(38)
        self._bubble_timer.timeout.connect(self._tick_bubbles)
        self._bubble_timer.stop()

    def showEvent(self, event) -> None:
        """Animate only while the idle bubble layer is visible."""
        if not self._bubble_timer.isActive():
            self._bubble_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        """Sleep the bubble animation whenever the layer is hidden."""
        self.cancel_hold_bubble()
        self._bubble_timer.stop()
        super().hideEvent(event)

    def set_pop_audio_callback(self, callback) -> None:
        """Register one callback for size-aware pop audio playback."""
        self._pop_audio_callback = callback

    def _bubble_palette(self) -> tuple[QColor, ...]:
        """Return the shared idle-water palette."""
        return (
            QColor("#7DD3FC"),
            QColor("#A5F3FC"),
            QColor("#BAE6FD"),
            QColor("#C4B5FD"),
            QColor("#E0F2FE"),
        )

    def _spawn_bubble(self) -> None:
        if self.width() <= 48 or self.height() <= 48:
            return

        palette = self._bubble_palette()
        is_micro = random.random() < 0.74
        depth = random.uniform(0.0, 1.0)
        if is_micro:
            radius = random.uniform(0.9, 2.3) * (0.9 + depth * 0.18)
            alpha = random.uniform(0.12, 0.21) * (0.84 + depth * 0.18)
        else:
            radius = random.uniform(5.0, 10.4) * (0.84 + depth * 0.42)
            alpha = random.uniform(0.2, 0.31) * (0.86 + depth * 0.3)
        spawn_y = random.uniform(self.height() * 0.985, self.height() * 1.055)
        cluster_bias = random.random()
        if is_micro:
            if cluster_bias < 0.44:
                origin_x = (self.width() * 0.5) + random.gauss(0.0, self.width() * 0.085)
            elif cluster_bias < 0.84:
                side = -1.0 if random.random() < 0.5 else 1.0
                origin_x = (self.width() * 0.5) + side * random.uniform(
                    self.width() * 0.16,
                    self.width() * 0.34,
                )
            else:
                origin_x = random.uniform(self.width() * 0.08, self.width() * 0.92)
        elif cluster_bias < 0.7:
            origin_x = (self.width() * 0.5) + random.gauss(0.0, self.width() * 0.07)
        elif cluster_bias < 0.92:
            side = -1.0 if random.random() < 0.5 else 1.0
            origin_x = (self.width() * 0.5) + side * random.uniform(
                self.width() * 0.14,
                self.width() * 0.26,
            )
        else:
            origin_x = random.uniform(self.width() * 0.14, self.width() * 0.86)
        origin_x = min(self.width() * 0.88, max(self.width() * 0.12, origin_x))
        drift_center = (origin_x - (self.width() * 0.5)) / max(1.0, self.width() * 0.5)
        if is_micro:
            lateral_drift = drift_center * random.uniform(0.08, 0.22)
            base_vx = random.uniform(-0.14, 0.14) * (0.82 + depth * 0.22)
            rise_vy = random.uniform(-1.26, -0.76) * (0.9 + depth * 0.22)
            sway = random.uniform(0.034, 0.082) * (0.9 + depth * 0.26)
            life = random.uniform(360.0, 600.0)
            glow_scale = 1.28 + depth * 0.24
            highlight_scale = random.uniform(0.12, 0.2)
        else:
            lateral_drift = drift_center * random.uniform(0.05, 0.16)
            base_vx = random.uniform(-0.12, 0.12) * (0.76 + depth * 0.28)
            rise_vy = random.uniform(-1.84, -1.08) * (0.88 + depth * 0.32)
            sway = random.uniform(0.024, 0.06) * (0.86 + depth * 0.42)
            life = random.uniform(260.0, 410.0)
            glow_scale = 1.8 + depth * 0.55
            highlight_scale = random.uniform(0.2, 0.3)
        color = QColor(random.choice(palette))
        self._bubbles.append(
            {
                "x": origin_x,
                "y": spawn_y,
                "start_y": spawn_y,
                "vx": base_vx + lateral_drift,
                "vy": rise_vy,
                "radius": radius,
                "life": life,
                "max_life": 0.0,
                "phase": random.uniform(0.0, math.tau),
                "alpha": alpha,
                "depth": depth,
                "sway": sway,
                "glow_scale": glow_scale,
                "highlight_scale": highlight_scale,
                "impulse_x": 0.0,
                "impulse_y": 0.0,
                "micro": is_micro,
                "color": color,
            }
        )
        self._bubbles[-1]["max_life"] = self._bubbles[-1]["life"]

    def _schedule_next_burst(self) -> None:
        self._cooldown_ticks_remaining = random.randint(54, 116)
        self._quiet_ticks_remaining = random.randint(14, 30)
        self._burst_ticks_remaining = 0

    def _start_burst(self) -> None:
        self._burst_ticks_remaining = random.randint(9, 16)
        self._cooldown_ticks_remaining = 0
        self._quiet_ticks_remaining = 0

    def prime_bubbles(self) -> None:
        """Kick off a visible first wave once the overlay has real geometry."""
        if self._bubbles or self.width() <= 48 or self.height() <= 48:
            return
        self._start_burst()
        for _ in range(random.randint(10, 14)):
            self._spawn_bubble()

    def begin_hold_bubble(self, x: float, y: float) -> bool:
        """Start growing a bubble at the pressed idle-background point."""
        if self.width() <= 48 or self.height() <= 48:
            return False

        clamped_x = min(self.width() - 6.0, max(6.0, x))
        clamped_y = min(self.height() - 8.0, max(8.0, y))
        color = QColor(random.choice(self._bubble_palette()))
        depth = random.uniform(0.1, 0.9)
        self._held_bubble = {
            "x": clamped_x,
            "y": clamped_y,
            "radius": 1.45,
            "max_radius": 12.8,
            "growth": 0.34,
            "life": 0,
            "alpha": 0.22,
            "depth": depth,
            "glow_scale": 1.32 + depth * 0.22,
            "highlight_scale": random.uniform(0.16, 0.24),
            "color": color,
        }
        self.update()
        return True

    def has_hold_bubble(self) -> bool:
        """Return True while the user is growing a bubble on hold."""
        return self._held_bubble is not None

    def cancel_hold_bubble(self) -> None:
        """Drop the current held bubble preview without spawning it."""
        if self._held_bubble is None:
            return
        self._held_bubble = None
        self.update()

    def release_hold_bubble(self) -> bool:
        """Convert the held preview into a real rising bubble."""
        if self._held_bubble is None:
            return False

        held = self._held_bubble
        self._held_bubble = None

        radius = float(held["radius"])
        depth = float(held["depth"])
        is_micro = radius < 3.2
        drift_center = (float(held["x"]) - (self.width() * 0.5)) / max(1.0, self.width() * 0.5)
        if is_micro:
            base_vx = random.uniform(-0.12, 0.12) * (0.82 + depth * 0.24)
            rise_vy = random.uniform(-1.18, -0.78) * (0.92 + depth * 0.18)
            sway = random.uniform(0.034, 0.072) * (0.9 + depth * 0.22)
            life = random.uniform(260.0, 420.0)
            glow_scale = 1.26 + depth * 0.2
            highlight_scale = random.uniform(0.13, 0.2)
            alpha = min(0.24, 0.11 + radius * 0.032)
        else:
            base_vx = random.uniform(-0.1, 0.1) * (0.78 + depth * 0.22)
            rise_vy = random.uniform(-1.72, -1.05) * (0.9 + depth * 0.26)
            sway = random.uniform(0.024, 0.056) * (0.88 + depth * 0.34)
            life = random.uniform(220.0, 340.0)
            glow_scale = 1.72 + depth * 0.44
            highlight_scale = random.uniform(0.2, 0.28)
            alpha = min(0.33, 0.17 + radius * 0.013)

        self._bubbles.append(
            {
                "x": float(held["x"]),
                "y": float(held["y"]),
                "start_y": float(held["y"]),
                "vx": base_vx + drift_center * random.uniform(0.03, 0.1),
                "vy": rise_vy,
                "radius": radius,
                "life": life,
                "max_life": life,
                "phase": random.uniform(0.0, math.tau),
                "alpha": alpha,
                "depth": depth,
                "sway": sway,
                "glow_scale": glow_scale,
                "highlight_scale": highlight_scale,
                "impulse_x": 0.0,
                "impulse_y": 0.0,
                "micro": is_micro,
                "color": QColor(held["color"]),
            }
        )
        self.update()
        return True

    def _hit_test_bubble(self, x: float, y: float) -> int | None:
        """Return the topmost bubble index under the given point, if any."""
        for index in range(len(self._bubbles) - 1, -1, -1):
            bubble = self._bubbles[index]
            bubble_x = float(bubble["x"])
            bubble_y = float(bubble["y"])
            radius = float(bubble["radius"])
            hit_radius = max(5.0, radius * (1.5 if bool(bubble.get("micro")) else 1.22))
            if math.hypot(x - bubble_x, y - bubble_y) <= hit_radius:
                return index
        return None

    def _spawn_pop_effect(self, bubble: dict[str, object]) -> None:
        """Create a short-lived, underwater-style collapse with wake and daughter bubbles."""
        x = float(bubble["x"])
        y = float(bubble["y"])
        radius = float(bubble["radius"])
        color = QColor(bubble["color"])
        micro = bool(bubble.get("micro"))
        large_pop = (not micro) and radius >= 6.4
        compression_color = QColor("#E6FCFF")
        compression_color.setAlphaF(0.08 if micro else 0.11)
        self._pop_effects.append(
            {
                "kind": "compression",
                "x": x,
                "y": y,
                "radius_x": radius * (0.34 if micro else 0.42),
                "radius_y": radius * (0.22 if micro else 0.28),
                "grow_x": 0.42 if micro else 0.56,
                "grow_y": 0.22 if micro else 0.3,
                "vy": -0.015 if micro else -0.028,
                "life": 9.0 if micro else 11.0,
                "max_life": 9.0 if micro else 11.0,
                "color": compression_color,
                "base_alpha": compression_color.alphaF(),
            }
        )
        snap_color = QColor("#F8FDFF")
        snap_color.setAlphaF(0.18 if micro else 0.24)
        self._pop_effects.append(
            {
                "kind": "snap",
                "x": x,
                "y": y,
                "radius_x": radius * (0.26 if micro else 0.32),
                "radius_y": radius * (0.22 if micro else 0.28),
                "shrink_x": 0.55 if micro else 0.68,
                "shrink_y": 0.44 if micro else 0.56,
                "line_width": max(0.35, radius * 0.055),
                "life": 7.0 if micro else 8.0,
                "max_life": 7.0 if micro else 8.0,
                "color": snap_color,
                "base_alpha": snap_color.alphaF(),
            }
        )
        if large_pop and random.random() < min(0.92, 0.26 + radius * 0.065):
            wake_color = QColor("#A5F3FC")
            wake_color.setAlphaF(0.095 if radius < 8.2 else 0.12)
            for direction in (-1.0, 1.0):
                self._pop_effects.append(
                    {
                        "kind": "wake",
                        "x": x + direction * radius * 0.2,
                        "y": y + radius * 0.05,
                        "vx": direction * 0.12,
                        "vy": -0.09,
                        "radius_x": radius * 0.3,
                        "radius_y": radius * 0.17,
                        "grow_x": 0.24,
                        "grow_y": 0.13,
                        "life": 14.0,
                        "max_life": 14.0,
                        "color": wake_color,
                        "base_alpha": wake_color.alphaF(),
                    }
                )
        ripple_color = QColor("#DDFBFF")
        ripple_color.setAlphaF(0.0)
        self._pop_effects.append(
            {
                "kind": "ripple",
                "x": x,
                "y": y + radius * 0.04,
                "radius_x": radius * (0.34 if micro else 0.42),
                "radius_y": radius * (0.18 if micro else 0.22),
                "grow_x": 0.28 if micro else 0.38,
                "grow_y": 0.12 if micro else 0.18,
                "line_width": max(0.2, radius * 0.02),
                "life": 11.0 if micro else 13.0,
                "max_life": 11.0 if micro else 13.0,
                "source_radius": radius,
                "wave_amplitude": (0.03 + radius * 0.0048) if micro else (0.052 + radius * 0.0085),
                "color": ripple_color,
                "base_alpha": ripple_color.alphaF(),
            }
        )

        fragment_count = 8 if micro else (14 if large_pop else 10)
        for _ in range(fragment_count):
            fragment_color = QColor("#DDF8FF")
            fragment_color.setAlphaF(0.14 if micro else 0.2)
            angle = (-math.pi / 2.0) + random.gauss(0.0, 0.9)
            speed = random.uniform(0.08, 0.22) if micro else random.uniform(0.12, 0.34)
            self._pop_effects.append(
                {
                    "kind": "fragment",
                    "x": x,
                    "y": y,
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed - (0.24 if micro else 0.3),
                    "radius": max(0.18, radius * (0.026 if micro else 0.036)),
                    "life": 13.0 if micro else 15.0,
                    "max_life": 13.0 if micro else 15.0,
                    "color": fragment_color,
                    "base_alpha": fragment_color.alphaF(),
                }
            )

    def emit_click_puff(self, x: float, y: float) -> None:
        """Release a tiny local puff of microbubbles from an idle-background click."""
        if self.width() <= 0 or self.height() <= 0:
            return

        palette = (
            QColor("#7DD3FC"),
            QColor("#A5F3FC"),
            QColor("#BAE6FD"),
            QColor("#E0F2FE"),
        )
        count = random.randint(7, 11)
        for _ in range(count):
            depth = random.uniform(0.0, 1.0)
            radius = random.uniform(0.48, 1.32) * (0.9 + depth * 0.12)
            alpha = random.uniform(0.08, 0.15) * (0.82 + depth * 0.12)
            bubble_x = min(
                self.width() - radius,
                max(radius, x + random.gauss(0.0, 8.4)),
            )
            bubble_y = min(
                self.height() + radius * 0.8,
                max(radius, y + random.gauss(0.0, 4.1)),
            )
            drift_center = (bubble_x - (self.width() * 0.5)) / max(1.0, self.width() * 0.5)
            lateral_drift = drift_center * random.uniform(0.04, 0.14)
            color = QColor(random.choice(palette))
            self._bubbles.append(
                {
                    "x": bubble_x,
                    "y": bubble_y,
                    "start_y": bubble_y,
                    "vx": random.uniform(-0.16, 0.16) * (0.84 + depth * 0.18) + lateral_drift,
                    "vy": random.uniform(-1.22, -0.78) * (0.9 + depth * 0.2),
                    "radius": radius,
                    "life": random.uniform(160.0, 260.0),
                    "max_life": 0.0,
                    "phase": random.uniform(0.0, math.tau),
                    "alpha": alpha,
                    "depth": depth,
                    "sway": random.uniform(0.032, 0.075) * (0.88 + depth * 0.2),
                    "glow_scale": 1.22 + depth * 0.18,
                    "highlight_scale": random.uniform(0.1, 0.17),
                    "impulse_x": 0.0,
                    "impulse_y": 0.0,
                    "micro": True,
                    "color": color,
                }
            )
            self._bubbles[-1]["max_life"] = self._bubbles[-1]["life"]

    def _apply_pop_impulse(self, popped_bubble: dict[str, object]) -> None:
        """Push nearby bubbles outward briefly so the water feels reactive."""
        source_x = float(popped_bubble["x"])
        source_y = float(popped_bubble["y"])
        source_radius = float(popped_bubble["radius"])
        source_micro = bool(popped_bubble.get("micro"))
        influence_radius = max(18.0, source_radius * (4.5 if source_micro else 6.2))
        radius_energy = source_radius ** (1.1 if source_micro else 1.22)
        base_impulse = (0.12 if source_micro else 0.18) + radius_energy * (
            0.026 if source_micro else 0.038
        )

        for bubble in self._bubbles:
            dx = float(bubble["x"]) - source_x
            dy = float(bubble["y"]) - source_y
            distance = math.hypot(dx, dy)
            if distance <= 0.001 or distance > influence_radius:
                continue

            proximity = 1.0 - (distance / influence_radius)
            direction_x = dx / distance
            direction_y = dy / distance
            target_radius = float(bubble["radius"])
            target_micro = bool(bubble.get("micro"))
            size_drag = 1.0 / max(0.75, 0.68 + target_radius * 0.11)
            impulse_strength = base_impulse * proximity * size_drag
            lateral_emphasis = 0.86 + abs(direction_x) * 0.42
            vertical_bias = -proximity * (0.04 if target_micro else 0.055)

            bubble["impulse_x"] = float(bubble.get("impulse_x", 0.0)) + (
                direction_x * impulse_strength * lateral_emphasis
            )
            bubble["impulse_y"] = float(bubble.get("impulse_y", 0.0)) + (
                direction_y * impulse_strength * 0.24 + vertical_bias
            )
            bubble["sway"] = min(
                0.14 if target_micro else 0.1,
                float(bubble["sway"]) * (1.0 + proximity * 0.12),
            )
            bubble["phase"] = float(bubble["phase"]) + proximity * 0.55

    def _ripple_wave_offset(
        self,
        bubble: dict[str, object],
        ripple_effects: list[dict[str, object]],
    ) -> tuple[float, float]:
        """Return the summed ripple-wave displacement at one bubble location."""
        if not ripple_effects:
            return 0.0, 0.0

        bubble_x = float(bubble["x"])
        bubble_y = float(bubble["y"])
        total_x = 0.0
        total_y = 0.0

        for ripple in ripple_effects:
            dx = bubble_x - float(ripple["x"])
            dy = bubble_y - float(ripple["y"])
            distance = math.hypot(dx, dy)
            if distance <= 0.001:
                continue

            ripple_radius = (float(ripple["radius_x"]) + float(ripple["radius_y"])) * 0.5
            source_radius = float(ripple.get("source_radius", ripple_radius))
            band_width = max(5.5, source_radius * 0.72)
            delta = distance - ripple_radius
            max_delta = band_width * 1.35
            if abs(delta) > max_delta:
                continue

            life_ratio = float(ripple["life"]) / max(1.0, float(ripple["max_life"]))
            envelope = math.cos((abs(delta) / max_delta) * (math.pi / 2.0)) ** 2
            phase = (delta / band_width) * math.pi
            signed_amplitude = float(ripple.get("wave_amplitude", 0.0)) * life_ratio * envelope * math.cos(phase)
            direction_x = dx / distance
            direction_y = dy / distance

            total_x += direction_x * signed_amplitude
            total_y += direction_y * signed_amplitude * 0.55

        return total_x, total_y

    def try_pop_at(self, x: float, y: float) -> bool:
        """Pop the topmost bubble at the given overlay-local coordinates."""
        hit_index = self._hit_test_bubble(x, y)
        if hit_index is None:
            return False

        bubble = self._bubbles.pop(hit_index)
        self._spawn_pop_effect(bubble)
        self._apply_pop_impulse(bubble)
        if self._pop_audio_callback is not None:
            try:
                self._pop_audio_callback(bubble)
            except Exception:
                pass
        self.update()
        return True

    def mousePressEvent(self, event) -> None:
        """Pop bubbles on click while letting untouched areas behave normally."""
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return

        if not self.try_pop_at(
            event.position().x(),
            event.position().y(),
        ):
            event.ignore()
            return

        event.accept()

    def _tick_bubbles(self) -> None:
        if not self.isVisible():
            return

        if self._held_bubble is not None:
            self._held_bubble["life"] = int(self._held_bubble["life"]) + 1
            self._held_bubble["radius"] = min(
                float(self._held_bubble["max_radius"]),
                float(self._held_bubble["radius"]) + float(self._held_bubble["growth"]),
            )
            held_progress = float(self._held_bubble["radius"]) / max(
                1.0, float(self._held_bubble["max_radius"])
            )
            self._held_bubble["alpha"] = min(0.34, 0.14 + held_progress * 0.18)

        ripple_effects = [
            effect for effect in self._pop_effects if effect["kind"] == "ripple"
        ]
        active_bubbles: list[dict[str, object]] = []
        for bubble in self._bubbles:
            age = float(bubble["max_life"]) - float(bubble["life"])
            impulse_x = float(bubble.get("impulse_x", 0.0))
            impulse_y = float(bubble.get("impulse_y", 0.0))
            ripple_x, ripple_y = self._ripple_wave_offset(bubble, ripple_effects)
            bubble["x"] = (
                float(bubble["x"])
                + float(bubble["vx"])
                + impulse_x
                + ripple_x
                + math.sin(age * 0.045 + float(bubble["phase"])) * float(bubble["sway"]) * 7.2
            )
            bubble["y"] = float(bubble["y"]) + float(bubble["vy"]) + impulse_y + ripple_y
            bubble["life"] = float(bubble["life"]) - 1.0
            bubble["impulse_x"] = impulse_x * 0.84
            bubble["impulse_y"] = impulse_y * 0.84
            if abs(float(bubble["impulse_x"])) < 0.002:
                bubble["impulse_x"] = 0.0
            if abs(float(bubble["impulse_y"])) < 0.002:
                bubble["impulse_y"] = 0.0
            radius = float(bubble["radius"])
            if (
                float(bubble["life"]) > 0.0
                and -radius * 1.5 <= float(bubble["x"]) <= self.width() + radius * 1.5
                and float(bubble["y"]) >= -(radius * 2.4)
            ):
                active_bubbles.append(bubble)

        self._bubbles = active_bubbles

        active_effects: list[dict[str, object]] = []
        for effect in self._pop_effects:
            effect["life"] = float(effect["life"]) - 1.0
            if float(effect["life"]) <= 0.0:
                continue

            if effect["kind"] == "compression":
                effect["radius_x"] = float(effect["radius_x"]) + float(effect["grow_x"])
                effect["radius_y"] = float(effect["radius_y"]) + float(effect["grow_y"])
                effect["y"] = float(effect["y"]) + float(effect["vy"])
            elif effect["kind"] == "wake":
                effect["x"] = float(effect["x"]) + float(effect["vx"])
                effect["y"] = float(effect["y"]) + float(effect["vy"])
                effect["radius_x"] = float(effect["radius_x"]) + float(effect["grow_x"])
                effect["radius_y"] = float(effect["radius_y"]) + float(effect["grow_y"])
            elif effect["kind"] == "snap":
                effect["radius_x"] = max(0.1, float(effect["radius_x"]) - float(effect["shrink_x"]))
                effect["radius_y"] = max(0.1, float(effect["radius_y"]) - float(effect["shrink_y"]))
                effect["line_width"] = max(0.22, float(effect["line_width"]) * 0.9)
            elif effect["kind"] == "ripple":
                effect["radius_x"] = float(effect["radius_x"]) + float(effect["grow_x"])
                effect["radius_y"] = float(effect["radius_y"]) + float(effect["grow_y"])
                effect["line_width"] = max(0.3, float(effect["line_width"]) * 0.93)
            else:
                effect["x"] = float(effect["x"]) + float(effect["vx"])
                effect["y"] = float(effect["y"]) + float(effect["vy"])
                effect["vy"] = float(effect["vy"]) + 0.016
                effect["radius"] = max(0.25, float(effect["radius"]) * 0.965)

            active_effects.append(effect)

        self._pop_effects = active_effects

        if self._burst_ticks_remaining > 0:
            self._burst_ticks_remaining -= 1
            if len(self._bubbles) < 22 and random.random() < 0.78:
                self._spawn_bubble()
            if len(self._bubbles) < 18 and random.random() < 0.58:
                self._spawn_bubble()
            if len(self._bubbles) < 12 and random.random() < 0.24:
                self._spawn_bubble()
            if self._burst_ticks_remaining <= 0:
                self._schedule_next_burst()
        else:
            if self._cooldown_ticks_remaining > 0:
                self._cooldown_ticks_remaining -= 1
            elif len(self._bubbles) > 2:
                self._quiet_ticks_remaining = max(self._quiet_ticks_remaining, random.randint(10, 18))
            elif self._quiet_ticks_remaining > 0:
                self._quiet_ticks_remaining -= 1
            elif random.random() < 0.14:
                self._start_burst()

        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        for bubble in sorted(self._bubbles, key=lambda item: float(item["depth"])):
            life_ratio = float(bubble["life"]) / max(1.0, float(bubble["max_life"]))
            depth = float(bubble["depth"])
            x = float(bubble["x"])
            y = float(bubble["y"])
            start_y = float(bubble.get("start_y", y))
            radius = float(bubble["radius"])
            travel_span = max(90.0, start_y + radius * 2.8)
            travel_ratio = min(1.0, max(0.0, (start_y - y) / travel_span))
            fade_out = 1.0 if travel_ratio < 0.88 else max(0.0, 1.0 - ((travel_ratio - 0.88) / 0.12))
            tail_fade = 1.0 if life_ratio > 0.18 else max(0.0, life_ratio / 0.18)
            fade = fade_out * tail_fade
            alpha = float(bubble["alpha"]) * fade
            if alpha <= 0.0:
                continue

            bubble_rect = QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0)
            glow_radius = radius * float(bubble["glow_scale"])
            tint = QColor(bubble["color"])

            glow = QRadialGradient(x, y, glow_radius)
            glow_outer = QColor(tint)
            glow_outer.setAlphaF(0.0)
            glow_mid = QColor(tint)
            glow_mid.setAlphaF(alpha * (0.12 + depth * 0.06))
            glow_inner = QColor("#F0FDFF")
            glow_inner.setAlphaF(alpha * (0.08 + depth * 0.05))
            glow.setColorAt(0.0, glow_inner)
            glow.setColorAt(0.68, glow_mid)
            glow.setColorAt(1.0, glow_outer)
            painter.setBrush(glow)
            painter.drawEllipse(
                QRectF(
                    x - glow_radius,
                    y - glow_radius,
                    glow_radius * 2.0,
                    glow_radius * 2.0,
                )
            )

            fill = QRadialGradient(
                x - radius * 0.28,
                y - radius * 0.34,
                radius * 1.16,
            )
            fill_center = QColor("#F8FDFF")
            fill_center.setAlphaF(alpha * 0.18)
            fill_mid = QColor(tint)
            fill_mid.setAlphaF(alpha * 0.10)
            fill_outer = QColor(tint)
            fill_outer.setAlphaF(alpha * 0.02)
            fill.setColorAt(0.0, fill_center)
            fill.setColorAt(0.48, fill_mid)
            fill.setColorAt(1.0, fill_outer)
            painter.setBrush(fill)
            painter.drawEllipse(bubble_rect)

            outline = QColor("#DDF8FF")
            outline.setAlphaF(alpha * (0.72 + depth * 0.12))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(outline, max(1.15, radius * 0.08)))
            painter.drawEllipse(bubble_rect)

            reflection = QColor("#F8FDFF")
            reflection.setAlphaF(alpha * (0.74 + depth * 0.08))
            highlight_radius = radius * float(bubble["highlight_scale"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(reflection)
            painter.drawEllipse(
                QRectF(
                    x - radius * 0.38,
                    y - radius * 0.50,
                    highlight_radius * 1.8,
                    highlight_radius,
                )
            )

            inner_glint = QColor("#CFFAFE")
            inner_glint.setAlphaF(alpha * 0.28)
            painter.setBrush(inner_glint)
            painter.drawEllipse(
                QRectF(
                    x + radius * 0.08,
                    y + radius * 0.08,
                    radius * 0.22,
                    radius * 0.22,
                )
            )

        if self._held_bubble is not None:
            held = self._held_bubble
            x = float(held["x"])
            y = float(held["y"])
            radius = float(held["radius"])
            alpha = float(held["alpha"])
            depth = float(held["depth"])
            tint = QColor(held["color"])
            bubble_rect = QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0)
            glow_radius = radius * float(held["glow_scale"])

            glow = QRadialGradient(x, y, glow_radius)
            glow_outer = QColor(tint)
            glow_outer.setAlphaF(0.0)
            glow_mid = QColor(tint)
            glow_mid.setAlphaF(alpha * (0.13 + depth * 0.06))
            glow_inner = QColor("#F6FEFF")
            glow_inner.setAlphaF(alpha * 0.16)
            glow.setColorAt(0.0, glow_inner)
            glow.setColorAt(0.7, glow_mid)
            glow.setColorAt(1.0, glow_outer)
            painter.setBrush(glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QRectF(
                    x - glow_radius,
                    y - glow_radius,
                    glow_radius * 2.0,
                    glow_radius * 2.0,
                )
            )

            fill = QRadialGradient(
                x - radius * 0.24,
                y - radius * 0.28,
                radius * 1.1,
            )
            fill_center = QColor("#FBFEFF")
            fill_center.setAlphaF(alpha * 0.2)
            fill_mid = QColor(tint)
            fill_mid.setAlphaF(alpha * 0.11)
            fill_outer = QColor(tint)
            fill_outer.setAlphaF(alpha * 0.02)
            fill.setColorAt(0.0, fill_center)
            fill.setColorAt(0.5, fill_mid)
            fill.setColorAt(1.0, fill_outer)
            painter.setBrush(fill)
            painter.drawEllipse(bubble_rect)

            outline = QColor("#DDF8FF")
            outline.setAlphaF(alpha * 0.84)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(outline, max(1.0, radius * 0.08)))
            painter.drawEllipse(bubble_rect)

            reflection = QColor("#F8FDFF")
            reflection.setAlphaF(alpha * 0.78)
            highlight_radius = radius * float(held["highlight_scale"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(reflection)
            painter.drawEllipse(
                QRectF(
                    x - radius * 0.36,
                    y - radius * 0.48,
                    highlight_radius * 1.8,
                    highlight_radius,
                )
            )

        for effect in self._pop_effects:
            life_ratio = float(effect["life"]) / max(1.0, float(effect["max_life"]))
            color = QColor(effect["color"])
            base_alpha = float(effect.get("base_alpha", color.alphaF()))
            if effect["kind"] == "compression":
                color.setAlphaF(base_alpha * life_ratio)
                radius_x = float(effect["radius_x"])
                radius_y = float(effect["radius_y"])
                gradient_radius = max(radius_x, radius_y)
                compression = QRadialGradient(
                    float(effect["x"]),
                    float(effect["y"]),
                    gradient_radius,
                )
                inner = QColor(color)
                inner.setAlphaF(base_alpha * life_ratio * 0.34)
                mid = QColor(color)
                mid.setAlphaF(base_alpha * life_ratio * 0.14)
                outer = QColor(color)
                outer.setAlphaF(0.0)
                compression.setColorAt(0.0, inner)
                compression.setColorAt(0.52, mid)
                compression.setColorAt(1.0, outer)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(compression)
                painter.drawEllipse(
                    QRectF(
                        float(effect["x"]) - radius_x,
                        float(effect["y"]) - radius_y,
                        radius_x * 2.0,
                        radius_y * 2.0,
                    )
                )
            elif effect["kind"] == "wake":
                color.setAlphaF(base_alpha * life_ratio)
                radius_x = float(effect["radius_x"])
                radius_y = float(effect["radius_y"])
                gradient_radius = max(radius_x, radius_y)
                wake = QRadialGradient(
                    float(effect["x"]),
                    float(effect["y"]),
                    gradient_radius,
                )
                inner = QColor(color)
                inner.setAlphaF(base_alpha * life_ratio * 0.28)
                mid = QColor(color)
                mid.setAlphaF(base_alpha * life_ratio * 0.12)
                outer = QColor(color)
                outer.setAlphaF(0.0)
                wake.setColorAt(0.0, inner)
                wake.setColorAt(0.48, mid)
                wake.setColorAt(1.0, outer)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(wake)
                painter.drawEllipse(
                    QRectF(
                        float(effect["x"]) - radius_x,
                        float(effect["y"]) - radius_y,
                        radius_x * 2.0,
                        radius_y * 2.0,
                    )
                )
            elif effect["kind"] == "snap":
                color.setAlphaF(base_alpha * life_ratio)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(color, float(effect["line_width"])))
                radius_x = float(effect["radius_x"])
                radius_y = float(effect["radius_y"])
                painter.drawEllipse(
                    QRectF(
                        float(effect["x"]) - radius_x,
                        float(effect["y"]) - radius_y,
                        radius_x * 2.0,
                        radius_y * 2.0,
                    )
                )
            elif effect["kind"] == "ripple":
                continue
            elif effect["kind"] == "fragment":
                color.setAlphaF(base_alpha * life_ratio)
                radius = float(effect["radius"])
                painter.setPen(QPen(color, max(0.22, radius * 0.9)))
                fill = QColor("#F8FDFF")
                fill.setAlphaF(base_alpha * life_ratio * 0.24)
                painter.setBrush(fill)
                painter.drawEllipse(
                    QRectF(
                        float(effect["x"]) - radius,
                        float(effect["y"]) - radius,
                        radius * 2.0,
                        radius * 2.0,
                    )
                )
            else:
                color.setAlphaF(base_alpha * life_ratio)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                radius = float(effect["radius"])
                painter.drawEllipse(
                    QRectF(
                        float(effect["x"]) - radius,
                        float(effect["y"]) - radius,
                        radius * 2.0,
                        radius * 2.0,
                    )
                )

        painter.end()


class ExtractionThread(QThread):
    """Thread for running extraction process without blocking GUI."""

    MAX_LNI_ATTEMPTS = 2
    progress_update = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, object)
    cancelled = pyqtSignal(str, object)

    def __init__(
        self,
        user_id: str,
        password: str,
        excel_path: Path | None,
        logger,
        file_manager,
        headless_mode: bool = False,
        run_folder: Path | None = None,
        remember_credentials: bool = False,
        developer_mode_enabled: bool = False,
        developer_override_to: str = "",
        developer_override_cc: str = "",
        manual_override_to: str = "",
        manual_override_cc: str = "",
        header_fill_color: str = "",
        source_mode: str = "irt",
        irt_court_scope: str = "both",
        irt_start_date: date | None = None,
        irt_end_date: date | None = None,
    ):
        super().__init__()
        self.user_id = user_id
        self.password = password
        self.excel_path = excel_path
        self.logger = logger
        self.file_manager = file_manager
        self.headless_mode = headless_mode
        self.run_folder = run_folder
        self.remember_credentials = remember_credentials
        self.developer_mode_enabled = developer_mode_enabled
        self.developer_override_to = developer_override_to
        self.developer_override_cc = developer_override_cc
        self.manual_override_to = manual_override_to
        self.manual_override_cc = manual_override_cc
        self.header_fill_color = header_fill_color
        self.source_mode = source_mode
        self.irt_court_scope = irt_court_scope
        self.irt_start_date = irt_start_date
        self.irt_end_date = irt_end_date
        self.outlook_import_summary = None
        self._stop_requested = False

    def request_stop(self) -> None:
        """Request a cooperative stop for the active run."""
        if self._stop_requested:
            return
        self._stop_requested = True
        self.requestInterruption()
        if self.logger:
            self.logger.log("Stop requested by user")

    def _is_stop_requested(self) -> bool:
        """Return True when the user has asked to stop the run."""
        return self._stop_requested or self.isInterruptionRequested()

    def _raise_if_stop_requested(self) -> None:
        """Abort promptly when a stop request is active."""
        if self._is_stop_requested():
            raise InterruptedError("Stopped.")

    def _finalize_cancellation(self, excel_handler=None) -> None:
        """Save partial work when possible and emit a calm cancelled state."""
        output_path = None
        message = ""

        if excel_handler is not None and self.excel_path:
            try:
                self.logger.log(
                    f"Attempting to save partial Excel file after stop request: {self.excel_path}"
                )
                excel_handler.save(self.excel_path)
                self.logger.log(
                    f"Successfully saved partial Excel file: {self.excel_path}"
                )
                output_path = self.excel_path
                message = "Progress so far was saved."
            except Exception as e:
                if self.logger:
                    self.logger.log(
                        f"Could not save partial Excel file after stop request: {e}"
                    )
                message = "Progress so far couldn't be saved automatically."

        if self.logger:
            try:
                self.logger.log("Saving log file")
                self.logger.save_log()
            except Exception:
                pass

        self.cancelled.emit(message, output_path)

    def _search_current_lni(self, scraper, lni: str, is_first_search: bool) -> tuple[bool, bool]:
        """Start or restart the search flow for the current LNI."""
        self._raise_if_stop_requested()

        if is_first_search:
            search_ok = scraper.search_lni(lni, is_first_search=True)
            return search_ok, False

        if scraper.click_new_search():
            return scraper.search_lni(lni, is_first_search=False), False

        self.logger.log(
            "New Search button was not available; attempting to reuse the current search field."
        )
        return scraper.search_lni(lni, is_first_search=False), False

    def _extract_lni_with_retry(
        self,
        scraper,
        lni: str,
        is_first_search: bool,
        processed: int,
        total_lnis: int,
        progress: int,
    ) -> tuple[str, bool]:
        """Extract one LNI, retrying once when the result stage looks transient."""
        last_failure_detail = "result card did not appear"

        for attempt in range(1, self.MAX_LNI_ATTEMPTS + 1):
            self._raise_if_stop_requested()

            if attempt > 1:
                retry_status = (
                    f"Retrying LNI {processed}/{total_lnis} after temporary result-load issue..."
                )
                self.progress_update.emit(progress, retry_status)
                self.logger.log(
                    f"Retrying LNI {lni} (attempt {attempt}/{self.MAX_LNI_ATTEMPTS}) "
                    f"because {last_failure_detail}."
                )

            search_ok, is_first_search = self._search_current_lni(
                scraper,
                lni,
                is_first_search,
            )
            if not search_ok:
                last_failure_detail = "the search/results panel did not finish loading"
                continue

            if not scraper.click_administrative_materials():
                last_failure_detail = "Administrative Materials could not be opened"
                continue

            found, result_element = scraper.find_result_card()
            if not found:
                last_failure_detail = "the result card did not appear in time"
                continue

            if not scraper.click_result_card(result_element):
                last_failure_detail = "the result card could not be opened"
                continue

            lexis_cite = scraper.extract_lexis_cite()
            if not lexis_cite or lexis_cite == "Not Available":
                last_failure_detail = "the Lexis Cite did not finish loading"
                continue

            if attempt > 1:
                self.logger.log(f"Retry succeeded for LNI {lni}")
            return lexis_cite, is_first_search

        self.logger.log(
            f"LNI {lni} remained unavailable after {self.MAX_LNI_ATTEMPTS} attempt(s): "
            f"{last_failure_detail}"
        )
        return "Not Available", is_first_search

    def run(self):
        """Run the extraction process."""
        scraper = None
        excel_handler = None

        try:
            from utils.excel_handler import ExcelHandler
            from utils.irt_intake import IRTQuery, import_irt_results_to_workbook
            from utils.outlook_intake import (
                find_conversion_email_context,
                import_conversion_email_to_workbook,
            )
            from automation.lexis_scraper import LexisScraper

            recipient_override_to = self.manual_override_to or (
                self.developer_override_to if self.developer_mode_enabled else ""
            )
            recipient_override_cc = (
                self.manual_override_cc
                if self.manual_override_to
                else (self.developer_override_cc if self.developer_mode_enabled else "")
            )

            if self.excel_path is None:
                try:
                    self._raise_if_stop_requested()
                    if self.run_folder is None:
                        self.run_folder = self.file_manager.create_run_folder()

                    if self.source_mode == "irt":
                        if self.irt_start_date is None or self.irt_end_date is None:
                            raise RuntimeError("IRT date filters were not provided.")

                        self.progress_update.emit(0, "Importing source data from IRT...")
                        self.logger.log("Importing source data from IRT Search Inventory")
                        import_summary = import_irt_results_to_workbook(
                            run_folder=self.run_folder,
                            query=IRTQuery(
                                court_scope=self.irt_court_scope,
                                start_date=self.irt_start_date,
                                end_date=self.irt_end_date,
                            ),
                            logger=self.logger,
                            header_fill_color=self.header_fill_color,
                            headless_mode=self.headless_mode,
                            cancel_check=self._is_stop_requested,
                        )
                        self.excel_path = import_summary.workbook_path
                        self.progress_update.emit(
                            0,
                            f"Imported {import_summary.imported_row_count} row(s) from IRT results",
                        )
                        self.logger.log(
                            f"Source workbook created from IRT results: {self.excel_path}"
                        )
                        if import_summary.selected_headers:
                            self.logger.log(
                                "Selected IRT result headers: "
                                + ", ".join(import_summary.selected_headers)
                            )
                    else:
                        self.progress_update.emit(0, "Importing source data from Outlook...")
                        self.logger.log("Importing source data from Outlook email")
                        import_summary = import_conversion_email_to_workbook(
                            run_folder=self.run_folder,
                            logger=self.logger,
                            header_fill_color=self.header_fill_color,
                            cancel_check=self._is_stop_requested,
                        )
                        self.outlook_import_summary = import_summary
                        self.excel_path = import_summary.workbook_path
                        self.progress_update.emit(
                            0,
                            f"Imported {import_summary.imported_row_count} row(s) from Outlook email",
                        )
                        self.logger.log(
                            f"Source workbook created from Outlook email: {self.excel_path}"
                        )
                except InterruptedError:
                    raise
                except Exception as e:
                    error_msg = (
                        f"Error importing source data from {self.source_mode}: {e}\n"
                        f"{traceback.format_exc()}"
                    )
                    self.logger.log(error_msg)
                    self.finished.emit(
                        False,
                        f"Failed to import source data from {self.source_mode.title()}: {e}",
                        None,
                    )
                    return

            if self.outlook_import_summary is None and not recipient_override_to:
                try:
                    self._raise_if_stop_requested()
                    source_label = (
                        "manual workbook source"
                        if self.source_mode == "manual"
                        else "IRT workbook source"
                    )
                    self.logger.log(f"Resolving Outlook reply context for {source_label}")
                    self.outlook_import_summary = find_conversion_email_context(
                        logger=self.logger,
                        open_inbox=False,
                        cancel_check=self._is_stop_requested,
                    )
                except InterruptedError:
                    raise
                except Exception as e:
                    self.logger.log(
                        "Could not resolve Outlook reply context for the "
                        f"{source_label}: {e}"
                    )

            try:
                self._raise_if_stop_requested()
                scraper = LexisScraper(
                    self.logger,
                    cancel_check=self._is_stop_requested,
                )
                excel_handler = ExcelHandler(
                    header_fill_color=self.header_fill_color
                )
                self.logger.log(f"Opening Excel file: {self.excel_path}")
                excel_handler.open_excel_file(self.excel_path)
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error initializing components: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.finished.emit(False, f"Failed to initialize: {e}", None)
                return

            try:
                self._raise_if_stop_requested()
                lni_data = excel_handler.read_lni_data()
                total_lnis = len(lni_data)
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error reading LNI data: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.finished.emit(False, f"Failed to read Excel file: {e}", None)
                return

            if total_lnis == 0:
                self.logger.log("No LNI data found in Excel file")
                self.finished.emit(False, "No LNI data found in Excel file", None)
                return

            self.progress_update.emit(0, f"Starting extraction of {total_lnis} LNIs...")
            self.logger.log(f"Starting extraction of {total_lnis} LNIs")

            try:
                self._raise_if_stop_requested()
                if not scraper.launch_browser(headless_mode=self.headless_mode):
                    self.logger.log("Failed to launch browser")
                    self.finished.emit(False, "Failed to launch browser", None)
                    return

                if not scraper.navigate_to_lexis():
                    self.logger.log("Failed to navigate to Lexis website")
                    self.finished.emit(False, "Failed to navigate to Lexis website", None)
                    return

                login_result = scraper.login(self.user_id, self.password)
                if not login_result.success:
                    self.logger.log(
                        "Login failed; credentials were not saved | "
                        f"reason={login_result.reason} | detail={login_result.message}"
                    )

                    if login_result.reason == scraper.LOGIN_REASON_INVALID_CREDENTIALS:
                        user_message = (
                            "Login failed because Lexis rejected the ID or password. "
                            "Please verify your credentials and retry."
                        )
                    elif login_result.reason == scraper.LOGIN_REASON_NETWORK_OR_SITE:
                        user_message = (
                            "Login could not complete because the Lexis site or network "
                            "appears slow/unavailable. Please retry shortly."
                        )
                    elif login_result.reason == scraper.LOGIN_REASON_AUTH_CHALLENGE:
                        user_message = (
                            "Login was interrupted by an additional verification challenge "
                            "(MFA/captcha/security check). Please complete the challenge "
                            "manually and retry."
                        )
                    else:
                        user_message = (
                            "Login did not complete. This may be due to credentials, "
                            "network latency, or a site-side issue. Please retry and "
                            "check the run log for details."
                        )

                    self.finished.emit(False, user_message, None)
                    return

                if self.remember_credentials:
                    password_saved = save_credentials(self.user_id, self.password)
                    if password_saved and is_keyring_available():
                        self.logger.log("Credentials saved securely using the OS keyring")
                    else:
                        self.logger.log(
                            "Lexis ID saved, but password was not stored because the OS keyring "
                            "is unavailable in this runtime."
                        )
                else:
                    clear_credentials()
                    self.logger.log("Saved credentials cleared")

            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error during browser setup/login: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.finished.emit(False, f"Browser/login error: {e}", None)
                return

            processed = 0
            is_first_search = True

            for row, lni in lni_data:
                try:
                    self._raise_if_stop_requested()
                    processed += 1
                    progress = int((processed / total_lnis) * 100)
                    self.progress_update.emit(
                        progress,
                        f"Processing LNI {processed}/{total_lnis}: {lni}",
                    )

                    lexis_cite, is_first_search = self._extract_lni_with_retry(
                        scraper=scraper,
                        lni=lni,
                        is_first_search=is_first_search,
                        processed=processed,
                        total_lnis=total_lnis,
                        progress=progress,
                    )
                    excel_handler.write_lexis_cite(row, lexis_cite)

                    if lexis_cite == "Not Available":
                        self.progress_update.emit(
                            progress,
                            f"No results found for LNI {processed}/{total_lnis}",
                        )
                    else:
                        self.progress_update.emit(
                            progress,
                            f"Successfully extracted LNI {processed}/{total_lnis}",
                        )

                except InterruptedError:
                    raise
                except Exception as e:
                    error_msg = (
                        f"Error processing LNI {lni} (Row {row}): "
                        f"{e}\n{traceback.format_exc()}"
                    )
                    self.logger.log(error_msg)
                    try:
                        excel_handler.write_lexis_cite(row, "Not Available")
                    except Exception as write_error:
                        self.logger.log(
                            f"Failed to write 'Not Available' for row {row}: {write_error}"
                        )
                    continue

            try:
                self._raise_if_stop_requested()
                self.logger.log(f"Attempting to save updated Excel file: {self.excel_path}")
                excel_handler.save(self.excel_path)
                self.logger.log(f"Successfully saved updated Excel file: {self.excel_path}")
                output_path = self.excel_path
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error saving output file: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.logger.save_log()
                self.finished.emit(False, f"Failed to save output file: {e}", None)
                return

            completion_message = "Workbook saved."

            try:
                self._raise_if_stop_requested()
                from utils.outlook_mailer import (
                    NoSuccessfulExtractionsError,
                    send_extraction_email,
                )

                if self.outlook_import_summary is not None:
                    email_summary = send_extraction_email(
                        output_path,
                        self.logger,
                        source_message_entry_id=self.outlook_import_summary.message_entry_id,
                        source_message_store_id=self.outlook_import_summary.message_store_id,
                        fallback_to=self.outlook_import_summary.to_recipients,
                        fallback_cc=self.outlook_import_summary.cc_recipients,
                        override_to=recipient_override_to or None,
                        override_cc=recipient_override_cc or None,
                    )
                else:
                    email_summary = send_extraction_email(
                        output_path,
                        self.logger,
                        override_to=recipient_override_to or None,
                        override_cc=recipient_override_cc or None,
                    )

                completion_message = (
                    f"Email sent with {email_summary.success_count} "
                    f"available {'document' if email_summary.success_count == 1 else 'documents'}."
                )
            except NoSuccessfulExtractionsError as e:
                self.logger.log(str(e))
                completion_message = str(e)
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error sending Outlook email: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                completion_message = (
                    "Workbook saved, but the email couldn't be sent automatically.\n\n"
                    f"{e}"
                )

            try:
                self.logger.log("Saving log file")
                self.logger.save_log()
            except Exception as e:
                self.logger.log(f"Error saving log file: {e}\n{traceback.format_exc()}")

            self.finished.emit(
                True,
                completion_message,
                output_path,
            )

        except InterruptedError:
            self._finalize_cancellation(excel_handler=excel_handler)

        except Exception as e:
            error_msg = f"Critical error in extraction process: {e}\n{traceback.format_exc()}"
            if self.logger:
                self.logger.log(error_msg)
                try:
                    self.logger.save_log()
                except Exception:
                    pass
            self.finished.emit(False, f"Extraction failed: {e}", None)

        finally:
            if scraper is not None:
                try:
                    scraper.close_browser()
                    self.logger.log("Browser closed successfully")
                except Exception as e:
                    self.logger.log(f"Error closing browser: {e}")


class DraggableTitleBar(QFrame):
    """A simple draggable title bar for a frameless window."""

    def mousePressEvent(self, event):
        """Store the drag offset when the user starts dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if isinstance(window, MainWindow):
                window.drag_offset = (
                    event.globalPosition().toPoint() - window.frameGeometry().topLeft()
                )
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Move the window while dragging."""
        if event.buttons() & Qt.MouseButton.LeftButton:
            window = self.window()
            if isinstance(window, MainWindow) and window.drag_offset is not None:
                window.move(event.globalPosition().toPoint() - window.drag_offset)
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Clear the stored drag offset when dragging ends."""
        window = self.window()
        if isinstance(window, MainWindow):
            window.drag_offset = None

        super().mouseReleaseEvent(event)


class FloatingPopupComboBox(QComboBox):
    """Combo box that uses a styled Qt popup view without native window chrome."""

    def showPopup(self):
        """Open the standard Qt popup with a width that matches the control."""
        popup_view = self.view()
        popup_view.setMinimumWidth(self.width())
        super().showPopup()


class CalendarHeaderPopup(QFrame):
    """Dedicated popup list for month and year selection inside the calendar header."""

    index_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, visible_row_limit: int = 5):
        super().__init__(parent)
        self.setObjectName("CalendarHeaderPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.visible_row_limit = max(1, visible_row_limit)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame()
        self.panel.setObjectName("CalendarHeaderPopupPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setSpacing(0)
        panel_layout.setContentsMargins(8, 8, 8, 8)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("CalendarHeaderList")
        self.list_widget.setSpacing(4)
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        panel_layout.addWidget(self.list_widget)
        layout.addWidget(self.panel)

    def set_visible_row_limit(self, limit: int) -> None:
        """Update the maximum number of visible rows before scrolling."""
        self.visible_row_limit = max(1, limit)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Emit the selected combo index and close the popup."""
        selected_index = item.data(Qt.ItemDataRole.UserRole)
        if selected_index is None:
            return
        self.index_selected.emit(int(selected_index))
        self.hide()

    def _visible_row_count(self) -> int:
        """Clamp popup height to a compact, scrollable number of rows."""
        return max(1, min(self.visible_row_limit, self.list_widget.count()))

    def refresh_from_combo(self, combo: QComboBox) -> None:
        """Mirror combo text and icons into the dedicated popup list."""
        self.list_widget.clear()
        for index in range(combo.count()):
            item = QListWidgetItem(combo.itemIcon(index), combo.itemText(index))
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)

        self.list_widget.setCurrentRow(max(0, combo.currentIndex()))

    def show_for_combo(self, combo: QComboBox) -> None:
        """Position the popup beneath the header combo and sync selection."""
        self.setStyleSheet(combo.window().styleSheet())
        self.refresh_from_combo(combo)

        row_height = self.list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 36
        visible_rows = self._visible_row_count()
        list_height = (row_height * visible_rows) + (self.list_widget.spacing() * (visible_rows - 1))
        popup_height = list_height + 24
        popup_width = max(combo.width(), 132)

        anchor = combo.mapToGlobal(QPoint(0, combo.height() + 6))
        self.resize(popup_width, popup_height)
        self.move(anchor)
        self.show()
        self.raise_()
        self.activateWindow()
        self.list_widget.setFocus()
        current_item = self.list_widget.currentItem()
        if current_item is not None:
            self.list_widget.scrollToItem(current_item, QListWidget.ScrollHint.PositionAtCenter)


class CalendarHeaderComboBox(FloatingPopupComboBox):
    """Header combo shell backed by a dedicated rounded popup list."""

    def __init__(self, parent: QWidget | None = None, visible_row_limit: int = 5):
        super().__init__(parent)
        self.header_popup = CalendarHeaderPopup(self, visible_row_limit=visible_row_limit)
        self.header_popup.index_selected.connect(self._apply_popup_selection)

    def setPopupVisibleRowLimit(self, visible_row_limit: int) -> None:
        """Allow the parent header to clamp popup rows without native combo behavior."""
        self.header_popup.set_visible_row_limit(visible_row_limit)

    def _apply_popup_selection(self, index: int) -> None:
        """Apply a popup selection back into the combo shell."""
        self.setCurrentIndex(index)

    def showPopup(self):
        """Show the dedicated rounded popup instead of the standard combo list."""
        if not self.isEnabled():
            return
        self.header_popup.show_for_combo(self)

    def hidePopup(self):
        """Hide the dedicated popup when Qt requests closure."""
        self.header_popup.hide()


class CalendarHeaderSelector(QPushButton):
    """Button-style calendar header selector that reuses the dedicated popup list."""

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, visible_row_limit: int = 5):
        super().__init__(parent)
        self._items: list[tuple[str, object | None]] = []
        self._current_index = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setIconSize(QSize(10, 7))

        chevron_icon_path = Path(__file__).resolve().parent.parent / "assets" / "chevron_down.png"
        if chevron_icon_path.exists():
            self.setIcon(QIcon(str(chevron_icon_path)))

        self.header_popup = CalendarHeaderPopup(self, visible_row_limit=visible_row_limit)
        self.header_popup.index_selected.connect(self._apply_popup_selection)

    def setPopupVisibleRowLimit(self, visible_row_limit: int) -> None:
        """Allow the parent header to clamp popup rows."""
        self.header_popup.set_visible_row_limit(visible_row_limit)

    def clear(self) -> None:
        """Remove all selector items."""
        self._items.clear()
        self._current_index = -1
        self.setText("")

    def addItem(self, text: str, user_data=None) -> None:
        """Append one selectable item to the header selector."""
        self._items.append((text, user_data))
        if self._current_index < 0:
            self.setCurrentIndex(0)

    def count(self) -> int:
        """Return the number of items available in the selector."""
        return len(self._items)

    def itemText(self, index: int) -> str:
        """Return the visible label for one selector row."""
        if 0 <= index < len(self._items):
            return self._items[index][0]
        return ""

    def itemIcon(self, index: int) -> QIcon:
        """Return the icon for one selector row."""
        return QIcon()

    def itemData(self, index: int):
        """Return the stored user data for one selector row."""
        if 0 <= index < len(self._items):
            return self._items[index][1]
        return None

    def currentIndex(self) -> int:
        """Return the active selector index."""
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:
        """Update the active selector item and refresh the button label."""
        if not (0 <= index < len(self._items)):
            return
        changed = index != self._current_index
        self._current_index = index
        self.setText(self._items[index][0])
        if changed:
            self.currentIndexChanged.emit(index)

    def findData(self, value) -> int:
        """Find the first selector row whose user data matches the value."""
        for index, (_text, data) in enumerate(self._items):
            if data == value:
                return index
        return -1

    def _apply_popup_selection(self, index: int) -> None:
        """Apply a popup selection back into the visible header button."""
        self.setCurrentIndex(index)

    def showPopup(self) -> None:
        """Show the dedicated popup list beneath the selector button."""
        if not self.isEnabled():
            return
        self.header_popup.show_for_combo(self)

    def hidePopup(self) -> None:
        """Hide the dedicated popup list."""
        self.header_popup.hide()

    def mousePressEvent(self, event) -> None:
        """Toggle the popup when the header button is clicked."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.header_popup.isVisible():
                self.header_popup.hide()
            else:
                self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        """Support keyboard opening for the selector popup."""
        if event.key() in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
            Qt.Key.Key_F4,
        ):
            if self.header_popup.isVisible():
                self.header_popup.hide()
            else:
                self.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)


class ThemedCalendarPopup(QFrame):
    """Compact themed calendar popup for date-range selection."""

    date_selected = pyqtSignal(QDate)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DatePickerPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.owner_edit: ThemedDateEdit | None = None
        self._year_span = 20
        self._app_event_filter_installed = False

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame()
        self.shell.setObjectName("DatePickerPopupShell")
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setSpacing(8)
        shell_layout.setContentsMargins(10, 10, 10, 10)

        self.header = QWidget()
        self.header.setObjectName("DatePickerHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setSpacing(8)
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.previous_button = QPushButton("‹")
        self.previous_button.setObjectName("DatePickerNavButton")
        self.previous_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.previous_button.clicked.connect(self._show_previous_month)
        header_layout.addWidget(self.previous_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.month_combo = CalendarHeaderSelector(visible_row_limit=5)
        self.month_combo.setObjectName("DatePickerMonthCombo")
        self._configure_header_combo(self.month_combo, max_visible_items=5)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)
        header_layout.addWidget(self.month_combo, 1, Qt.AlignmentFlag.AlignVCenter)

        self.year_combo = CalendarHeaderSelector(visible_row_limit=5)
        self.year_combo.setObjectName("DatePickerYearCombo")
        self._configure_header_combo(self.year_combo, max_visible_items=5)
        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        header_layout.addWidget(self.year_combo, 0, Qt.AlignmentFlag.AlignVCenter)

        self.next_button = QPushButton("›")
        self.next_button.setObjectName("DatePickerNavButton")
        self.next_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_button.clicked.connect(self._show_next_month)
        header_layout.addWidget(self.next_button, 0, Qt.AlignmentFlag.AlignVCenter)

        shell_layout.addWidget(self.header)

        self.calendar = QCalendarWidget()
        self.calendar.setObjectName("DatePickerCalendar")
        self.calendar.setNavigationBarVisible(False)
        self.calendar.setDateEditEnabled(False)
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.calendar.setHorizontalHeaderFormat(
            QCalendarWidget.HorizontalHeaderFormat.ShortDayNames
        )
        self.calendar.clicked.connect(self._on_date_chosen)
        self.calendar.activated.connect(self._on_date_chosen)
        self.calendar.currentPageChanged.connect(self._sync_title)
        shell_layout.addWidget(self.calendar)

        layout.addWidget(self.shell)
        self._populate_month_combo()
        self._apply_calendar_formats()
        self.resize(324, 294)

    def _configure_header_combo(self, combo: QWidget, max_visible_items: int) -> None:
        """Make month and year selectors feel like compact header controls."""
        combo.setMinimumHeight(40)
        combo.setMaximumHeight(40)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        if hasattr(combo, "setPopupVisibleRowLimit"):
            combo.setPopupVisibleRowLimit(max_visible_items)

    def _populate_month_combo(self) -> None:
        """Fill the month selector with human-friendly month labels."""
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        for month in range(1, 13):
            self.month_combo.addItem(date(2000, month, 1).strftime("%B"), month)
        self.month_combo.blockSignals(False)

    def _refresh_year_combo(self, visible_year: int) -> None:
        """Keep a focused, scrollable year range around the current page."""
        minimum_year = 1900
        maximum_year = 2100

        if self.owner_edit is not None:
            owner_minimum = self.owner_edit.minimumDate().year()
            owner_maximum = self.owner_edit.maximumDate().year()
            if owner_minimum > minimum_year:
                minimum_year = owner_minimum
            if owner_maximum < maximum_year:
                maximum_year = owner_maximum

        if maximum_year < minimum_year:
            minimum_year, maximum_year = maximum_year, minimum_year

        start_year = max(minimum_year, visible_year - self._year_span)
        end_year = min(maximum_year, visible_year + self._year_span)

        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for year in range(start_year, end_year + 1):
            self.year_combo.addItem(str(year), year)
        selected_index = self.year_combo.findData(visible_year)
        if selected_index >= 0:
            self.year_combo.setCurrentIndex(selected_index)
        self.year_combo.blockSignals(False)

    def _apply_calendar_formats(self) -> None:
        """Apply stable weekday and today styling so Qt defaults do not leak through."""
        header_format = QTextCharFormat()
        header_format.setForeground(QColor("#cbd5e1"))
        self.calendar.setHeaderTextFormat(header_format)

        weekday_format = QTextCharFormat()
        weekday_format.setForeground(QColor("#dbe7f5"))
        weekend_format = QTextCharFormat()
        weekend_format.setForeground(QColor("#f8fafc"))
        today_format = QTextCharFormat()
        today_format.setForeground(QColor("#67e8f9"))
        today_format.setFontWeight(QFont.Weight.DemiBold)

        for weekday in (
            Qt.DayOfWeek.Monday,
            Qt.DayOfWeek.Tuesday,
            Qt.DayOfWeek.Wednesday,
            Qt.DayOfWeek.Thursday,
            Qt.DayOfWeek.Friday,
        ):
            self.calendar.setWeekdayTextFormat(weekday, weekday_format)

        self.calendar.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, weekend_format)
        self.calendar.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, weekend_format)
        self.calendar.setDateTextFormat(QDate.currentDate(), today_format)

    def _month_title(self, year: int, month: int) -> str:
        """Return a clean English month-year label for the popup header."""
        return f"{date(year, month, 1).strftime('%B')} {year}"

    def _sync_title(self, year: int, month: int) -> None:
        """Keep the popup month and year selectors aligned with the visible page."""
        if self.year_combo.findData(year) < 0:
            self._refresh_year_combo(year)

        self.month_combo.blockSignals(True)
        month_index = self.month_combo.findData(month)
        if month_index >= 0:
            self.month_combo.setCurrentIndex(month_index)
        self.month_combo.blockSignals(False)

        self.year_combo.blockSignals(True)
        year_index = self.year_combo.findData(year)
        if year_index >= 0:
            self.year_combo.setCurrentIndex(year_index)
        self.year_combo.blockSignals(False)

    def _show_previous_month(self) -> None:
        """Move the visible calendar page backward by one month."""
        self.calendar.showPreviousMonth()

    def _show_next_month(self) -> None:
        """Move the visible calendar page forward by one month."""
        self.calendar.showNextMonth()

    def _on_month_changed(self, index: int) -> None:
        """Jump directly to a chosen month from the popup header."""
        if index < 0:
            return
        month = self.month_combo.itemData(index)
        if not month:
            return
        self.calendar.setCurrentPage(self.calendar.yearShown(), int(month))

    def _on_year_changed(self, index: int) -> None:
        """Jump directly to a chosen year from the popup header."""
        if index < 0:
            return
        year = self.year_combo.itemData(index)
        if not year:
            return
        self.calendar.setCurrentPage(int(year), self.calendar.monthShown())

    def _on_date_chosen(self, selected_date: QDate) -> None:
        """Commit a chosen date back into the owning editor and close the popup."""
        self.date_selected.emit(selected_date)
        self.hide()

    def _interactive_popup_surfaces(self) -> tuple[QWidget, ...]:
        """Return all visible surfaces that count as inside the active picker."""
        surfaces: list[QWidget] = [self]

        if self.owner_edit is not None:
            surfaces.append(self.owner_edit)

        for selector_name in ("month_combo", "year_combo"):
            selector = getattr(self, selector_name, None)
            header_popup = getattr(selector, "header_popup", None) if selector is not None else None
            if header_popup is not None and header_popup.isVisible():
                surfaces.append(header_popup)

        return tuple(surfaces)

    def _global_rect_for_widget(self, widget: QWidget | None) -> QRect | None:
        """Return one widget rect in global coordinates when visible."""
        if widget is None or not widget.isVisible():
            return None
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())

    def _global_pos_within_picker(self, global_pos: QPoint) -> bool:
        """Return True when a click lands inside the picker or its attached controls."""
        for surface in self._interactive_popup_surfaces():
            surface_rect = self._global_rect_for_widget(surface)
            if surface_rect is not None and surface_rect.adjusted(-2, -2, 2, 2).contains(global_pos):
                return True
        return False

    def eventFilter(self, watched, event):
        """Close the picker when the user clicks outside any active picker surface."""
        if (
            self.isVisible()
            and event.type() == QEvent.Type.MouseButtonPress
            and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton
        ):
            global_pos = event.globalPosition().toPoint()
            if not self._global_pos_within_picker(global_pos):
                self.hide()

        return super().eventFilter(watched, event)

    def show_for_editor(self, date_edit: ThemedDateEdit) -> None:
        """Open the popup below the supplied editor and clamp it on-screen."""
        self.owner_edit = date_edit
        self.setStyleSheet(date_edit.window().styleSheet())

        selected_date = date_edit.date()
        self.calendar.setMinimumDate(date_edit.minimumDate())
        self.calendar.setMaximumDate(date_edit.maximumDate())
        self.calendar.setSelectedDate(selected_date)
        self._refresh_year_combo(selected_date.year())
        self.calendar.setCurrentPage(selected_date.year(), selected_date.month())
        self._sync_title(selected_date.year(), selected_date.month())

        popup_width = max(324, date_edit.width() + 58)
        popup_height = 294
        self.resize(popup_width, popup_height)

        anchor_below = date_edit.mapToGlobal(QPoint(0, date_edit.height() + 8))
        screen = QApplication.screenAt(anchor_below)
        if screen is None:
            screen = date_edit.screen()
        geometry = screen.availableGeometry() if screen is not None else date_edit.window().frameGeometry()

        x = max(
            geometry.left() + 8,
            min(anchor_below.x(), geometry.right() - popup_width - 8),
        )
        y = anchor_below.y()

        if y + popup_height > geometry.bottom() - 8:
            anchor_above = date_edit.mapToGlobal(QPoint(0, -(popup_height + 8)))
            y = max(
                geometry.top() + 8,
                min(anchor_above.y(), geometry.bottom() - popup_height - 8),
            )

        self.move(x, y)
        app = QApplication.instance()
        if app is not None and not self._app_event_filter_installed:
            app.installEventFilter(self)
            self._app_event_filter_installed = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.calendar.setFocus()

    def hideEvent(self, event) -> None:
        """Reset popup ownership and notify the parent when it closes."""
        app = QApplication.instance()
        if app is not None and self._app_event_filter_installed:
            app.removeEventFilter(self)
            self._app_event_filter_installed = False
        self.owner_edit = None
        super().hideEvent(event)
        self.closed.emit()


class ThemedDateEdit(QDateEdit):
    """Read-only themed date field backed by a custom popup calendar."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setKeyboardTracking(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.calendar_popup_widget = ThemedCalendarPopup(self)
        self.calendar_popup_widget.date_selected.connect(self._apply_selected_date)

        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setReadOnly(True)
            line_edit.setCursor(Qt.CursorShape.PointingHandCursor)
            line_edit.installEventFilter(self)

    def _apply_selected_date(self, selected_date: QDate) -> None:
        """Update the field when the custom popup returns a chosen date."""
        self.setDate(selected_date)

    def toggle_calendar_popup(self) -> None:
        """Toggle the custom themed popup instead of Qt's native one."""
        if self.calendar_popup_widget.isVisible():
            self.calendar_popup_widget.hide()
            return
        self.calendar_popup_widget.show_for_editor(self)

    def eventFilter(self, watched, event):
        """Treat clicks on the inner line-edit as clicks on the whole field."""
        line_edit = self.lineEdit()
        if watched is line_edit and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        ):
            if getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
                self.toggle_calendar_popup()
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        """Open the themed calendar when the field itself is clicked."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_calendar_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        """Open or close the themed calendar for common picker keys."""
        if event.key() == Qt.Key.Key_Escape and self.calendar_popup_widget.isVisible():
            self.calendar_popup_widget.hide()
            event.accept()
            return

        if event.key() in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
            Qt.Key.Key_F4,
        ):
            self.toggle_calendar_popup()
            event.accept()
            return

        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        """Ignore wheel scrolling so dates do not shift accidentally."""
        event.ignore()


class HeaderColorPopup(QFrame):
    """Dedicated popup list for selecting workbook header colors."""

    color_index_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HeaderColorPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame()
        self.panel.setObjectName("HeaderColorPopupPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setSpacing(0)
        panel_layout.setContentsMargins(8, 8, 8, 8)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("HeaderColorList")
        self.list_widget.setSpacing(4)
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        panel_layout.addWidget(self.list_widget)
        layout.addWidget(self.panel)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Emit the selected source-combo index and close the popup."""
        selected_index = item.data(Qt.ItemDataRole.UserRole)
        if selected_index is None:
            return
        self.color_index_selected.emit(int(selected_index))
        self.hide()

    def _visible_row_count(self) -> int:
        """Clamp the popup height to three visible color rows."""
        return max(1, min(3, self.list_widget.count()))

    def refresh_from_combo(self, combo: QComboBox) -> None:
        """Mirror the combo items into the dedicated popup list."""
        self.list_widget.clear()
        for index in range(combo.count()):
            item = QListWidgetItem(combo.itemIcon(index), combo.itemText(index))
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)

        self.list_widget.setCurrentRow(max(0, combo.currentIndex()))

    def show_for_combo(self, combo: QComboBox) -> None:
        """Position the popup beneath the combo and sync the current selection."""
        self.setStyleSheet(combo.window().styleSheet())
        self.refresh_from_combo(combo)

        row_height = self.list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 36
        visible_rows = self._visible_row_count()
        list_height = (row_height * visible_rows) + (self.list_widget.spacing() * (visible_rows - 1))
        popup_height = list_height + 32
        popup_width = max(combo.width(), 196)

        anchor = combo.mapToGlobal(QPoint(0, combo.height() + 6))
        self.resize(popup_width, popup_height)
        self.move(anchor)
        self.show()
        self.raise_()
        self.activateWindow()
        self.list_widget.setFocus()
        current_item = self.list_widget.currentItem()
        if current_item is not None:
            self.list_widget.scrollToItem(current_item, QListWidget.ScrollHint.PositionAtCenter)


class HeaderColorComboBox(FloatingPopupComboBox):
    """Combo box display shell that uses a dedicated popup for color choices."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.color_popup = HeaderColorPopup(self)
        self.color_popup.color_index_selected.connect(self._apply_popup_selection)

    def _apply_popup_selection(self, index: int) -> None:
        """Apply one popup-selected color back into the combo display."""
        self.setCurrentIndex(index)

    def showPopup(self):
        """Show the dedicated header-color popup instead of the native combo popup."""
        if not self.isEnabled():
            return
        self.color_popup.show_for_combo(self)

    def hidePopup(self):
        """Hide the dedicated popup when Qt requests the combo to close."""
        self.color_popup.hide()


class FolderTabButton(QPushButton):
    """Small folder-style tab button with a double-click signal."""

    double_clicked = pyqtSignal()

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("RecipientFolderTab")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, event):
        """Emit a dedicated signal so the popup can expand the editor."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class RecipientOverridePopup(QFrame):
    """Compact popup for manual recipient overrides."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RecipientPanel")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())

        self.expanded = False

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame()
        self.shell.setObjectName("RecipientPopupShell")
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setSpacing(0)
        shell_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_strip = QWidget()
        self.tab_strip.setObjectName("RecipientTabStrip")
        tab_layout = QHBoxLayout(self.tab_strip)
        tab_layout.setSpacing(6)
        tab_layout.setContentsMargins(12, 0, 12, 0)

        self.to_tab_button = FolderTabButton("To")
        self.to_tab_button.clicked.connect(lambda _checked=False: self._on_tab_clicked(0))
        self.to_tab_button.double_clicked.connect(lambda: self._on_tab_double_clicked(0))
        tab_layout.addWidget(self.to_tab_button)

        self.cc_tab_button = FolderTabButton("CC")
        self.cc_tab_button.clicked.connect(lambda _checked=False: self._on_tab_clicked(1))
        self.cc_tab_button.double_clicked.connect(lambda: self._on_tab_double_clicked(1))
        tab_layout.addWidget(self.cc_tab_button)
        tab_layout.addStretch(1)
        shell_layout.addWidget(self.tab_strip)

        self.panel = QFrame()
        self.panel.setObjectName("RecipientTabPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setSpacing(0)
        panel_layout.setContentsMargins(14, 14, 14, 14)

        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("RecipientTabStack")

        self.to_input = QPlainTextEdit()
        self.to_input.setPlaceholderText("name@example.com; team@example.com")
        self._configure_editor(self.to_input)
        self.editor_stack.addWidget(self._wrap_editor(self.to_input))

        self.cc_input = QPlainTextEdit()
        self.cc_input.setPlaceholderText("observer@example.com")
        self._configure_editor(self.cc_input)
        self.editor_stack.addWidget(self._wrap_editor(self.cc_input))

        panel_layout.addWidget(self.editor_stack)
        shell_layout.addWidget(self.panel)
        layout.addWidget(self.shell)

        self.tab_bridge = QFrame(self.shell)
        self.tab_bridge.setObjectName("RecipientTabBridge")
        self.tab_bridge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.tab_bridge.hide()

        self.tab_buttons = (self.to_tab_button, self.cc_tab_button)

        self.setMinimumWidth(424)
        self.resize(436, 132)
        self._set_active_tab(0)
        self._set_expanded(False)

    def _wrap_editor(self, editor: QPlainTextEdit) -> QWidget:
        """Wrap the editor so each tab page keeps clean, even padding."""
        page = QWidget()
        page.setObjectName("RecipientTabPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addWidget(editor)
        return page

    def _configure_editor(self, editor: QPlainTextEdit) -> None:
        """Apply compact popup-editor defaults."""
        editor.setMinimumHeight(38)
        editor.setMaximumHeight(38)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setTabChangesFocus(True)

    def _active_editor(self) -> QPlainTextEdit:
        """Return the editor for the currently selected recipient tab."""
        return self.to_input if self.editor_stack.currentIndex() == 0 else self.cc_input

    def _set_active_tab(self, index: int) -> None:
        """Switch the visible editor page and update the folder tab styling."""
        self.editor_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.tab_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.blockSignals(False)
        self._reposition_tab_bridge()
        self._focus_active_editor()

    def _set_expanded(self, expanded: bool) -> None:
        """Toggle compact vs expanded editor height inside the popup."""
        self.expanded = expanded
        editor_height = 132 if expanded else 46
        popup_height = 226 if expanded else 132

        for editor in (self.to_input, self.cc_input):
            editor.setMinimumHeight(editor_height)
            editor.setMaximumHeight(editor_height)
            editor.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
                if expanded
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

        self.resize(self.width(), popup_height)
        self._reposition_tab_bridge()

    def _on_tab_clicked(self, index: int) -> None:
        """Switch tabs without changing the current compact/expanded state."""
        self._set_active_tab(index)

    def _on_tab_double_clicked(self, index: int) -> None:
        """Expand or collapse the active tab editor on tab double-click."""
        same_tab = index == self.editor_stack.currentIndex()
        self._set_active_tab(index)
        self._set_expanded(not self.expanded if same_tab else True)

    def _focus_active_editor(self) -> None:
        """Move focus into the active tab's editor when tabs change."""
        self._active_editor().setFocus()

    def _reposition_tab_bridge(self) -> None:
        """Bridge the active tab to the panel so it reads like a real folder tab."""
        if not self.isVisible():
            return

        active_button = next((button for button in self.tab_buttons if button.isChecked()), None)
        if active_button is None:
            self.tab_bridge.hide()
            return

        button_top_left = active_button.mapTo(self.shell, QPoint(0, 0))
        panel_top_left = self.panel.mapTo(self.shell, QPoint(0, 0))
        bridge_width = max(32, active_button.width() - 8)
        bridge_height = 9
        bridge_x = button_top_left.x() + 4
        bridge_y = panel_top_left.y() - 4
        self.tab_bridge.setGeometry(bridge_x, bridge_y, bridge_width, bridge_height)
        self.tab_bridge.show()
        self.tab_bridge.raise_()

    def resizeEvent(self, event):
        """Keep the active-tab bridge aligned during popup resizes."""
        super().resizeEvent(event)
        self._reposition_tab_bridge()

    def show_for_button(self, button: QWidget) -> None:
        """Show the popup aligned beneath the anchor button."""
        self._set_expanded(False)
        self._set_active_tab(0)
        button_bottom_left = button.mapToGlobal(QPoint(0, button.height() + 8))
        target_x = button_bottom_left.x()
        target_y = button_bottom_left.y()

        host_window = button.window()
        if isinstance(host_window, QWidget):
            host_frame = host_window.frameGeometry()
            inset = 16
            min_x = host_frame.left() + inset
            max_x = host_frame.right() - self.width() - inset + 1
            if max_x < min_x:
                max_x = min_x
            target_x = max(min_x, min(target_x, max_x))

            min_y = host_frame.top() + inset
            max_y = host_frame.bottom() - self.height() - inset + 1
            preferred_above_y = button.mapToGlobal(QPoint(0, -self.height() - 8)).y()
            if target_y > max_y:
                target_y = preferred_above_y if preferred_above_y >= min_y else max(min_y, max_y)

        self.move(QPoint(target_x, target_y))
        self.show()
        self.raise_()
        self.to_input.setFocus()
        self.to_input.selectAll()
        self._reposition_tab_bridge()

    def hideEvent(self, event):
        """Notify the parent when the popup closes."""
        self._set_expanded(False)
        self.tab_bridge.hide()
        self.closed.emit()
        super().hideEvent(event)


class SettingsPopup(QFrame):
    """Compact popup for less-frequently changed run settings."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame()
        self.shell.setObjectName("SettingsPopupShell")
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setSpacing(0)
        shell_layout.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame()
        self.panel.setObjectName("SettingsPopupPanel")
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setSpacing(10)
        self.panel_layout.setContentsMargins(14, 14, 14, 14)

        shell_layout.addWidget(self.panel)
        layout.addWidget(self.shell)

        self.setMinimumWidth(246)

    def add_widget(self, widget: QWidget) -> None:
        """Add one settings control to the popup panel."""
        self.panel_layout.addWidget(widget)

    def add_spacing(self, amount: int) -> None:
        """Insert a little breathing room between popup controls."""
        self.panel_layout.addSpacing(amount)

    def show_for_button(self, button: QWidget) -> None:
        """Show the popup aligned beneath the anchor button."""
        self.setStyleSheet(button.window().styleSheet())
        self.adjustSize()

        button_bottom_right = button.mapToGlobal(QPoint(button.width() - self.width(), button.height() + 8))
        target_x = button_bottom_right.x()
        target_y = button_bottom_right.y()

        host_window = button.window()
        if isinstance(host_window, QWidget):
            host_frame = host_window.frameGeometry()
            inset = 16
            min_x = host_frame.left() + inset
            max_x = host_frame.right() - self.width() - inset + 1
            if max_x < min_x:
                max_x = min_x
            target_x = max(min_x, min(target_x, max_x))

            min_y = host_frame.top() + inset
            max_y = host_frame.bottom() - self.height() - inset + 1
            preferred_above_y = button.mapToGlobal(QPoint(0, -self.height() - 8)).y()
            if target_y > max_y:
                target_y = preferred_above_y if preferred_above_y >= min_y else max(min_y, max_y)

        self.move(QPoint(target_x, target_y))
        self.show()
        self.raise_()
        self.activateWindow()

    def hideEvent(self, event):
        """Notify the parent when the popup closes."""
        self.closed.emit()
        super().hideEvent(event)


class ThemedMessageDialog(QDialog):
    """Custom in-theme modal dialog for information, warnings, errors, and confirmations."""

    TONE_META = {
        "info": {
            "pill": "INFO",
            "glyph": "i",
            "bg": "#082f49",
            "border": "#0e7490",
            "text": "#67e8f9",
        },
        "success": {
            "pill": "DONE",
            "glyph": "✓",
            "bg": "#052e16",
            "border": "#166534",
            "text": "#86efac",
        },
        "warning": {
            "pill": "WARNING",
            "glyph": "!",
            "bg": "#3b2503",
            "border": "#854d0e",
            "text": "#fbbf24",
        },
        "error": {
            "pill": "ERROR",
            "glyph": "!",
            "bg": "#3b0a10",
            "border": "#991b1b",
            "text": "#fda4af",
        },
        "question": {
            "pill": "CONFIRM",
            "glyph": "?",
            "bg": "#172554",
            "border": "#1d4ed8",
            "text": "#93c5fd",
        },
    }

    BUTTON_TEXT = {
        "ok": "OK",
        "yes": "Yes",
        "no": "No",
        "cancel": "Cancel",
    }

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        tone: str = "info",
        buttons: tuple[str, ...] = ("ok",),
        default_button: str = "ok",
        title_font_family: str = "",
    ):
        super().__init__(parent)
        self.setObjectName("ThemedMessageDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())

        self.title_font_family = title_font_family
        self._drag_offset = None
        self._buttons = buttons or ("ok",)
        self._close_choice = self._buttons[-1] if len(self._buttons) > 1 else self._buttons[0]
        self.choice = self._close_choice
        tone_meta = dict(self.TONE_META.get(tone, self.TONE_META["info"]))

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame()
        self.shell.setObjectName("DialogShell")
        shadow = QGraphicsDropShadowEffect(self.shell)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(2, 6, 23, 180))
        self.shell.setGraphicsEffect(shadow)
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setSpacing(14)
        shell_layout.setContentsMargins(18, 18, 18, 16)
        layout.addWidget(self.shell)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(6)
        title_stack.setContentsMargins(0, 0, 0, 0)

        tone_pill = QLabel(tone_meta["pill"])
        tone_pill.setObjectName("DialogTonePill")
        tone_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tone_pill.setStyleSheet(
            f"""
            QLabel#DialogTonePill {{
                background-color: {tone_meta['bg']};
                color: {tone_meta['text']};
                border: 1px solid {tone_meta['border']};
                border-radius: 10px;
                padding: 3px 9px;
                font-size: 10px;
                font-weight: 700;
            }}
            """
        )
        title_stack.addWidget(tone_pill, 0, Qt.AlignmentFlag.AlignLeft)

        title_label = QLabel(title)
        title_label.setObjectName("DialogTitle")
        title_label.setWordWrap(True)
        if self.title_font_family:
            dialog_title_font = QFont(self.title_font_family, 15)
            dialog_title_font.setWeight(QFont.Weight.Black)
            title_label.setFont(dialog_title_font)
        title_stack.addWidget(title_label)
        top_row.addLayout(title_stack, 1)

        close_btn = QPushButton("×")
        close_btn.setObjectName("DialogCloseButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(lambda: self._finish(self._close_choice))
        top_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        shell_layout.addLayout(top_row)

        if message.strip():
            divider = QFrame()
            divider.setObjectName("DialogDivider")
            divider.setFixedHeight(1)
            shell_layout.addWidget(divider)

            body_label = QLabel(message)
            body_label.setObjectName("DialogBody")
            body_label.setWordWrap(True)
            body_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            body_label.setMinimumWidth(360)
            body_label.setMaximumWidth(420)
            shell_layout.addWidget(body_label)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        buttons_row.setContentsMargins(0, 4, 0, 0)
        buttons_row.addStretch(1)

        for button_key in self._buttons:
            button = QPushButton(self.BUTTON_TEXT.get(button_key, button_key.title()))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(38)
            button.setMinimumWidth(92)
            if button_key in {"ok", "yes"}:
                button.setObjectName("DialogPrimaryButton")
            else:
                button.setObjectName("DialogSecondaryButton")
            button.clicked.connect(lambda _checked=False, key=button_key: self._finish(key))
            button.setAutoDefault(button_key == default_button)
            button.setDefault(button_key == default_button)
            buttons_row.addWidget(button)

        shell_layout.addLayout(buttons_row)

        self.adjustSize()
        self.setMinimumWidth(self.shell.sizeHint().width())

    def _finish(self, choice: str) -> None:
        """Store the selected button and close using the matching dialog result."""
        self.choice = choice
        if choice in {"ok", "yes"}:
            self.accept()
            return
        self.reject()

    def reject(self) -> None:
        """Treat escape and close actions the same way as the explicit close button."""
        self.choice = self._close_choice
        super().reject()

    def showEvent(self, event):
        """Center the dialog over the parent window when shown."""
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            parent_center = parent.frameGeometry().center()
            dialog_rect = self.frameGeometry()
            dialog_rect.moveCenter(parent_center)
            self.move(dialog_rect.topLeft())

    def mousePressEvent(self, event):
        """Allow dragging the frameless dialog by its surface."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Move the dialog while dragging."""
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_offset is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Clear drag state after moving the dialog."""
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class UnderwaterLightOverlay(QWidget):
    """Soft animated caustic light that enriches the idle wallpaper without blocking UI."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._phase = random.uniform(0.0, math.tau)
        seed_rng = random.Random(41209)
        self._caustic_seeds = [
            (
                seed_rng.uniform(0.02, 0.98),
                seed_rng.uniform(0.04, 0.96),
                seed_rng.uniform(0.025, 0.075),
                seed_rng.uniform(0.018, 0.060),
                seed_rng.uniform(0.22, 0.58),
                seed_rng.uniform(0.24, 0.66),
                seed_rng.uniform(0.0, math.tau),
                seed_rng.uniform(0.0, math.tau),
            )
            for _ in range(34)
        ]
        self._caustic_refresh_tick = 0
        self._caustic_dirty = True
        self._cached_caustic_size = QSize()
        self._cached_primary_caustic = QPixmap()
        self._cached_secondary_caustic = QPixmap()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._advance)
        self._timer.stop()

    def _advance(self) -> None:
        """Drift the caustic light slowly so it feels alive but never distracting."""
        self._phase = (self._phase + 0.052) % (math.tau * 8.0)
        self._caustic_refresh_tick = (self._caustic_refresh_tick + 1) % 4096
        if self._caustic_refresh_tick % 3 == 0:
            self._caustic_dirty = True
        if self.isVisible():
            if self._caustic_dirty:
                self._refresh_caustic_cache()
            self.update()

    def resizeEvent(self, event) -> None:
        """Refresh the cached caustics when the overlay geometry changes."""
        self._caustic_dirty = True
        super().resizeEvent(event)

    def showEvent(self, event) -> None:
        """Prime the caustic cache before the overlay becomes visible."""
        self._caustic_dirty = True
        self._refresh_caustic_cache(force=True)
        if not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        """Suspend the caustic animation whenever the overlay is hidden."""
        self._timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _smoothstep(edge0: float, edge1: float, value: float) -> float:
        """Return a smooth 0..1 transition between two edges."""
        if edge0 == edge1:
            return 0.0
        t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def _ridge(value: float, width: float) -> float:
        """Return a soft ridge intensity centered around zero."""
        if width <= 0.0:
            return 0.0
        t = max(0.0, 1.0 - abs(value) / width)
        return t * t

    def _build_caustic_image(self, pixel_width: int, pixel_height: int, phase: float) -> QImage:
        """Generate an irregular underwater caustic network for the sandy floor."""
        image = QImage(pixel_width, pixel_height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        phase *= 1.42

        seed_positions = []
        for anchor_x, anchor_y, drift_x, drift_y, speed_x, speed_y, phase_x, phase_y in self._caustic_seeds:
            sx = (
                anchor_x
                + math.sin((phase * speed_x) + phase_x) * drift_x * 1.12
                + math.cos((phase * speed_x * 0.54) - phase_y) * drift_x * 0.34
            )
            sy = (
                anchor_y
                + math.cos((phase * speed_y) + phase_y) * drift_y * 1.14
                + math.sin((phase * speed_y * 0.62) - phase_x) * drift_y * 0.32
            )
            seed_positions.append((sx, sy))

        for py in range(pixel_height):
            ny = py / max(1, pixel_height - 1)
            fade_y = self._smoothstep(0.04, 0.28, ny)
            bottom_bias = self._smoothstep(0.52, 1.0, ny)

            for px in range(pixel_width):
                nx = px / max(1, pixel_width - 1)

                sample_x = (
                    nx
                    + 0.028 * math.sin((ny * 7.0) + phase * 0.44)
                    + 0.012 * math.sin((ny * 16.0) - phase * 0.82)
                )
                sample_y = (
                    ny
                    + 0.022 * math.sin((nx * 6.2) - phase * 0.36)
                    + 0.010 * math.sin((nx * 15.4) + phase * 0.58)
                )

                nearest_1 = 1e9
                nearest_2 = 1e9
                nearest_3 = 1e9
                for sx, sy in seed_positions:
                    dx = sample_x - sx
                    dy = (sample_y - sy) * 1.18
                    distance = dx * dx + dy * dy
                    if distance < nearest_1:
                        nearest_3 = nearest_2
                        nearest_2 = nearest_1
                        nearest_1 = distance
                    elif distance < nearest_2:
                        nearest_3 = nearest_2
                        nearest_2 = distance
                    elif distance < nearest_3:
                        nearest_3 = distance

                edge_gap = max(0.0, math.sqrt(nearest_2) - math.sqrt(nearest_1))
                node_gap = max(0.0, math.sqrt(nearest_3) - math.sqrt(nearest_1))
                edge = math.exp(-edge_gap * 54.0)
                node = math.exp(-node_gap * 34.0)
                shimmer = 0.80 + 0.20 * math.sin((sample_x * 9.4) + (sample_y * 4.1) - phase * 0.46)
                intensity = (edge * 0.58 + node * 0.11) * shimmer

                envelope_x = 0.82 + 0.18 * math.sin((nx * 2.8) + phase * 0.22)
                envelope = fade_y * (0.74 + bottom_bias * 0.30) * envelope_x
                intensity = max(0.0, min(1.0, intensity * envelope))

                if intensity <= 0.02:
                    continue

                warm_mix = 0.28 + bottom_bias * 0.30
                red = int(178 + intensity * (44 + 12 * warm_mix))
                green = int(226 + intensity * (20 + 8 * warm_mix))
                blue = int(242 + intensity * 10)
                alpha = int(max(0, min(255, (intensity ** 1.95) * 108)))
                image.setPixelColor(px, py, QColor(red, green, blue, alpha))

        return image

    def _refresh_caustic_cache(self, force: bool = False) -> None:
        """Rebuild the heavy caustic textures only when the cache is stale."""
        if self.width() <= 24 or self.height() <= 24:
            return

        size = self.size()
        if (
            not force
            and not self._caustic_dirty
            and self._cached_caustic_size == size
            and not self._cached_primary_caustic.isNull()
            and not self._cached_secondary_caustic.isNull()
        ):
            return

        phase = self._phase
        self._cached_primary_caustic = QPixmap.fromImage(
            self._build_caustic_image(176, 96, phase)
        )
        self._cached_secondary_caustic = QPixmap.fromImage(
            self._build_caustic_image(136, 76, phase + 0.9)
        )
        self._cached_caustic_size = QSize(size)
        self._caustic_dirty = False

    def paintEvent(self, event) -> None:
        del event
        if self.width() <= 24 or self.height() <= 24:
            return
        if self._cached_primary_caustic.isNull() or self._cached_secondary_caustic.isNull():
            self._refresh_caustic_cache(force=True)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        clip = QPainterPath()
        clip.addRoundedRect(
            0.0,
            0.0,
            float(self.width()),
            float(self.height()),
            16.0,
            16.0,
        )
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)

        width = float(self.width())
        height = float(self.height())
        phase = self._phase

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)

        source_glow = QRadialGradient(
            width * 0.50,
            height * 0.08,
            width * 0.72,
            width * 0.50,
            height * 0.05,
        )
        source_glow.setColorAt(0.0, QColor(255, 248, 228, 34))
        source_glow.setColorAt(0.10, QColor(242, 248, 240, 20))
        source_glow.setColorAt(0.26, QColor(184, 236, 252, 11))
        source_glow.setColorAt(0.56, QColor(88, 192, 228, 4))
        source_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), source_glow)

        surface_haze = QLinearGradient(0.0, 0.0, 0.0, height * 0.68)
        surface_haze.setColorAt(0.0, QColor(214, 248, 255, 24))
        surface_haze.setColorAt(0.14, QColor(144, 230, 255, 14))
        surface_haze.setColorAt(0.42, QColor(68, 164, 210, 8))
        surface_haze.setColorAt(0.74, QColor(26, 96, 156, 3))
        surface_haze.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), surface_haze)

        top_sheen = QLinearGradient(0.0, height * 0.13, 0.0, height * 0.26)
        top_sheen.setColorAt(0.0, QColor(255, 248, 228, 8))
        top_sheen.setColorAt(0.34, QColor(190, 240, 255, 6))
        top_sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0.0, height * 0.13, width, height * 0.13), top_sheen)

        sun_patch = QRadialGradient(
            width * (0.50 + math.sin(phase * 0.10) * 0.015),
            height * 0.04,
            width * 0.34,
            width * 0.50,
            height * 0.02,
        )
        sun_patch.setColorAt(0.0, QColor(255, 249, 228, 22))
        sun_patch.setColorAt(0.16, QColor(236, 248, 252, 14))
        sun_patch.setColorAt(0.42, QColor(160, 226, 246, 7))
        sun_patch.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), sun_patch)

        upper_refract = QLinearGradient(0.0, height * 0.14, width, height * 0.28)
        upper_refract.setColorAt(0.0, QColor(0, 0, 0, 0))
        upper_refract.setColorAt(0.24, QColor(192, 240, 255, 3))
        upper_refract.setColorAt(0.52, QColor(255, 248, 232, 4))
        upper_refract.setColorAt(0.78, QColor(182, 236, 255, 3))
        upper_refract.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0.0, height * 0.14, width, height * 0.14), upper_refract)

        shimmer_specs = (
            (0.28, 0.18, 0.24, 0.06, 9),
            (0.50, 0.22, 0.28, 0.07, 14),
            (0.72, 0.28, 0.22, 0.06, 8),
        )
        for index, (ax, ay, rx_scale, ry_scale, peak_alpha) in enumerate(shimmer_specs):
            cx = width * ax + math.sin((phase * 0.36) + index * 1.45) * width * 0.044
            cy = height * ay + math.cos((phase * 0.26) + index * 0.7) * height * 0.018
            rx = width * rx_scale
            ry = height * ry_scale
            radius = max(rx, ry)
            pool = QRadialGradient(cx, cy, radius, cx, cy)
            pool.setColorAt(0.0, QColor(248, 250, 244, peak_alpha))
            pool.setColorAt(0.18, QColor(172, 238, 255, max(4, int(peak_alpha * 0.6))))
            pool.setColorAt(0.50, QColor(88, 190, 232, max(2, int(peak_alpha * 0.24))))
            pool.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(pool)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(cx - rx, cy - ry, rx * 2.0, ry * 2.0))

        lower_ambient = QLinearGradient(0.0, height * 0.42, 0.0, height)
        lower_ambient.setColorAt(0.0, QColor(0, 0, 0, 0))
        lower_ambient.setColorAt(0.52, QColor(102, 206, 238, 6))
        lower_ambient.setColorAt(0.82, QColor(142, 226, 246, 9))
        lower_ambient.setColorAt(1.0, QColor(196, 244, 255, 10))
        painter.fillRect(QRectF(0.0, height * 0.42, width, height * 0.58), lower_ambient)

        caustic_fade = QLinearGradient(0.0, height * 0.60, 0.0, height)
        caustic_fade.setColorAt(0.0, QColor(0, 0, 0, 0))
        caustic_fade.setColorAt(0.36, QColor(140, 234, 252, 8))
        caustic_fade.setColorAt(1.0, QColor(222, 248, 255, 14))
        painter.fillRect(QRectF(0.0, height * 0.60, width, height * 0.40), caustic_fade)

        primary_drift_x = math.sin(phase * 0.54) * width * 0.010
        primary_drift_y = math.cos(phase * 0.46) * height * 0.012
        primary_scale = 1.0 + math.sin(phase * 0.18) * 0.022
        caustic_rect = QRectF(
            (-width * 0.05) + primary_drift_x,
            (height * 0.55) + primary_drift_y,
            width * (1.10 * primary_scale),
            height * (0.52 * primary_scale),
        )
        painter.setOpacity(0.96)
        painter.drawPixmap(caustic_rect.toRect(), self._cached_primary_caustic)

        secondary_drift_x = math.cos((phase * 0.60) + 0.7) * width * 0.012
        secondary_drift_y = math.sin((phase * 0.50) - 0.5) * height * 0.014
        secondary_scale = 0.985 + math.cos(phase * 0.16) * 0.018
        painter.setOpacity(0.34)
        painter.drawPixmap(
            QRectF(
                caustic_rect.x() + width * 0.014 + secondary_drift_x,
                caustic_rect.y() + height * 0.018 + secondary_drift_y,
                caustic_rect.width() * secondary_scale,
                caustic_rect.height() * (secondary_scale * 0.985),
            ).toRect(),
            self._cached_secondary_caustic,
        )
        painter.setOpacity(1.0)

        sand_glow = QLinearGradient(0.0, height * 0.80, 0.0, height)
        sand_glow.setColorAt(0.0, QColor(0, 0, 0, 0))
        sand_glow.setColorAt(0.44, QColor(170, 238, 248, 7))
        sand_glow.setColorAt(0.84, QColor(225, 248, 244, 9))
        sand_glow.setColorAt(1.0, QColor(255, 248, 230, 10))
        painter.fillRect(QRectF(0.0, height * 0.80, width, height * 0.20), sand_glow)

        painter.restore()
        painter.end()


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.extraction_thread = None
        self.drag_offset = None
        self.developer_mode_enabled = False
        self.dev_preview_compact_mode = False
        self._launch_intro_active = True
        self._recipient_popup_closed_by_button = False
        self._settings_popup_closed_by_button = False
        self._close_requested_after_stop = False
        self.password_visible = False
        self.title_font_family = ""
        self.loading_gif_path = self._resolve_runtime_loading_asset()
        self.idle_wallpaper_path = self._resolve_idle_wallpaper_asset()
        self._runtime_loading_opacity = 0.24
        self._runtime_loading_inset = 2
        self._runtime_loading_speed = 100
        self.runtime_loading_watermark = None
        self.runtime_loading_opacity = None
        self.runtime_loading_movie = None
        self.idle_wallpaper = None
        self.idle_wallpaper_source = QPixmap(str(self.idle_wallpaper_path))
        self.idle_wallpaper_opacity = None
        self.idle_light_overlay = None
        self.idle_bubble_overlay = None
        self.bubble_pop_audio = BubblePopAudio(self)
        self.header_color_options = self._load_header_color_options()
        self.header_fill_color = self._load_saved_header_fill_color()
        self.source_mode_options = (
            ("IRT Results", "irt"),
            ("Template", "manual"),
        )
        self.developer_mode_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.developer_mode_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.developer_mode_shortcut.setAutoRepeat(False)
        self.developer_mode_shortcut.activated.connect(self._toggle_developer_mode)
        self._load_custom_fonts()
        self.init_ui()
        self.load_saved_credentials()

    def _resolve_runtime_loading_asset(self) -> Path:
        """Return the one approved runtime loading animation for the app."""
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        return assets_dir / "surface.gif"

    def _resolve_idle_wallpaper_asset(self) -> Path:
        """Return the subtle idle wallpaper image for the main screen."""
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        return assets_dir / "idle_wallpaper.png"

    def _apply_window_theme(self):
        """Apply the application stylesheet."""
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        chevron_icon_url = str((assets_dir / "chevron_down.png").resolve()).replace("\\", "/")
        checkmark_icon_url = str((assets_dir / "checkmark.png").resolve()).replace("\\", "/")
        stylesheet = """
            QMainWindow {
                background: transparent;
                border: none;
            }

            QWidget#AppSurface {
                background: transparent;
                border: none;
            }

            QFrame#HeaderCard {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #101826,
                    stop: 1 #172033
                );
                border: 1px solid #22304a;
                border-radius: 18px;
            }

            QFrame#MainAuthCard,
            QFrame#SectionCard {
                background-color: #101722;
                border: 1px solid #1d293d;
                border-radius: 16px;
            }

            QLabel#IdleWallpaper {
                background: transparent;
                border: none;
            }

            QLabel#HeroTitle {
                color: #f8fafc;
                font-size: 28px;
                font-weight: 700;
                letter-spacing: 0.02em;
            }

            QLabel#SectionTitle {
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 700;
            }

            QLabel#SectionDescription {
                color: #8aa0bd;
                font-size: 11px;
            }

            QLabel#FieldLabel {
                color: #93a8c3;
                font-size: 11px;
                font-weight: 600;
            }

            QLineEdit {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 12px;
                selection-background-color: #22d3ee;
            }

            QLineEdit:focus {
                background-color: #0d1525;
                border: 1px solid #22d3ee;
            }

            QPlainTextEdit {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 12px;
                selection-background-color: #22d3ee;
            }

            QPlainTextEdit:focus {
                background-color: #0d1525;
                border: 1px solid #22d3ee;
            }

            QComboBox,
            QDateEdit {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                padding: 8px 40px 8px 11px;
                font-size: 12px;
                min-height: 20px;
            }

            QComboBox {
                combobox-popup: 0;
            }

            QComboBox:hover,
            QDateEdit:hover {
                background-color: #0d1525;
            }

            QComboBox:focus,
            QDateEdit:focus {
                border: 1px solid #22d3ee;
            }

            QComboBox:on {
                background-color: #0f1827;
                border: 1px solid #22d3ee;
            }

            QComboBox::drop-down,
            QDateEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 36px;
                border-left: 1px solid #22304a;
                border-top-right-radius: 12px;
                border-bottom-right-radius: 12px;
                background-color: #0f1827;
            }

            QComboBox::down-arrow,
            QDateEdit::down-arrow {
                image: url("__CHEVRON_ICON_URL__");
                width: 13px;
                height: 9px;
            }

            QComboBox QAbstractItemView {
                background-color: #111b2b;
                color: #f8fafc;
                border: 1px solid #325174;
                border-radius: 14px;
                padding: 8px 6px;
                selection-background-color: #17314a;
                selection-color: #f8fafc;
                outline: 0;
            }

            QComboBox QAbstractItemView::item {
                min-height: 32px;
                margin: 2px;
                padding: 0 12px;
                border-radius: 9px;
            }

            QComboBox QAbstractItemView::item:hover {
                background-color: #152538;
            }

            QComboBox QAbstractItemView::item:selected {
                background-color: #17314a;
                border: 1px solid #2c698f;
            }

            QFrame#DatePickerPopup {
                background: transparent;
                border: none;
            }

            QFrame#DatePickerPopupShell {
                background-color: rgba(11, 18, 32, 248);
                border: 1px solid #2b3d59;
                border-radius: 16px;
            }

            QWidget#DatePickerHeader {
                background-color: transparent;
                border: none;
            }

            QPushButton#DatePickerMonthCombo,
            QPushButton#DatePickerYearCombo {
                background-color: #13243a;
                color: #f8fafc;
                border: 2px solid #3d6287;
                border-radius: 20px;
                padding: 0 16px;
                min-height: 26px;
                font-size: 12px;
                font-weight: 700;
                text-align: left;
            }

            QPushButton#DatePickerMonthCombo {
                min-width: 128px;
            }

            QPushButton#DatePickerYearCombo {
                min-width: 92px;
            }

            QPushButton#DatePickerMonthCombo:hover,
            QPushButton#DatePickerYearCombo:hover {
                background-color: #18304b;
                border-color: #4e84b2;
            }

            QPushButton#DatePickerMonthCombo:pressed,
            QPushButton#DatePickerYearCombo:pressed {
                background-color: #1b3551;
                border-color: #22d3ee;
            }

            QComboBox#DatePickerMonthCombo::down-arrow,
            QComboBox#DatePickerYearCombo::down-arrow {
                image: url("__CHEVRON_ICON_URL__");
                width: 10px;
                height: 7px;
            }

            QLabel#DatePickerTitle {
                color: #f8fafc;
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.3px;
                padding: 0 4px 2px 4px;
            }

            QPushButton#DatePickerNavButton {
                background-color: #0f1827;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 18px;
                font-weight: 700;
            }

            QPushButton#DatePickerNavButton:hover {
                background-color: #152538;
                border-color: #2c698f;
            }

            QPushButton#DatePickerNavButton:pressed {
                background-color: #112638;
            }

            QCalendarWidget#DatePickerCalendar {
                background-color: transparent;
                color: #dbe7f5;
                border: none;
            }

            QCalendarWidget#DatePickerCalendar QTableView#qt_calendar_calendarview {
                background-color: transparent;
                alternate-background-color: transparent;
                border: none;
                outline: 0;
                margin: 0;
                padding: 0;
            }

            QCalendarWidget#DatePickerCalendar QTableView#qt_calendar_calendarview::item {
                border-radius: 10px;
                padding: 2px;
            }

            QCalendarWidget#DatePickerCalendar QTableView#qt_calendar_calendarview::item:hover {
                background-color: #122437;
                color: #f8fafc;
            }

            QCalendarWidget#DatePickerCalendar QTableView#qt_calendar_calendarview::item:selected {
                background-color: #22d3ee;
                color: #03131a;
            }

            QCalendarWidget#DatePickerCalendar QAbstractItemView:enabled {
                background-color: transparent;
                color: #dbe7f5;
                selection-background-color: #22d3ee;
                selection-color: #03131a;
                alternate-background-color: transparent;
                outline: 0;
                border: none;
                border-radius: 12px;
            }

            QCalendarWidget#DatePickerCalendar QAbstractItemView:disabled {
                color: #5e7088;
            }

            QCalendarWidget#DatePickerCalendar QHeaderView {
                background-color: transparent;
                border: none;
            }

            QCalendarWidget#DatePickerCalendar QHeaderView::section {
                background-color: transparent;
                color: #cbd5e1;
                border: none;
                padding: 0 0 8px 0;
                font-size: 11px;
                font-weight: 600;
            }

            QListView#DropdownPopupView {
                background-color: #111b2b;
                color: #f8fafc;
                border: 1px solid #325174;
                border-radius: 16px;
                padding: 8px;
                outline: 0;
            }

            QListView#DropdownPopupView::viewport {
                background-color: #111b2b;
                border-radius: 14px;
            }

            QListView#DropdownPopupView::item {
                min-height: 32px;
                margin: 2px 1px;
                padding: 0 12px;
                border-radius: 11px;
            }

            QListView#DropdownPopupView::item:hover {
                background-color: #152538;
            }

            QListView#DropdownPopupView::item:selected {
                background-color: #17314a;
                border: 1px solid #2c698f;
            }

            QListView#DropdownPopupView QScrollBar:vertical {
                background-color: #0c1422;
                width: 11px;
                margin: 10px 2px 10px 2px;
                border-radius: 6px;
                border: 1px solid #22304a;
            }

            QListView#DropdownPopupView QScrollBar::handle:vertical {
                background-color: #315f87;
                border-radius: 5px;
                min-height: 34px;
            }

            QListView#DropdownPopupView QScrollBar::handle:vertical:hover {
                background-color: #3c7cae;
            }

            QListView#DropdownPopupView QScrollBar::add-line:vertical,
            QListView#DropdownPopupView QScrollBar::sub-line:vertical,
            QListView#DropdownPopupView QScrollBar::add-page:vertical,
            QListView#DropdownPopupView QScrollBar::sub-page:vertical {
                background: transparent;
                height: 0px;
            }

            QFrame#CalendarHeaderPopup,
            QFrame#CalendarHeaderPopupPanel {
                background: transparent;
                border: none;
            }

            QFrame#CalendarHeaderPopupPanel {
                background-color: #111b2b;
                border: 1px solid #325174;
                border-radius: 16px;
            }

            QListWidget#CalendarHeaderList {
                background-color: transparent;
                color: #f8fafc;
                border: none;
                outline: 0;
            }

            QListWidget#CalendarHeaderList::item {
                min-height: 32px;
                margin: 2px 1px;
                padding: 0 12px;
                border-radius: 11px;
            }

            QListWidget#CalendarHeaderList::item:hover {
                background-color: #152538;
            }

            QListWidget#CalendarHeaderList::item:selected {
                background-color: #17314a;
                border: 1px solid #2c698f;
            }

            QListWidget#CalendarHeaderList QScrollBar:vertical {
                background-color: #0c1422;
                width: 12px;
                margin: 8px 0 8px 6px;
                border-radius: 6px;
                border: 1px solid #22304a;
            }

            QListWidget#CalendarHeaderList QScrollBar::handle:vertical {
                background-color: #315f87;
                border-radius: 5px;
                min-height: 34px;
            }

            QListWidget#CalendarHeaderList QScrollBar::handle:vertical:hover {
                background-color: #3c7cae;
            }

            QListWidget#CalendarHeaderList QScrollBar::add-line:vertical,
            QListWidget#CalendarHeaderList QScrollBar::sub-line:vertical,
            QListWidget#CalendarHeaderList QScrollBar::add-page:vertical,
            QListWidget#CalendarHeaderList QScrollBar::sub-page:vertical {
                background: transparent;
                height: 0px;
                border: none;
            }

            QFrame#HeaderColorPopup,
            QFrame#HeaderColorPopupPanel {
                background: transparent;
                border: none;
            }

            QFrame#HeaderColorPopupPanel {
                background-color: #111b2b;
                border: 1px solid #325174;
                border-radius: 14px;
            }

            QListWidget#HeaderColorList {
                background-color: transparent;
                color: #f8fafc;
                border: none;
                outline: 0;
            }

            QListWidget#HeaderColorList::item {
                min-height: 32px;
                margin: 2px;
                padding: 0 12px;
                border-radius: 9px;
            }

            QListWidget#HeaderColorList::item:hover {
                background-color: #152538;
            }

            QListWidget#HeaderColorList::item:selected {
                background-color: #17314a;
                border: 1px solid #2c698f;
            }

            QListWidget#HeaderColorList QScrollBar:vertical {
                background-color: #0c1422;
                width: 12px;
                margin: 8px 0 8px 6px;
                border-radius: 6px;
                border: 1px solid #22304a;
            }

            QListWidget#HeaderColorList QScrollBar::handle:vertical {
                background-color: #315f87;
                border-radius: 5px;
                min-height: 34px;
            }

            QListWidget#HeaderColorList QScrollBar::handle:vertical:hover {
                background-color: #3c7cae;
            }

            QListWidget#HeaderColorList QScrollBar::add-line:vertical,
            QListWidget#HeaderColorList QScrollBar::sub-line:vertical,
            QListWidget#HeaderColorList QScrollBar::add-page:vertical,
            QListWidget#HeaderColorList QScrollBar::sub-page:vertical {
                background: transparent;
                height: 0px;
                border: none;
            }

            QCheckBox {
                color: #cbd5e1;
                font-size: 11px;
                spacing: 8px;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 5px;
                border: 1px solid #334155;
                background-color: #0b1220;
            }

            QCheckBox::indicator:checked {
                background-color: #22d3ee;
                border: 1px solid #22d3ee;
                image: url("__CHECKMARK_ICON_URL__");
            }

            QPushButton {
                min-height: 40px;
                border-radius: 12px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 600;
            }

            QPushButton#PrimaryButton {
                background-color: #22d3ee;
                color: #03131a;
                border: 1px solid #22d3ee;
            }

            QPushButton#PrimaryButton:hover {
                background-color: #67e8f9;
                border-color: #67e8f9;
            }

            QPushButton#SecondaryButton {
                background-color: #131c2b;
                color: #dbe7f5;
                border: 1px solid #243248;
            }

            QPushButton#SecondaryButton:hover {
                background-color: #182335;
            }

            QPushButton#GhostButton {
                background-color: #101722;
                color: #9fb2ca;
                border: 1px solid #22304a;
            }

            QPushButton#GhostButton:hover {
                background-color: #131c2b;
            }

            QPushButton#StopButton {
                min-height: 36px;
                max-width: 172px;
                background-color: #221216;
                color: #fecdd3;
                border: 1px solid #7f1d1d;
            }

            QPushButton#StopButton:hover {
                background-color: #2f151b;
                color: #ffe4e6;
                border-color: #991b1b;
            }

            QPushButton#PrimaryButton:disabled,
            QPushButton#SecondaryButton:disabled,
            QPushButton#GhostButton:disabled,
            QPushButton#StopButton:disabled {
                background-color: #16202f;
                color: #5e7088;
                border-color: #16202f;
            }

            QPushButton#TitleBarButton,
            QPushButton#CloseTitleBarButton {
                min-height: 28px;
                max-height: 28px;
                min-width: 28px;
                max-width: 28px;
                border-radius: 8px;
                background-color: transparent;
                color: #9fb2ca;
                border: 1px solid transparent;
                padding: 0;
                font-size: 13px;
                font-weight: 700;
            }

            QPushButton#CloseTitleBarButton {
                font-size: 17px;
                font-weight: 800;
            }

            QPushButton#TitleBarButton:hover {
                background-color: #182335;
                color: #f8fafc;
                border-color: #243248;
            }

            QPushButton#CloseTitleBarButton:hover {
                background-color: #7f1d1d;
                color: #ffffff;
                border-color: #991b1b;
            }

            QDialog#ThemedMessageDialog {
                background: transparent;
                border: none;
            }

            QFrame#DialogShell {
                background-color: #0e1624;
                border: 1px solid #22304a;
                border-radius: 18px;
            }

            QLabel#DialogTitle {
                color: #f8fafc;
                font-size: 15px;
                font-weight: 700;
            }

            QLabel#DialogBody {
                color: #d5e1f0;
                font-size: 12px;
                line-height: 1.35em;
                background: transparent;
            }

            QFrame#DialogDivider {
                background-color: #22304a;
                border: none;
            }

            QPushButton#DialogPrimaryButton,
            QPushButton#DialogSecondaryButton,
            QPushButton#DialogCloseButton {
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
            }

            QPushButton#DialogPrimaryButton {
                background-color: #22d3ee;
                color: #03131a;
                border: 1px solid #22d3ee;
                padding: 0 14px;
            }

            QPushButton#DialogPrimaryButton:hover {
                background-color: #67e8f9;
                border-color: #67e8f9;
            }

            QPushButton#DialogSecondaryButton {
                background-color: #111a28;
                color: #cbd5e1;
                border: 1px solid #243248;
                padding: 0 14px;
            }

            QPushButton#DialogSecondaryButton:hover {
                background-color: #162131;
                color: #f8fafc;
            }

            QPushButton#DialogCloseButton {
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                background-color: transparent;
                color: #9fb2ca;
                border: 1px solid transparent;
                padding: 0;
                font-size: 17px;
                font-weight: 800;
            }

            QPushButton#DialogCloseButton:hover {
                background-color: #7f1d1d;
                color: #ffffff;
                border-color: #991b1b;
            }

            QProgressBar {
                min-height: 12px;
                border-radius: 6px;
                background-color: #0b1220;
                border: none;
                text-align: center;
                color: #e2e8f0;
                font-size: 10px;
                font-weight: 700;
            }

            QProgressBar::chunk {
                border-radius: 6px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #22d3ee,
                    stop: 1 #2563eb
                );
            }

            QLabel#ProgressLabel {
                color: #e2e8f0;
                font-size: 12px;
                font-weight: 500;
            }

            QLabel#ProgressMeta {
                color: #7f93ae;
                font-size: 11px;
            }

            QLabel#DeveloperPill {
                background-color: #2b1f0f;
                color: #fbbf24;
                border: 1px solid #5b4417;
                border-radius: 9px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
            }

            QFrame#DeveloperPanel {
                background-color: #0b1220;
                border: 1px solid #22304a;
                border-radius: 12px;
            }

            QFrame#InlineOptionsCard,
            QFrame#InlineFiltersCard {
                background-color: #0c1422;
                border: 1px solid #22304a;
                border-radius: 16px;
            }

            QFrame#HeaderInnerDivider {
                background-color: #22304a;
                border: none;
            }

            QWidget#HeaderTitleBand {
                background: transparent;
                border: none;
            }

            QFrame#WorkflowDivider {
                background-color: #22304a;
                border: none;
            }

            QPushButton#InlineToggleButton {
                min-height: 40px;
                max-height: 40px;
                border-radius: 12px;
                padding: 0 14px;
                font-size: 11px;
                font-weight: 600;
                background-color: #101722;
                color: #cbd5e1;
                border: 1px solid #243248;
            }

            QPushButton#InlineToggleButton:hover {
                background-color: #131c2b;
                color: #f8fafc;
            }

            QPushButton#InlineToggleButton:checked {
                background-color: #112638;
                color: #67e8f9;
                border-color: #1f6f8b;
            }

            QPushButton#SquareAccentButton {
                min-width: 42px;
                max-width: 42px;
                min-height: 40px;
                max-height: 40px;
                border-radius: 12px;
                padding: 0;
                font-size: 22px;
                font-weight: 700;
                background-color: #112638;
                color: #67e8f9;
                border: 1px solid #1f6f8b;
            }

            QPushButton#SquareAccentButton:hover {
                background-color: #163248;
                color: #a5f3fc;
                border-color: #22d3ee;
            }

            QFrame#RecipientPanel,
            QFrame#RecipientPopupShell,
            QWidget#RecipientTabStrip {
                background: transparent;
                border: none;
            }

            QFrame#SettingsPanel,
            QFrame#SettingsPopupShell {
                background: transparent;
                border: none;
            }

            QFrame#SettingsPopupPanel {
                background-color: #0a1220;
                border: 1px solid #22304a;
                border-radius: 14px;
            }

            QFrame#RecipientTabPanel {
                background-color: #0a1220;
                border: 1px solid #22304a;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }

            QFrame#RecipientTabBridge {
                background-color: #0a1220;
                border: none;
            }

            QPushButton#RecipientFolderTab {
                background-color: #0f1827;
                color: #8fa6c4;
                border: 1px solid #22304a;
                border-top-left-radius: 11px;
                border-top-right-radius: 11px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                padding: 0 18px;
                min-width: 60px;
                min-height: 32px;
                max-height: 32px;
                font-size: 11px;
                font-weight: 600;
                margin-top: 4px;
            }

            QPushButton#RecipientFolderTab:checked {
                background-color: #0a1220;
                color: #f8fafc;
                border-color: #314662;
                margin-top: 0px;
                padding-bottom: 3px;
                border-bottom-color: #0a1220;
            }

            QPushButton#RecipientFolderTab:hover:!checked {
                background-color: #142032;
                color: #dbe7f5;
            }
            """
        self.setStyleSheet(
            stylesheet
            .replace("__CHEVRON_ICON_URL__", chevron_icon_url)
            .replace("__CHECKMARK_ICON_URL__", checkmark_icon_url)
        )

    def _load_custom_fonts(self):
        """Load bundled fonts and cache the title font family."""
        font_path = Path(__file__).resolve().parent.parent / "assets" / "Baron_Neue_Black.otf"
        if not font_path.exists():
            return

        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return

        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            self.title_font_family = families[0]

    def _load_header_color_options(self) -> list[tuple[str, str]]:
        """Load the available workbook header color presets."""
        from utils.excel_handler import ExcelHandler

        return list(ExcelHandler.HEADER_COLOR_PRESETS)

    def _load_saved_header_fill_color(self) -> str:
        """Load and normalize the persisted workbook header color."""
        from utils.excel_handler import ExcelHandler

        saved_color = load_setting(
            HEADER_FILL_COLOR_KEY,
            ExcelHandler.DEFAULT_HEADER_FILL_COLOR,
        )
        return ExcelHandler.normalize_header_fill_color(saved_color)

    def _default_irt_date_range(self) -> tuple[date, date]:
        """Return the current week's Thursday-to-Saturday default IRT window."""
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        return week_start + timedelta(days=3), week_start + timedelta(days=5)

    def _apply_default_irt_filters(self) -> None:
        """Seed the IRT controls with the current week's standard date range."""
        start_date, end_date = self._default_irt_date_range()
        self.irt_start_date_edit.setDate(QDate(start_date.year, start_date.month, start_date.day))
        self.irt_end_date_edit.setDate(QDate(end_date.year, end_date.month, end_date.day))
        self.irt_start_date_edit.setToolTip(
            f"Default IRT start date: {start_date.strftime('%B %d, %Y')}"
        )
        self.irt_end_date_edit.setToolTip(
            f"Default IRT end date: {end_date.strftime('%B %d, %Y')}"
        )

    def _load_saved_recipient_override(self, key: str) -> str:
        """Return one saved recipient override value as trimmed text."""
        saved_value = load_setting(key, "")
        return str(saved_value or "").strip()

    def _load_saved_source_mode(self) -> str:
        """Return the persisted source mode when it is still supported."""
        default_mode = self.source_mode_options[0][1] if self.source_mode_options else "irt"
        saved_mode = str(load_setting(SOURCE_MODE_KEY, default_mode) or default_mode).strip().lower()
        supported_modes = {mode for _label, mode in self.source_mode_options}
        return saved_mode if saved_mode in supported_modes else default_mode

    def _restore_saved_recipient_overrides(self) -> None:
        """Restore the last saved manual recipient overrides into the popup."""
        saved_to = self._load_saved_recipient_override(RECIPIENT_OVERRIDE_TO_KEY)
        saved_cc = self._load_saved_recipient_override(RECIPIENT_OVERRIDE_CC_KEY)

        self.recipient_to_input.blockSignals(True)
        self.recipient_cc_input.blockSignals(True)
        self.recipient_to_input.setPlainText(saved_to)
        self.recipient_cc_input.setPlainText(saved_cc)
        self.recipient_to_input.blockSignals(False)
        self.recipient_cc_input.blockSignals(False)
        self._update_recipient_override_tooltip()

    def _persist_recipient_override_settings(self) -> None:
        """Persist the manual recipient override fields for future launches."""
        recipient_to = self.recipient_to_input.toPlainText().strip()
        recipient_cc = self.recipient_cc_input.toPlainText().strip()

        if recipient_to:
            save_setting(RECIPIENT_OVERRIDE_TO_KEY, recipient_to)
        else:
            remove_setting(RECIPIENT_OVERRIDE_TO_KEY)

        if recipient_cc:
            save_setting(RECIPIENT_OVERRIDE_CC_KEY, recipient_cc)
        else:
            remove_setting(RECIPIENT_OVERRIDE_CC_KEY)

        self._update_recipient_override_tooltip()

    def _update_recipient_override_tooltip(self) -> None:
        """Reflect whether the recipients override is currently saved and active."""
        recipient_to = self.recipient_to_input.toPlainText().strip()
        recipient_cc = self.recipient_cc_input.toPlainText().strip()
        if recipient_to or recipient_cc:
            self.recipients_toggle_btn.setToolTip(
                "Manual recipient override is saved and will be reused until you change or clear it."
            )
            return

        self.recipients_toggle_btn.setToolTip(
            "Manually override the outgoing To and CC recipients for this run."
        )

    def _create_color_swatch_icon(self, color_hex: str) -> QIcon:
        """Build a compact swatch icon for the header-color dropdown."""
        swatch = QPixmap(18, 18)
        swatch.fill(Qt.GlobalColor.transparent)

        painter = QPainter(swatch)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#243248"))
        painter.setBrush(QColor(f"#{color_hex}"))
        painter.drawRoundedRect(1, 1, 16, 16, 4, 4)
        painter.end()

        return QIcon(swatch)

    def _load_colored_icon(
        self,
        icon_name: str,
        color_hex: str,
        size: int = 18,
        vertical_offset: int = 0,
    ) -> QIcon:
        """Load one bundled icon and tint it to match the active UI surface."""
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        icon_candidates = (
            assets_dir / f"{icon_name}.svg",
            assets_dir / f"{icon_name}.png",
        )
        icon_path = next((candidate for candidate in icon_candidates if candidate.exists()), None)
        if not icon_path:
            return QIcon()

        base_icon = QIcon(str(icon_path))
        render_scale = max(2.0, float(self.devicePixelRatioF()))
        canvas_size = max(1, int(round(size * render_scale)))
        source_size = max(1, int(round((size - 2) * render_scale)))
        pixmap = base_icon.pixmap(source_size, source_size)
        if pixmap.isNull():
            return base_icon

        tinted = QPixmap(canvas_size, canvas_size)
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        draw_x = max(0, (canvas_size - pixmap.width()) // 2)
        draw_y = max(
            0,
            ((canvas_size - pixmap.height()) // 2) + int(round(vertical_offset * render_scale)),
        )
        painter.drawPixmap(draw_x, draw_y, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color_hex))
        painter.end()
        return QIcon(tinted)

    def _create_workflow_field(
        self,
        label_text: str,
        editor: QWidget,
        min_width: int,
        max_width: int | None = None,
        label_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    ) -> QWidget:
        """Wrap one workflow control in a tidy label-plus-field column."""
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        label.setAlignment(label_alignment)
        layout.addWidget(label)

        editor.setMinimumWidth(min_width)
        editor.setMaximumWidth(max_width if max_width is not None else 16777215)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(editor)

        return container

    def _create_workflow_divider(self, height: int = 46) -> QFrame:
        """Create a quiet vertical divider between workflow controls."""
        divider = QFrame()
        divider.setObjectName("WorkflowDivider")
        divider.setFixedWidth(1)
        divider.setFixedHeight(height)
        return divider

    def _decorate_date_edit(self, date_edit: QDateEdit) -> None:
        """Add the leading calendar affordance while keeping popup logic custom."""
        line_edit = date_edit.lineEdit()
        if line_edit is None:
            return

        icon_action = QAction(date_edit)
        icon_action.setIcon(self._load_colored_icon("calendar", "#38bdf8", 20, vertical_offset=1))
        line_edit.addAction(icon_action, QLineEdit.ActionPosition.LeadingPosition)

    def _configure_dropdown_combo(
        self,
        combo: QComboBox,
        max_visible_items: int = 8,
        vertical_scrollbar_policy: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    ) -> None:
        """Make compact selectors feel like true popup dropdowns."""
        popup_view = QListView(combo)
        popup_view.setObjectName("DropdownPopupView")
        popup_view.setSpacing(4)
        popup_view.setUniformItemSizes(True)
        popup_view.setFrameShape(QFrame.Shape.NoFrame)
        popup_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        popup_view.setVerticalScrollBarPolicy(vertical_scrollbar_policy)
        popup_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        popup_view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        popup_view.verticalScrollBar().setCursor(Qt.CursorShape.PointingHandCursor)
        popup_view.verticalScrollBar().setStyleSheet(
            """
            QScrollBar:vertical {
                background-color: #0c1422;
                width: 12px;
                margin: 8px 4px 8px 0;
                border-radius: 6px;
                border: 1px solid #22304a;
            }

            QScrollBar::handle:vertical {
                background-color: #315f87;
                border-radius: 5px;
                min-height: 34px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #3c7cae;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                height: 0px;
                border: none;
            }
            """
        )
        combo.setView(popup_view)
        combo.setMaxVisibleItems(max(1, max_visible_items))
        combo.setCursor(Qt.CursorShape.PointingHandCursor)

    def _populate_header_color_combo(self):
        """Fill the dropdown with preset Excel header colors."""
        self.header_color_combo.blockSignals(True)
        self.header_color_combo.clear()

        selected_index = 0
        for index, (label, color_hex) in enumerate(self.header_color_options):
            self.header_color_combo.addItem(
                self._create_color_swatch_icon(color_hex),
                label,
            )
            self.header_color_combo.setItemData(
                index,
                color_hex,
                Qt.ItemDataRole.UserRole,
            )
            self.header_color_combo.setItemData(
                index,
                f"#{color_hex}",
                Qt.ItemDataRole.ToolTipRole,
            )

            if color_hex == self.header_fill_color:
                selected_index = index

        self.header_color_combo.setCurrentIndex(selected_index)
        self.header_color_combo.blockSignals(False)
        self._update_header_color_tooltip()

    def _update_header_color_tooltip(self):
        """Keep the header-color selector tooltip aligned with the current value."""
        current_color = self.header_color_combo.currentData(Qt.ItemDataRole.UserRole)
        if current_color:
            self.header_color_combo.setToolTip(
                f"Workbook and Outlook table header color: #{current_color}"
            )

    def _on_header_color_changed(self, *_args):
        """Persist the selected workbook header color for future runs."""
        selected_color = self.header_color_combo.currentData(Qt.ItemDataRole.UserRole)
        if not selected_color:
            return

        self.header_fill_color = str(selected_color)
        save_setting(HEADER_FILL_COLOR_KEY, self.header_fill_color)
        self._update_header_color_tooltip()

    def _populate_source_mode_combo(self):
        """Fill the source selector with supported extraction source modes."""
        self.source_mode_combo.blockSignals(True)
        self.source_mode_combo.clear()
        saved_source_mode = self._load_saved_source_mode()
        selected_index = 0

        for index, (label, source_mode) in enumerate(self.source_mode_options):
            icon_name = "source_irt" if source_mode == "irt" else "template_file"
            self.source_mode_combo.addItem(
                self._load_colored_icon(icon_name, "#38bdf8", 24, vertical_offset=1),
                label,
            )
            self.source_mode_combo.setItemData(
                index,
                source_mode,
                Qt.ItemDataRole.UserRole,
            )
            if source_mode == saved_source_mode:
                selected_index = index

        self.source_mode_combo.setCurrentIndex(selected_index)
        self.source_mode_combo.blockSignals(False)
        self._update_source_mode_tooltip()

    def _sync_source_mode_layout(self) -> None:
        """Refresh source-specific controls without reshuffling the whole form."""
        is_manual = self._current_source_mode() == "manual"
        if hasattr(self, "source_template_btn"):
            self.source_template_btn.setVisible(is_manual)
            self.source_template_btn.setEnabled(self.extraction_thread is None and is_manual)

    def _current_source_mode(self) -> str:
        """Return the currently selected extraction source mode."""
        if not hasattr(self, "source_mode_combo"):
            return self.source_mode_options[0][1] if self.source_mode_options else "irt"
        selected_mode = self.source_mode_combo.currentData(Qt.ItemDataRole.UserRole)
        return str(selected_mode or (self.source_mode_options[0][1] if self.source_mode_options else "irt"))

    def _update_source_mode_tooltip(self):
        """Describe the behavior of the currently selected source mode."""
        source_mode = self._current_source_mode()
        if source_mode == "manual":
            self.source_mode_combo.setToolTip(
                "Run Extraction will use the most recently edited Excel workbook in the Results folder."
            )
            return
        self.source_mode_combo.setToolTip(
            "Run Extraction will import rows from IRT Search Inventory using the selected date range across both courts."
        )

    def _idle_ready_detail(self) -> str:
        """Return source-aware helper copy for the idle state."""
        source_mode = self._current_source_mode()
        if source_mode == "manual":
            return "Enter your credentials, then run extraction using the latest manual workbook."
        return (
            "Enter your credentials, then import IRT Search Inventory results and "
            "continue with extraction."
        )

    def _on_source_mode_changed(self, *_args):
        """Refresh helper copy when the extraction source mode changes."""
        save_setting(SOURCE_MODE_KEY, self._current_source_mode())
        self._update_source_mode_tooltip()
        self._sync_source_mode_layout()
        self._update_window_size()
        if self.extraction_thread is None and not self.developer_mode_enabled:
            self._set_status_state("Ready", self._idle_ready_detail(), "ready")

    def _update_window_size(self):
        """Resize the fixed window based on the active UI mode."""
        base_width, base_height = (
            self.dev_mode_window_size if self.developer_mode_enabled else self.base_window_size
        )
        self.setFixedSize(base_width, base_height)

    def _recipient_button_contains_cursor(self) -> bool:
        """Return True when the current cursor position is inside the Recipients button."""
        local_pos = self.recipients_toggle_btn.mapFromGlobal(QCursor.pos())
        return self.recipients_toggle_btn.rect().contains(local_pos)

    def _toggle_recipient_panel(self, _checked: bool = False):
        """Show or hide the manual recipient override popup via explicit button clicks."""
        if self._recipient_popup_closed_by_button:
            self._recipient_popup_closed_by_button = False
            self.recipients_toggle_btn.blockSignals(True)
            self.recipients_toggle_btn.setChecked(False)
            self.recipients_toggle_btn.blockSignals(False)
            return

        if self.recipient_popup.isVisible():
            self.recipients_toggle_btn.blockSignals(True)
            self.recipients_toggle_btn.setChecked(False)
            self.recipients_toggle_btn.blockSignals(False)
            self.recipient_popup.hide()
            return

        self._recipient_popup_closed_by_button = False
        if hasattr(self, "settings_popup") and self.settings_popup.isVisible():
            self.settings_toggle_btn.blockSignals(True)
            self.settings_toggle_btn.setChecked(False)
            self.settings_toggle_btn.blockSignals(False)
            self.settings_popup.hide()
        self.recipients_toggle_btn.blockSignals(True)
        self.recipients_toggle_btn.setChecked(True)
        self.recipients_toggle_btn.blockSignals(False)
        self.recipient_popup.show_for_button(self.recipients_toggle_btn)

    def _on_recipient_popup_closed(self):
        """Sync the toggle button when the popup closes itself."""
        self._recipient_popup_closed_by_button = (
            self.recipients_toggle_btn.isDown() or self._recipient_button_contains_cursor()
        )
        self.recipients_toggle_btn.blockSignals(True)
        self.recipients_toggle_btn.setChecked(False)
        self.recipients_toggle_btn.blockSignals(False)

    def _settings_button_contains_cursor(self) -> bool:
        """Return True when the cursor is inside the Settings button."""
        local_pos = self.settings_toggle_btn.mapFromGlobal(QCursor.pos())
        return self.settings_toggle_btn.rect().contains(local_pos)

    def _toggle_settings_panel(self, _checked: bool = False) -> None:
        """Show or hide the compact Settings popup via explicit button clicks."""
        if self._settings_popup_closed_by_button:
            self._settings_popup_closed_by_button = False
            self.settings_toggle_btn.blockSignals(True)
            self.settings_toggle_btn.setChecked(False)
            self.settings_toggle_btn.blockSignals(False)
            return

        if self.settings_popup.isVisible():
            self.settings_toggle_btn.blockSignals(True)
            self.settings_toggle_btn.setChecked(False)
            self.settings_toggle_btn.blockSignals(False)
            self.settings_popup.hide()
            return

        self._settings_popup_closed_by_button = False
        if hasattr(self, "recipient_popup") and self.recipient_popup.isVisible():
            self.recipients_toggle_btn.blockSignals(True)
            self.recipients_toggle_btn.setChecked(False)
            self.recipients_toggle_btn.blockSignals(False)
            self.recipient_popup.hide()
        self.settings_toggle_btn.blockSignals(True)
        self.settings_toggle_btn.setChecked(True)
        self.settings_toggle_btn.blockSignals(False)
        self.settings_popup.show_for_button(self.settings_toggle_btn)

    def _on_settings_popup_closed(self) -> None:
        """Sync the toggle button when the settings popup closes itself."""
        self._settings_popup_closed_by_button = (
            self.settings_toggle_btn.isDown() or self._settings_button_contains_cursor()
        )
        self.settings_toggle_btn.blockSignals(True)
        self.settings_toggle_btn.setChecked(False)
        self.settings_toggle_btn.blockSignals(False)

    def _apply_card_shadow(self, widget: QFrame):
        """Apply a soft drop shadow to a card."""
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 72))
        widget.setGraphicsEffect(shadow)

    def _create_card(self, object_name: str = "SectionCard") -> QFrame:
        """Create a styled card frame."""
        card = QFrame()
        card.setObjectName(object_name)
        self._apply_card_shadow(card)
        return card

    def _refresh_idle_wallpaper(self) -> None:
        """Fit the idle wallpaper neatly inside the rounded main card."""
        auth_card = getattr(self, "auth_card", None)
        if (
            self.idle_wallpaper is None
            or auth_card is None
            or self.idle_wallpaper_source.isNull()
        ):
            return

        card_rect = auth_card.rect()
        if card_rect.width() <= 0 or card_rect.height() <= 0:
            return

        target_size = card_rect.size()
        scaled = self.idle_wallpaper_source.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        offset_x = max(0, (scaled.width() - target_size.width()) // 2)
        offset_y = max(0, (scaled.height() - target_size.height()) // 2)

        rounded = QPixmap(target_size)
        rounded.fill(Qt.GlobalColor.transparent)

        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(
            0.0,
            0.0,
            float(target_size.width()),
            float(target_size.height()),
            16.0,
            16.0,
        )
        painter.setClipPath(clip_path)
        painter.drawPixmap(-offset_x, -offset_y, scaled)

        overlay = QLinearGradient(0, 0, target_size.width(), target_size.height())
        overlay.setColorAt(0.0, QColor(6, 11, 18, 58))
        overlay.setColorAt(0.5, QColor(8, 14, 22, 40))
        overlay.setColorAt(1.0, QColor(5, 10, 17, 52))
        painter.fillPath(clip_path, overlay)

        cyan_glow = QRadialGradient(
            target_size.width() * 0.28,
            target_size.height() * 0.2,
            target_size.width() * 0.72,
        )
        cyan_glow.setColorAt(0.0, QColor(34, 211, 238, 34))
        cyan_glow.setColorAt(0.4, QColor(34, 211, 238, 12))
        cyan_glow.setColorAt(1.0, QColor(34, 211, 238, 0))
        painter.fillRect(rounded.rect(), cyan_glow)

        bottom_lift = QLinearGradient(0, target_size.height() * 0.64, 0, target_size.height())
        bottom_lift.setColorAt(0.0, QColor(56, 189, 248, 0))
        bottom_lift.setColorAt(1.0, QColor(56, 189, 248, 18))
        painter.fillRect(rounded.rect(), bottom_lift)

        magenta_glow = QRadialGradient(
            target_size.width() * 0.8,
            target_size.height() * 0.9,
            target_size.width() * 0.48,
        )
        magenta_glow.setColorAt(0.0, QColor(217, 70, 239, 30))
        magenta_glow.setColorAt(0.45, QColor(217, 70, 239, 10))
        magenta_glow.setColorAt(1.0, QColor(217, 70, 239, 0))
        painter.fillRect(rounded.rect(), magenta_glow)
        painter.end()

        self.idle_wallpaper.setGeometry(card_rect)
        self.idle_wallpaper.setPixmap(rounded)
        self.idle_wallpaper.show()
        self._update_idle_bubble_geometry()
        self._apply_idle_visual_z_order()

    def _update_idle_light_geometry(self) -> None:
        """Keep the idle light overlay fitted to the main auth card."""
        auth_card = getattr(self, "auth_card", None)
        if self.idle_light_overlay is None or auth_card is None:
            return
        self.idle_light_overlay.setGeometry(auth_card.rect())

    def _update_idle_bubble_geometry(self) -> None:
        """Keep the idle bubble overlay fitted to the main auth card."""
        auth_card = getattr(self, "auth_card", None)
        if self.idle_bubble_overlay is None or auth_card is None:
            return
        self.idle_bubble_overlay.setGeometry(auth_card.rect())

    def _apply_idle_visual_z_order(self) -> None:
        """Stack wallpaper, idle bubbles, and interactive controls in a readable order."""
        if self.idle_wallpaper is not None:
            self.idle_wallpaper.lower()
        if self.idle_bubble_overlay is not None and self.idle_bubble_overlay.isVisible():
            self.idle_bubble_overlay.raise_()
        if hasattr(self, "title_bar_surface") and self.title_bar_surface is not None:
            self.title_bar_surface.raise_()
        if hasattr(self, "header_separator") and self.header_separator is not None:
            self.header_separator.raise_()
        if hasattr(self, "auth_body") and self.auth_body is not None:
            self.auth_body.raise_()

    def _refresh_idle_bubble_visibility(self) -> None:
        """Show bubbles only while the main idle card is the active surface."""
        auth_card = getattr(self, "auth_card", None)
        if self.idle_bubble_overlay is None or auth_card is None:
            return

        active = (
            auth_card.isVisible()
            and self.extraction_thread is None
            and not self._launch_intro_active
        )
        if active:
            self.idle_bubble_overlay.prime_bubbles()
            self.idle_bubble_overlay.show()
            self._apply_idle_visual_z_order()
        else:
            self.idle_bubble_overlay.cancel_hold_bubble()
            self.idle_bubble_overlay.hide()

    def set_launch_intro_active(self, active: bool) -> None:
        """Pause idle-only animation while the launch intro owns the stage."""
        self._launch_intro_active = active
        self._refresh_idle_bubble_visibility()

    def _set_status_state(self, state: str, detail: str, tone: str = "ready"):
        """Update the runtime status badge and detail text."""
        palette = {
            "ready": ("#131c2b", "#cbd5e1", "#243248"),
            "working": ("#0f2330", "#67e8f9", "#164e63"),
            "success": ("#0d2018", "#86efac", "#14532d"),
            "error": ("#2a1215", "#fda4af", "#7f1d1d"),
            "warning": ("#2b1f0f", "#fbbf24", "#5b4417"),
        }
        background, foreground, border = palette.get(tone, palette["ready"])
        self.status_badge.setText(state.upper())
        self.status_badge.setStyleSheet(
            f"""
            background-color: {background};
            color: {foreground};
            border: 1px solid {border};
            border-radius: 11px;
            padding: 5px 10px;
            font-size: 10px;
            font-weight: 700;
            """
        )
        self.status_label.setText(detail)

    def _set_developer_mode_state(self, enabled: bool):
        """Toggle developer-only UX hints while reusing the Recipients override."""
        self.developer_mode_enabled = enabled
        self.compact_preview_checkbox.setVisible(enabled)
        self.developer_mode_indicator.setVisible(enabled)
        self.progress_meta_label.setVisible(enabled)
        self.status_badge.setAlignment(
            Qt.AlignmentFlag.AlignCenter if not enabled else Qt.AlignmentFlag.AlignLeft
        )
        self.status_row_layout.setAlignment(
            self.status_badge,
            Qt.AlignmentFlag.AlignLeft if enabled else Qt.AlignmentFlag.AlignHCenter,
        )
        self.status_row_layout.setStretch(0, 0 if enabled else 1)
        self.status_row_layout.setStretch(1, 1 if enabled else 0)
        self._update_window_size()
        self._set_runtime_panel_mode(show_runtime=self.extraction_thread is not None)
        self.progress_meta_label.setText(
            "Developer mode uses the Recipients override for safe test routing."
            if enabled
            else "IRT intake uses the selected date range across both courts."
        )
        if not enabled:
            self.recipient_popup.hide()
            if hasattr(self, "settings_popup"):
                self.settings_popup.hide()

        self.recipients_toggle_btn.setToolTip(
            (
                "Override the outgoing To and CC recipients for this run. "
                "In Developer Mode, use this to route email to your test inbox."
            )
            if enabled
            else "Manually override the outgoing To and CC recipients for this run."
        )

        if self.extraction_thread is None:
            self._set_status_state(
                "Developer" if enabled else "Ready",
                (
                    "Developer mode enabled. Use Recipients to route email to your test inbox."
                    if enabled
                    else self._idle_ready_detail()
                ),
                "working" if enabled else "ready",
            )

    def _toggle_developer_mode(self):
        """Toggle Developer Mode for safe test routing with the Recipients override."""
        if self.extraction_thread is not None:
            return

        self._set_developer_mode_state(not self.developer_mode_enabled)

    def _toggle_password_visibility(self):
        """Toggle password visibility in the password input."""
        self.password_visible = not self.password_visible
        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Normal if self.password_visible else QLineEdit.EchoMode.Password
        )
        self._update_password_toggle_action()

    def _update_password_toggle_action(self):
        """Update icon and tooltip for the password visibility toggle."""
        icon_base = "pw_hidden" if self.password_visible else "pw_visible"
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        icon_candidates = (
            assets_dir / f"{icon_base}.svg",
            assets_dir / f"{icon_base}.png",
        )
        icon_path = next((candidate for candidate in icon_candidates if candidate.exists()), None)
        if not icon_path:
            self.password_toggle_action.setIcon(QIcon())
        elif icon_path.suffix.lower() == ".svg":
            # Prefer SVG for crisp rendering at small action-icon sizes.
            self.password_toggle_action.setIcon(QIcon(str(icon_path)))
        else:
            self.password_toggle_action.setIcon(self._load_tinted_icon(icon_path))
        self.password_toggle_action.setToolTip(
            "Hide password" if self.password_visible else "Show password"
        )

    def _load_tinted_icon(self, icon_path: Path) -> QIcon:
        """Load icon and tint it white for dark-theme visibility."""
        base_icon = QIcon(str(icon_path))
        pixmap = base_icon.pixmap(16, 16)
        if pixmap.isNull():
            return base_icon

        tinted = QPixmap(pixmap.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor("#f8fafc"))
        painter.end()
        return QIcon(tinted)

    def _begin_window_drag(self, global_pos: QPoint) -> None:
        """Start dragging the frameless window from the given global point."""
        self.drag_offset = global_pos - self.frameGeometry().topLeft()

    def _move_window_drag(self, global_pos: QPoint) -> None:
        """Move the frameless window using the stored drag offset."""
        if self.drag_offset is not None:
            self.move(global_pos - self.drag_offset)

    def _end_window_drag(self) -> None:
        """Clear any active window drag state."""
        self.drag_offset = None

    def _runtime_drag_enabled(self) -> bool:
        """Return True when the simplified runtime panel is the active screen."""
        return self.runtime_card.isVisible() and not self.auth_card.isVisible()

    def _transient_ui_popup_visible(self) -> bool:
        """Return True while an interactive popup is open so idle bubbles do not steal clicks."""
        for popup_name in ("recipient_popup", "settings_popup"):
            popup = getattr(self, popup_name, None)
            if popup is not None and popup.isVisible():
                return True

        for date_edit_name in ("irt_start_date_edit", "irt_end_date_edit"):
            date_edit = getattr(self, date_edit_name, None)
            if date_edit is None:
                continue

            calendar_popup = getattr(date_edit, "calendar_popup_widget", None)
            if calendar_popup is not None and calendar_popup.isVisible():
                return True

            if calendar_popup is None:
                continue

            for selector_name in ("month_combo", "year_combo"):
                selector = getattr(calendar_popup, selector_name, None)
                header_popup = getattr(selector, "header_popup", None) if selector is not None else None
                if header_popup is not None and header_popup.isVisible():
                    return True

        return False

    def _idle_bubble_click_enabled(self) -> bool:
        """Return True when idle bubbles are visible and can respond to clicks."""
        return (
            self.extraction_thread is None
            and hasattr(self, "auth_card")
            and self.auth_card.isVisible()
            and not self._transient_ui_popup_visible()
            and self.idle_bubble_overlay is not None
            and self.idle_bubble_overlay.isVisible()
        )

    def _handle_idle_bubble_pointer_event(self, event) -> bool:
        """Route idle-background mouse gestures into bubble pops or hold-grow-release bubbles."""
        if not self._idle_bubble_click_enabled():
            return False

        overlay = self.idle_bubble_overlay
        global_pos = event.globalPosition().toPoint()
        local_pos = overlay.mapFromGlobal(global_pos)

        if event.type() == QEvent.Type.MouseButtonPress:
            if not overlay.rect().contains(local_pos):
                return False
            local_x = float(local_pos.x())
            local_y = float(local_pos.y())
            if event.button() == Qt.MouseButton.RightButton:
                overlay.emit_click_puff(local_x, local_y)
                return True
            if overlay.try_pop_at(local_x, local_y):
                return True
            return overlay.begin_hold_bubble(local_x, local_y)

        if event.type() == QEvent.Type.MouseMove:
            return (
                overlay.has_hold_bubble()
                and bool(event.buttons() & Qt.MouseButton.LeftButton)
            )

        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if overlay.has_hold_bubble():
                overlay.release_hold_bubble()
                return True
            return False

        return False

    def eventFilter(self, watched, event):
        """Allow the runtime panel itself to drag the window while buttons stay clickable."""
        if (
            hasattr(self, "_idle_bubble_click_widgets")
            and watched in self._idle_bubble_click_widgets
            and event.type()
            in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease,
            )
            and (
                event.type() == QEvent.Type.MouseMove
                or getattr(event, "button", lambda: None)()
                in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)
            )
        ):
            if self._handle_idle_bubble_pointer_event(event):
                event.accept()
                return True

        if (
            hasattr(self, "_runtime_drag_widgets")
            and watched in self._runtime_drag_widgets
            and self._runtime_drag_enabled()
        ):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._begin_window_drag(event.globalPosition().toPoint())
                event.accept()
                return True

            if event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
                self._move_window_drag(event.globalPosition().toPoint())
                event.accept()
                return True

            if event.type() == QEvent.Type.MouseButtonRelease:
                self._end_window_drag()
                event.accept()
                return True

        return super().eventFilter(watched, event)

    def _set_runtime_panel_mode(self, show_runtime: bool):
        """Show either single-panel or full layout depending on mode."""
        if self.developer_mode_enabled and not self.dev_preview_compact_mode:
            # Keep the original full developer layout unless preview is enabled.
            self.auth_card.setVisible(True)
            if hasattr(self, "auth_body"):
                self.auth_body.setVisible(True)
            self.runtime_card.setVisible(True)
            if hasattr(self, "runtime_window_controls"):
                self.runtime_window_controls.hide()
            self._refresh_runtime_loading_watermark()
            self._refresh_idle_bubble_visibility()
            return

        self.auth_card.setVisible(not show_runtime)
        self.runtime_card.setVisible(show_runtime)
        if hasattr(self, "runtime_window_controls"):
            self.runtime_window_controls.setVisible(show_runtime)
            self._update_runtime_window_controls_geometry()
        self._refresh_runtime_loading_watermark()
        self._refresh_idle_bubble_visibility()

    def _update_runtime_window_controls_geometry(self) -> None:
        """Keep the floating runtime window controls pinned in the card corner."""
        if not hasattr(self, "runtime_window_controls"):
            return

        if not self.runtime_card.isVisible():
            self.runtime_window_controls.hide()
            return

        controls_width = self.runtime_window_controls.sizeHint().width()
        controls_height = self.runtime_window_controls.sizeHint().height()
        inset_x = 16
        inset_y = 12
        x = max(inset_x, self.runtime_card.width() - controls_width - inset_x)
        y = inset_y
        self.runtime_window_controls.setGeometry(x, y, controls_width, controls_height)
        self.runtime_window_controls.raise_()

    def _update_runtime_loading_watermark_geometry(self) -> None:
        """Keep the animated runtime watermark fitted behind the run-state card."""
        if self.runtime_loading_watermark is None:
            return

        runtime_rect = self.runtime_card.rect()
        if runtime_rect.width() <= 0 or runtime_rect.height() <= 0:
            self.runtime_loading_watermark.hide()
            return

        inset = self._runtime_loading_inset
        watermark_rect = runtime_rect.adjusted(inset, inset, -inset, -inset)
        if watermark_rect.width() <= 0 or watermark_rect.height() <= 0:
            self.runtime_loading_watermark.hide()
            return

        self.runtime_loading_watermark.setGeometry(watermark_rect)
        mask_path = QPainterPath()
        corner_radius = max(0.0, 16.0 - float(self._runtime_loading_inset))
        mask_path.addRoundedRect(
            0.0,
            0.0,
            float(watermark_rect.width()),
            float(watermark_rect.height()),
            corner_radius,
            corner_radius,
        )
        self.runtime_loading_watermark.setMask(
            QRegion(mask_path.toFillPolygon().toPolygon())
        )
        if self.runtime_loading_movie is not None:
            self.runtime_loading_movie.setScaledSize(watermark_rect.size())
        self.runtime_loading_watermark.lower()

    def _refresh_runtime_loading_watermark(self) -> None:
        """Show the sea animation only while the runtime card is actively handling a run."""
        if self.runtime_loading_watermark is None:
            return

        self._update_runtime_loading_watermark_geometry()
        active = (
            self.runtime_card.isVisible()
            and self.stop_btn.isVisible()
            and self.runtime_loading_movie is not None
        )
        if not active:
            if self.runtime_loading_opacity is not None:
                self.runtime_loading_opacity.setOpacity(0.0)
            self.runtime_loading_watermark.hide()
            if (
                self.runtime_loading_movie is not None
                and self.runtime_loading_movie.state() == QMovie.MovieState.Running
            ):
                self.runtime_loading_movie.setPaused(True)
            return

        self.runtime_loading_watermark.show()
        self.runtime_loading_watermark.lower()
        if hasattr(self, "runtime_content") and self.runtime_content is not None:
            self.runtime_content.raise_()
        if hasattr(self, "runtime_window_controls") and self.runtime_window_controls is not None:
            self.runtime_window_controls.raise_()
        if self.runtime_loading_movie is not None:
            self.runtime_loading_movie.setSpeed(self._runtime_loading_speed)
            if self.runtime_loading_movie.state() == QMovie.MovieState.NotRunning:
                self.runtime_loading_movie.start()
            elif self.runtime_loading_movie.state() == QMovie.MovieState.Paused:
                self.runtime_loading_movie.setPaused(False)
        if self.runtime_loading_opacity is not None:
            self.runtime_loading_opacity.setOpacity(self._runtime_loading_opacity)

    def _on_compact_preview_toggled(self, checked: bool):
        """Enable/disable compact two-panel preview while in Developer Mode."""
        self.dev_preview_compact_mode = checked
        self._set_runtime_panel_mode(show_runtime=self.extraction_thread is not None)

    def showEvent(self, event):
        """Refresh layered visuals after the window actually becomes visible."""
        super().showEvent(event)
        self._refresh_idle_wallpaper()
        self._update_idle_bubble_geometry()
        self._refresh_idle_bubble_visibility()
        self._update_runtime_window_controls_geometry()
        self._refresh_runtime_loading_watermark()

    def resizeEvent(self, event):
        """Keep the idle and runtime background media aligned with their card bounds."""
        super().resizeEvent(event)
        self._refresh_idle_wallpaper()
        self._update_idle_bubble_geometry()
        self._refresh_idle_bubble_visibility()
        self._update_runtime_window_controls_geometry()
        self._refresh_runtime_loading_watermark()

    def init_ui(self):
        """Initialize the UI."""
        self.base_window_size = (536, 548)
        self.dev_mode_window_size = (536, 548)
        self.setWindowTitle("PLR000-CCA001 Extractor")
        self._update_window_size()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._apply_window_theme()

        central_widget = QWidget()
        central_widget.setObjectName("AppSurface")
        central_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 8, 8, 8)
        central_widget.setLayout(main_layout)

        header_card = self._create_card("HeaderCard")
        header_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header_card.setMinimumHeight(90)
        header_card.setMaximumHeight(90)
        header_layout = QVBoxLayout(header_card)
        header_layout.setSpacing(0)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("PLR000-CCA001 EXTRACTOR")
        title.setObjectName("HeroTitle")
        title.setWordWrap(False)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title.setMinimumHeight(26)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if self.title_font_family:
            title_font = QFont(self.title_font_family, 22)
            title_font.setWeight(QFont.Weight.Black)
            title.setFont(title_font)
        title_bar = DraggableTitleBar()
        self.title_bar_surface = title_bar
        title_bar.setObjectName("TitleBarSurface")
        title_bar.setStyleSheet("background: transparent; border: none;")
        title_bar.setFixedHeight(44)
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setSpacing(8)
        title_bar_layout.setContentsMargins(4, 0, 2, 0)

        self.developer_mode_indicator = QLabel("DEV MODE")
        self.developer_mode_indicator.setObjectName("DeveloperPill")
        self.developer_mode_indicator.setVisible(False)
        self.developer_mode_indicator.setToolTip("Developer Mode is enabled. Toggle with Ctrl+K.")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_bar_layout.addWidget(
            title,
            1,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        title_bar_layout.addWidget(
            self.developer_mode_indicator,
            0,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        )

        self.minimize_btn = QPushButton("—")
        self.minimize_btn.setObjectName("TitleBarButton")
        self.minimize_btn.setToolTip("Minimize")
        self.minimize_btn.clicked.connect(self.showMinimized)
        title_bar_layout.addWidget(
            self.minimize_btn,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseTitleBarButton")
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.close)
        title_bar_layout.addWidget(
            self.close_btn,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        header_layout.addWidget(title_bar)

        header_separator = QFrame()
        self.header_separator = header_separator
        header_separator.setObjectName("HeaderInnerDivider")
        header_separator.setFixedHeight(1)
        header_layout.addWidget(header_separator)

        title_band = QWidget()
        title_band.setObjectName("HeaderTitleBand")
        title_band_layout = QHBoxLayout(title_band)
        title_band_layout.setContentsMargins(22, 4, 22, 10)
        title_band_layout.setSpacing(0)
        title_band.hide()
        header_layout.addWidget(title_band)

        header_card.hide()

        auth_card = self._create_card("MainAuthCard")
        self.auth_card = auth_card
        self.idle_wallpaper = QLabel(auth_card)
        self.idle_wallpaper.setObjectName("IdleWallpaper")
        self.idle_wallpaper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.idle_wallpaper.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.idle_wallpaper.lower()
        self.idle_wallpaper_opacity = QGraphicsOpacityEffect(self.idle_wallpaper)
        self.idle_wallpaper_opacity.setOpacity(0.35)
        self.idle_wallpaper.setGraphicsEffect(self.idle_wallpaper_opacity)
        if self.idle_wallpaper_source.isNull():
            self.idle_wallpaper.hide()
        self.idle_bubble_overlay = BubbleOverlay(auth_card)
        self.idle_bubble_overlay.setObjectName("IdleBubbleOverlay")
        self.idle_bubble_overlay.set_pop_audio_callback(self.bubble_pop_audio.play_pop_for_bubble)
        self.idle_bubble_overlay.hide()
        auth_layout = QVBoxLayout(auth_card)
        auth_layout.setSpacing(0)
        auth_layout.setContentsMargins(14, 10, 14, 12)
        auth_layout.addWidget(title_bar)
        auth_layout.addWidget(header_separator)

        auth_body = QWidget()
        self.auth_body = auth_body
        auth_body_layout = QVBoxLayout(auth_body)
        auth_body_layout.setSpacing(4)
        auth_body_layout.setContentsMargins(0, 2, 0, 0)

        credentials_section = QWidget()
        credentials_section.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        credentials_layout = QVBoxLayout(credentials_section)
        credentials_layout.setSpacing(2)
        credentials_layout.setContentsMargins(0, 0, 0, 0)

        id_label = QLabel("Lexis ID")
        id_label.setObjectName("FieldLabel")
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("Lexis ID")
        self.id_input.setMinimumHeight(40)
        self.id_input.textChanged.connect(self.on_credentials_changed)
        self.id_indicator_action = QAction(self.id_input)
        self.id_indicator_action.setIcon(
            self._load_colored_icon("user", "#67e8f9", 22, vertical_offset=1)
        )
        self.id_input.addAction(
            self.id_indicator_action,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        id_section = QWidget()
        id_section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        id_block = QVBoxLayout(id_section)
        id_block.setSpacing(3)
        id_block.setContentsMargins(0, 0, 0, 0)
        id_block.addWidget(id_label)
        id_block.addWidget(self.id_input)
        credentials_layout.addWidget(id_section)

        password_label = QLabel("Password")
        password_label.setObjectName("FieldLabel")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setMinimumHeight(40)
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.textChanged.connect(self.on_credentials_changed)
        self.password_toggle_action = QAction(self.password_input)
        self._update_password_toggle_action()
        self.password_toggle_action.triggered.connect(self._toggle_password_visibility)
        self.password_input.addAction(
            self.password_toggle_action,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        password_section = QWidget()
        password_section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        password_block = QVBoxLayout(password_section)
        password_block.setSpacing(3)
        password_block.setContentsMargins(0, 0, 0, 0)
        password_block.addWidget(password_label)
        password_block.addWidget(self.password_input)
        credentials_layout.addWidget(password_section)

        auth_body_layout.addWidget(credentials_section)

        self.source_mode_combo = FloatingPopupComboBox()
        self.source_mode_combo.setMinimumHeight(38)
        self.source_mode_combo.setIconSize(QSize(24, 24))
        self.source_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.source_mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._configure_dropdown_combo(self.source_mode_combo, max_visible_items=5)
        self._populate_source_mode_combo()
        self.source_mode_combo.currentIndexChanged.connect(self._on_source_mode_changed)

        self.save_credentials_checkbox = QCheckBox("Remember ID")
        self.headless_checkbox = QCheckBox("Headless Mode")
        self.headless_checkbox.setToolTip("Run browser in background without showing the window")
        self.headless_checkbox.setChecked(True)

        self.header_color_combo = HeaderColorComboBox()
        self.header_color_combo.setMinimumHeight(38)
        self.header_color_combo.setMinimumWidth(160)
        self.header_color_combo.setMaximumWidth(178)
        self.header_color_combo.setIconSize(QSize(18, 18))
        self.header_color_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._populate_header_color_combo()
        self.header_color_combo.currentIndexChanged.connect(self._on_header_color_changed)

        self.compact_preview_checkbox = QCheckBox("Preview compact runtime UX")
        self.compact_preview_checkbox.setToolTip(
            "When enabled in Developer Mode, only two cards are shown at once."
        )
        self.compact_preview_checkbox.toggled.connect(self._on_compact_preview_toggled)
        self.compact_preview_checkbox.setVisible(False)

        self.settings_popup = SettingsPopup(self)
        self.settings_popup.closed.connect(self._on_settings_popup_closed)
        self.settings_popup.add_widget(self.save_credentials_checkbox)
        self.settings_popup.add_widget(self.headless_checkbox)

        settings_header_color_group = QWidget()
        settings_header_color_layout = QVBoxLayout(settings_header_color_group)
        settings_header_color_layout.setContentsMargins(0, 0, 0, 0)
        settings_header_color_layout.setSpacing(6)
        settings_header_color_label = QLabel("Header Color")
        settings_header_color_label.setObjectName("FieldLabel")
        settings_header_color_layout.addWidget(settings_header_color_label)
        settings_header_color_layout.addWidget(self.header_color_combo)
        self.settings_popup.add_widget(settings_header_color_group)
        self.settings_popup.add_widget(self.compact_preview_checkbox)

        controls_frame = QFrame()
        controls_frame.setObjectName("InlineOptionsCard")
        controls_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls_outer_layout = QHBoxLayout(controls_frame)
        controls_outer_layout.setContentsMargins(10, 7, 10, 7)
        controls_outer_layout.setSpacing(0)

        controls_inner = QWidget()
        controls_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls_layout = QGridLayout(controls_inner)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(8)
        controls_layout.setVerticalSpacing(4)

        source_label = QLabel("Source")
        source_label.setObjectName("FieldLabel")
        source_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        controls_layout.addWidget(source_label, 0, 0)

        recipients_label = QLabel("Recipients")
        recipients_label.setObjectName("FieldLabel")
        recipients_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        controls_layout.addWidget(recipients_label, 0, 1)

        source_selector_row = QWidget()
        source_selector_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_selector_layout = QHBoxLayout(source_selector_row)
        source_selector_layout.setContentsMargins(0, 0, 0, 0)
        source_selector_layout.setSpacing(5)
        source_selector_layout.addWidget(self.source_mode_combo, 1)
        self.source_mode_combo.setMinimumWidth(146)
        self.source_mode_combo.setMaximumWidth(186)

        self.source_template_btn = QPushButton("+")
        self.source_template_btn.setObjectName("SquareAccentButton")
        self.source_template_btn.setToolTip("Generate a manual template workbook.")
        self.source_template_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.source_template_btn.clicked.connect(self.generate_template)
        self.source_template_btn.hide()
        source_selector_layout.addWidget(self.source_template_btn, 0)
        controls_layout.addWidget(source_selector_row, 1, 0)

        self.recipients_toggle_btn = QPushButton("Recipients")
        self.recipients_toggle_btn.setObjectName("InlineToggleButton")
        self.recipients_toggle_btn.setCheckable(True)
        self.recipients_toggle_btn.setMinimumHeight(38)
        self.recipients_toggle_btn.setMinimumWidth(130)
        self.recipients_toggle_btn.setMaximumWidth(146)
        self.recipients_toggle_btn.setIcon(
            self._load_colored_icon("recipients", "#cbd5e1", 24, vertical_offset=1)
        )
        self.recipients_toggle_btn.setIconSize(QSize(22, 22))
        self.recipients_toggle_btn.setToolTip(
            "Manually override the outgoing To and CC recipients for this run."
        )
        self.recipients_toggle_btn.clicked.connect(self._toggle_recipient_panel)
        controls_layout.addWidget(self.recipients_toggle_btn, 1, 1)

        self.settings_toggle_btn = QPushButton("Settings")
        self.settings_toggle_btn.setObjectName("InlineToggleButton")
        self.settings_toggle_btn.setCheckable(True)
        self.settings_toggle_btn.setMinimumHeight(50)
        self.settings_toggle_btn.setMinimumWidth(94)
        self.settings_toggle_btn.setMaximumWidth(100)
        self.settings_toggle_btn.setIcon(
            self._load_colored_icon("settings", "#67e8f9", 24, vertical_offset=1)
        )
        self.settings_toggle_btn.setIconSize(QSize(20, 20))
        self.settings_toggle_btn.setToolTip(
            "Show Remember ID, Headless Mode, and Header Color settings."
        )
        self.settings_toggle_btn.clicked.connect(self._toggle_settings_panel)
        controls_layout.addWidget(
            self.settings_toggle_btn,
            0,
            2,
            2,
            1,
            Qt.AlignmentFlag.AlignBottom,
        )
        controls_outer_layout.addStretch(1)
        controls_outer_layout.addWidget(controls_inner, 0, Qt.AlignmentFlag.AlignHCenter)
        controls_outer_layout.addStretch(1)
        auth_body_layout.addWidget(controls_frame)

        self.irt_filters_frame = QFrame()
        self.irt_filters_frame.setObjectName("InlineFiltersCard")
        self.irt_filters_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        irt_filters_outer_layout = QHBoxLayout(self.irt_filters_frame)
        irt_filters_outer_layout.setSpacing(0)
        irt_filters_outer_layout.setContentsMargins(10, 5, 10, 5)

        irt_filters_inner = QWidget()
        irt_filters_inner.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        irt_filters_layout = QHBoxLayout(irt_filters_inner)
        irt_filters_layout.setSpacing(8)
        irt_filters_layout.setContentsMargins(0, 0, 0, 0)

        self.irt_start_date_edit = ThemedDateEdit()
        self.irt_start_date_edit.setDisplayFormat("M/d/yyyy")
        self.irt_start_date_edit.setMinimumHeight(38)
        self._decorate_date_edit(self.irt_start_date_edit)
        self.irt_start_field = self._create_workflow_field(
            "From",
            self.irt_start_date_edit,
            176,
            194,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        irt_filters_layout.addWidget(self.irt_start_field, 1)

        self.workflow_divider_dates = self._create_workflow_divider()
        irt_filters_layout.addWidget(self.workflow_divider_dates)

        self.irt_end_date_edit = ThemedDateEdit()
        self.irt_end_date_edit.setDisplayFormat("M/d/yyyy")
        self.irt_end_date_edit.setMinimumHeight(38)
        self._decorate_date_edit(self.irt_end_date_edit)
        self.irt_end_field = self._create_workflow_field(
            "To",
            self.irt_end_date_edit,
            176,
            194,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        irt_filters_layout.addWidget(self.irt_end_field, 1)

        self._apply_default_irt_filters()
        self._sync_source_mode_layout()
        irt_filters_outer_layout.addStretch(1)
        irt_filters_outer_layout.addWidget(irt_filters_inner, 0, Qt.AlignmentFlag.AlignHCenter)
        irt_filters_outer_layout.addStretch(1)
        auth_body_layout.addWidget(self.irt_filters_frame)

        self.recipient_popup = RecipientOverridePopup(self)
        self.recipient_popup.closed.connect(self._on_recipient_popup_closed)
        self.recipient_to_input = self.recipient_popup.to_input
        self.recipient_cc_input = self.recipient_popup.cc_input
        self._restore_saved_recipient_overrides()
        self.recipient_to_input.textChanged.connect(self._persist_recipient_override_settings)
        self.recipient_cc_input.textChanged.connect(self._persist_recipient_override_settings)

        actions_divider = QFrame()
        actions_divider.setObjectName("WorkflowDivider")
        actions_divider.setFixedHeight(1)
        actions_divider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        auth_body_layout.addWidget(actions_divider)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)
        buttons_layout.setContentsMargins(4, 0, 4, 0)

        self.view_folder_btn = QPushButton("View Folder")
        self.view_folder_btn.setObjectName("GhostButton")
        self.view_folder_btn.setMinimumHeight(42)
        self.view_folder_btn.setMinimumWidth(142)
        self.view_folder_btn.setMaximumWidth(152)
        self.view_folder_btn.setIcon(
            self._load_colored_icon("results_folder", "#9fb2ca", 22, vertical_offset=1)
        )
        self.view_folder_btn.setIconSize(QSize(22, 22))
        self.view_folder_btn.clicked.connect(self.view_output_folder)
        buttons_layout.addWidget(self.view_folder_btn)

        buttons_layout.addStretch(1)

        self.extract_btn = QPushButton("Run Extraction")
        self.extract_btn.setObjectName("PrimaryButton")
        self.extract_btn.setMinimumHeight(44)
        self.extract_btn.setMinimumWidth(180)
        self.extract_btn.setMaximumWidth(190)
        self.extract_btn.setIcon(self._load_colored_icon("play", "#ffffff", 28, vertical_offset=1))
        self.extract_btn.setIconSize(QSize(28, 28))
        self.extract_btn.setEnabled(False)
        self.extract_btn.clicked.connect(self.start_extraction)
        buttons_layout.addWidget(self.extract_btn, 0, Qt.AlignmentFlag.AlignRight)

        auth_body_layout.addLayout(buttons_layout)
        auth_layout.addWidget(auth_body)
        self._idle_bubble_click_widgets = tuple(
            widget
            for widget in auth_card.findChildren(QWidget)
            if widget is not self.title_bar_surface
            and not self.title_bar_surface.isAncestorOf(widget)
            if not isinstance(
                widget,
                (
                    QLineEdit,
                    QPushButton,
                    QComboBox,
                    QDateEdit,
                    QCheckBox,
                    QToolButton,
                    QPlainTextEdit,
                    QListWidget,
                    QListView,
                    QCalendarWidget,
                ),
            )
        ) + (auth_card,)
        for bubble_click_widget in self._idle_bubble_click_widgets:
            bubble_click_widget.installEventFilter(self)

        main_layout.addWidget(auth_card)
        self._refresh_idle_wallpaper()

        runtime_card = self._create_card()
        self.runtime_card = runtime_card
        runtime_layout = QVBoxLayout(runtime_card)
        runtime_layout.setSpacing(0)
        runtime_layout.setContentsMargins(18, 18, 18, 18)

        self.runtime_window_controls = QWidget(runtime_card)
        self.runtime_window_controls.setObjectName("RuntimeWindowControls")
        self.runtime_window_controls.setStyleSheet("background: transparent; border: none;")
        runtime_controls_layout = QHBoxLayout(self.runtime_window_controls)
        runtime_controls_layout.setContentsMargins(0, 0, 0, 0)
        runtime_controls_layout.setSpacing(8)

        self.runtime_minimize_btn = QPushButton("—")
        self.runtime_minimize_btn.setObjectName("TitleBarButton")
        self.runtime_minimize_btn.setToolTip("Minimize")
        self.runtime_minimize_btn.clicked.connect(self.showMinimized)
        runtime_controls_layout.addWidget(self.runtime_minimize_btn)

        self.runtime_close_btn = QPushButton("×")
        self.runtime_close_btn.setObjectName("CloseTitleBarButton")
        self.runtime_close_btn.setToolTip("Close")
        self.runtime_close_btn.clicked.connect(self.close)
        runtime_controls_layout.addWidget(self.runtime_close_btn)
        self.runtime_window_controls.hide()

        runtime_content = QWidget()
        runtime_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        runtime_content_layout = QVBoxLayout(runtime_content)
        runtime_content_layout.setSpacing(0)
        runtime_content_layout.setContentsMargins(0, 0, 0, 0)
        runtime_content_layout.addSpacing(10)

        status_row = QHBoxLayout()
        self.status_row_layout = status_row
        status_row.setSpacing(8)
        status_row.setContentsMargins(0, 0, 0, 0)

        self.status_badge = QLabel()
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.status_badge.setMaximumWidth(220)
        status_row.addWidget(self.status_badge, 0)

        self.progress_meta_label = QLabel("IRT intake uses the selected date range across both courts.")
        self.progress_meta_label.setObjectName("ProgressMeta")
        self.progress_meta_label.setWordWrap(True)
        status_row.addWidget(self.progress_meta_label, 1)
        runtime_content_layout.addLayout(status_row)
        runtime_content_layout.addSpacing(16)

        self.progress_bar = AnimatedProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        runtime_content_layout.addWidget(self.progress_bar)
        runtime_content_layout.addSpacing(14)

        self.status_label = QLabel()
        self.status_label.setObjectName("ProgressLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(8, 0, 8, 0)
        runtime_content_layout.addWidget(self.status_label)
        runtime_content_layout.addSpacing(22)

        stop_row = QHBoxLayout()
        stop_row.setContentsMargins(0, 0, 0, 0)
        stop_row.addStretch(1)
        self.stop_btn = QPushButton("Stop Run")
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.setVisible(False)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_extraction)
        stop_row.addWidget(self.stop_btn)
        stop_row.addStretch(1)
        runtime_content_layout.addLayout(stop_row)

        self.runtime_loading_watermark = QLabel(runtime_card)
        self.runtime_loading_watermark.setObjectName("RuntimeWatermark")
        self.runtime_loading_watermark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.runtime_loading_watermark.setScaledContents(True)
        self.runtime_loading_watermark.setStyleSheet("background: transparent;")
        self.runtime_loading_watermark.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.runtime_loading_watermark.hide()
        self.runtime_loading_watermark.lower()
        self.runtime_loading_opacity = QGraphicsOpacityEffect(self.runtime_loading_watermark)
        self.runtime_loading_opacity.setOpacity(0.0)
        self.runtime_loading_watermark.setGraphicsEffect(self.runtime_loading_opacity)
        if self.loading_gif_path.exists():
            self.runtime_loading_movie = QMovie(str(self.loading_gif_path))
            self.runtime_loading_movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self.runtime_loading_movie.setSpeed(self._runtime_loading_speed)
            self.runtime_loading_watermark.setMovie(self.runtime_loading_movie)

        self.runtime_content = runtime_content
        self._runtime_drag_widgets = (
            runtime_card,
            runtime_content,
            self.status_badge,
            self.progress_meta_label,
            self.progress_bar,
            self.status_label,
        )
        for runtime_widget in self._runtime_drag_widgets:
            runtime_widget.installEventFilter(self)
        runtime_layout.addStretch(1)
        runtime_layout.addWidget(runtime_content)
        runtime_layout.addStretch(1)

        main_layout.addWidget(runtime_card)

        self._set_developer_mode_state(False)
        self._set_runtime_panel_mode(show_runtime=False)

    def load_saved_credentials(self):
        """Load saved credentials from the secure credential store."""
        try:
            credentials = load_credentials()
        except Exception as e:
            self._set_status_state(
                "Attention",
                f"Could not load saved credentials: {e}",
                "error",
            )
            return

        if credentials:
            self.id_input.setText(credentials.username)
            # Do not auto-fill password for better security.
            # User can re-enter it when starting extraction.
            self.save_credentials_checkbox.setChecked(True)
            self._set_status_state(
                "Ready",
                "Saved ID loaded. Re-enter your password to start.",
                "ready",
            )
            self.on_credentials_changed()

    def on_credentials_changed(self):
        """Handle credentials input change."""
        has_id = bool(self.id_input.text().strip())
        has_password = bool(self.password_input.text())
        self.extract_btn.setEnabled(has_id and has_password)

    def generate_template(self):
        """Generate formatted Excel template file in run subfolder."""
        try:
            from utils.excel_handler import ExcelHandler
            from utils.file_manager import FileManager

            file_manager = FileManager()
            run_folder = file_manager.create_run_folder()
            excel_handler = ExcelHandler(header_fill_color=self.header_fill_color)
            template_path = excel_handler.create_template(run_folder)

            self._set_status_state(
                "Ready",
                f"Manual template created: {template_path.name}",
                "ready",
            )
            excel_handler.open_file(template_path)

            self._show_success_dialog(
                "Template ready",
                (
                    "Opened and ready to edit.\n\n"
                    f"Location:\n{template_path}\n\n"
                    "When you're done, switch Source to Template before running extraction."
                ),
            )

        except Exception as e:
            self._set_status_state(
                "Issue",
                f"Failed to generate template: {e}",
                "error",
            )
            self._show_error_dialog(
                "Couldn't create template",
                f"Something went wrong while creating the template.\n\n{e}",
            )

    def view_output_folder(self):
        """Open the output results folder."""
        try:
            from utils.file_manager import FileManager

            file_manager = FileManager()
            if file_manager.open_results_folder():
                self._set_status_state(
                    "Ready",
                    "Results folder opened.",
                    "ready",
                )
            else:
                self._show_warning_dialog(
                    "Couldn't open results folder",
                    "Open it manually here:\n"
                    + str(file_manager.get_results_folder()),
                )
        except Exception as e:
            self._set_status_state(
                "Issue",
                f"Failed to open folder: {e}",
                "error",
            )
            self._show_error_dialog(
                "Couldn't open folder",
                f"Try opening the results folder manually.\n\n{e}",
            )

    def stop_extraction(self):
        """Request a cooperative stop for the active automation run."""
        if self.extraction_thread is None:
            return

        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping...")
        self.progress_meta_label.setText(
            "Stopping after the current step. Saving any progress so far."
        )
        self._set_status_state(
            "Stopping",
            "Stopping after the current step and saving progress...",
            "warning",
        )
        self.extraction_thread.request_stop()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop an active run first, then close cleanly once progress is saved."""
        if self.extraction_thread is None:
            super().closeEvent(event)
            return

        if self._close_requested_after_stop:
            event.ignore()
            return

        if self._confirm_dialog(
            "Stop this run?",
            (
                "A run is still active.\n\n"
                "Stop it, save any progress so far, and close the window when it's safe?"
            ),
            tone="warning",
            default_button="no",
        ):
            self._close_requested_after_stop = True
            if self.stop_btn.isEnabled():
                self.stop_extraction()
            event.ignore()
            return

        event.ignore()

    def _show_themed_dialog(
        self,
        title: str,
        message: str,
        tone: str = "info",
        buttons: tuple[str, ...] = ("ok",),
        default_button: str = "ok",
    ) -> str:
        """Show a custom in-theme modal dialog and return the selected button key."""
        dialog = ThemedMessageDialog(
            parent=self,
            title=title,
            message=message,
            tone=tone,
            buttons=buttons,
            default_button=default_button,
            title_font_family=self.title_font_family,
        )
        dialog.exec()
        return dialog.choice

    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show an informational dialog styled to match the app."""
        self._show_themed_dialog(title, message, tone="info", buttons=("ok",))

    def _show_success_dialog(self, title: str, message: str) -> None:
        """Show a success dialog styled to match the app."""
        self._show_themed_dialog(title, message, tone="success", buttons=("ok",))

    def _show_warning_dialog(self, title: str, message: str) -> None:
        """Show a warning dialog styled to match the app."""
        self._show_themed_dialog(title, message, tone="warning", buttons=("ok",))

    def _show_error_dialog(self, title: str, message: str) -> None:
        """Show an error dialog styled to match the app."""
        self._show_themed_dialog(title, message, tone="error", buttons=("ok",))

    def _confirm_dialog(
        self,
        title: str,
        message: str,
        tone: str = "question",
        default_button: str = "no",
    ) -> bool:
        """Show a themed Yes/No prompt and return True when the user confirms."""
        return (
            self._show_themed_dialog(
                title,
                message,
                tone=tone,
                buttons=("yes", "no"),
                default_button=default_button,
            )
            == "yes"
        )

    def start_extraction(self):
        """Start the extraction process."""
        from utils.file_manager import FileManager
        from utils.logger import Logger

        user_id = self.id_input.text().strip()
        password = self.password_input.text()

        if not user_id or not password:
            self._show_warning_dialog(
                "Add your credentials",
                "Enter your Lexis ID and password to continue.",
            )
            return

        recipient_to = self.recipient_to_input.toPlainText().strip()
        recipient_cc = self.recipient_cc_input.toPlainText().strip()

        if (recipient_to or recipient_cc) and not recipient_to:
            self._show_warning_dialog(
                "Add a To recipient",
                "Enter at least one To recipient before using a manual recipient override.",
            )
            return

        if self.developer_mode_enabled and not recipient_to:
            self._show_warning_dialog(
                "Add a test recipient",
                "In Developer Mode, add at least one To recipient before sending a test run.",
            )
            return

        file_manager = FileManager()
        source_mode = self._current_source_mode()
        excel_path = None
        irt_court_scope = "both"
        irt_start_date = None
        irt_end_date = None

        if source_mode == "irt":
            irt_start_date = self.irt_start_date_edit.date().toPyDate()
            irt_end_date = self.irt_end_date_edit.date().toPyDate()
            if irt_start_date > irt_end_date:
                self._show_warning_dialog(
                    "Check the date range",
                    "The From date must be on or before the To date.",
                )
                return

        if source_mode == "manual":
            excel_path = file_manager.find_most_recent_excel_file()
            if excel_path is None:
                self._show_warning_dialog(
                    "Template not found",
                    "Create and fill out a Template first, then close it before running extraction.",
                )
                return

            if file_manager.is_file_locked(excel_path):
                self._show_warning_dialog(
                    "Close the template first",
                    "Close the template workbook, then run extraction again.",
                )
                return

            run_folder = excel_path.parent
        else:
            run_folder = file_manager.create_run_folder()

        logger = Logger(file_manager)
        logger.initialize_log_file(run_folder)
        logger.log("Extraction started")
        if source_mode == "manual":
            logger.log(f"Source workbook will be loaded from Template: {excel_path}")
            if not self.developer_mode_enabled:
                logger.log(
                    "Outlook reply context will still be resolved from the latest matching email"
                )
        elif source_mode == "irt":
            logger.log("Source data will be imported from IRT Search Inventory")
            logger.log(
                "IRT filters selected: "
                f"court_scope={irt_court_scope} "
                f"start={irt_start_date.strftime('%m/%d/%Y')} "
                f"end={irt_end_date.strftime('%m/%d/%Y')}"
            )
        else:
            logger.log("Source data will be imported from IRT Search Inventory")
        logger.log(f"Workbook header color selected: #{self.header_fill_color}")
        if recipient_to:
            logger.log(
                (
                    "Developer mode enabled; outgoing email will use the Recipients override "
                    if self.developer_mode_enabled
                    else "Manual recipient override enabled; outgoing email will be sent "
                )
                + f"to To='{recipient_to}' CC='{recipient_cc}'"
            )
        if self.developer_mode_enabled and recipient_to:
            logger.log(
                "Developer mode is enabled and the Recipients override is controlling the safe test routing"
            )

        headless_mode = self.headless_checkbox.isChecked()
        remember_credentials = self.save_credentials_checkbox.isChecked()

        self.id_input.setEnabled(False)
        self.password_input.setEnabled(False)
        self.save_credentials_checkbox.setEnabled(False)
        self.headless_checkbox.setEnabled(False)
        self.header_color_combo.setEnabled(False)
        self.source_mode_combo.setEnabled(False)
        self.irt_filters_frame.setEnabled(False)
        self.recipients_toggle_btn.setEnabled(False)
        self.settings_toggle_btn.setEnabled(False)
        self.recipient_popup.hide()
        self.settings_popup.hide()
        self.developer_mode_shortcut.setEnabled(False)
        self.source_template_btn.setEnabled(False)
        self.extract_btn.setEnabled(False)
        self.stop_btn.setText("Stop Run")
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self._set_status_state(
            "Syncing",
            (
                "Opening manual workbook and starting extraction..."
                if source_mode == "manual"
                else (
                    "Importing IRT results and starting extraction..."
                    if source_mode == "irt"
                    else "Importing Outlook data and starting extraction..."
                )
            ),
            "working",
        )
        self._set_runtime_panel_mode(show_runtime=True)

        self.extraction_thread = ExtractionThread(
            user_id=user_id,
            password=password,
            excel_path=excel_path,
            logger=logger,
            file_manager=file_manager,
            headless_mode=headless_mode,
            run_folder=run_folder,
            remember_credentials=remember_credentials,
            developer_mode_enabled=self.developer_mode_enabled,
            developer_override_to="",
            developer_override_cc="",
            manual_override_to=recipient_to,
            manual_override_cc=recipient_cc,
            header_fill_color=self.header_fill_color,
            source_mode=source_mode,
            irt_court_scope=irt_court_scope,
            irt_start_date=irt_start_date,
            irt_end_date=irt_end_date,
        )
        self.extraction_thread.progress_update.connect(self.update_progress)
        self.extraction_thread.finished.connect(self.extraction_finished)
        self.extraction_thread.cancelled.connect(self.extraction_cancelled)
        self.extraction_thread.start()

    def update_progress(self, percentage: int, status: str):
        """Update progress bar and status."""
        self.progress_bar.setValue(percentage)
        self._set_status_state("Running", status, "working")

    def reset_ui(self):
        """Reset UI to initial state for next run."""
        self.progress_bar.setValue(0)

        self.id_input.setEnabled(True)
        self.password_input.setEnabled(True)
        self.save_credentials_checkbox.setEnabled(True)
        self.headless_checkbox.setEnabled(True)
        self.header_color_combo.setEnabled(True)
        self.source_mode_combo.setEnabled(True)
        self.irt_filters_frame.setEnabled(True)
        self.recipients_toggle_btn.setEnabled(True)
        self.settings_toggle_btn.setEnabled(True)
        self.developer_mode_shortcut.setEnabled(True)
        self._sync_source_mode_layout()
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.setText("Stop Run")

        self.on_credentials_changed()
        self._set_developer_mode_state(self.developer_mode_enabled)
        self._set_runtime_panel_mode(show_runtime=False)

    def extraction_finished(self, success: bool, message: str, output_path):
        """Handle extraction completion."""
        self.progress_bar.setValue(0)

        if success:
            self._set_status_state("Completed", message, "success")
            self._show_success_dialog("Extraction complete", message)
            self.open_output_file(output_path)
        else:
            self._set_status_state("Issue", message, "error")
            self._show_error_dialog("Extraction couldn't finish", message)

        self.extraction_thread = None
        self.reset_ui()
        if self._close_requested_after_stop:
            self._close_requested_after_stop = False
            QTimer.singleShot(0, self.close)

    def extraction_cancelled(self, message: str, output_path):
        """Handle a user-requested stop without treating it like a crash."""
        self.progress_bar.setValue(0)
        self._set_status_state("Stopped", message, "warning")
        self._show_info_dialog("Run stopped", message)
        self.extraction_thread = None
        self.reset_ui()
        if self._close_requested_after_stop:
            self._close_requested_after_stop = False
            QTimer.singleShot(0, self.close)

    def open_output_file(self, output_path):
        """Open the output Excel file after the user acknowledges success."""
        if output_path is None:
            return

        path = Path(output_path)
        if not path.exists():
            self._show_warning_dialog(
                "Output file not found",
                f"The run finished, but the output file wasn't found:\n{path}",
            )
            return

        try:
            from utils.excel_handler import ExcelHandler

            excel_handler = ExcelHandler()
            excel_handler.open_file(path)
        except Exception as e:
            self._show_warning_dialog(
                "Couldn't open the file",
                f"Excel couldn't open the results file:\n{e}",
            )
