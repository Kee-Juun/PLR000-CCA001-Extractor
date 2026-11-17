"""Keep the revealed workspace visible until the user chooses another window."""
import ctypes
from ctypes import wintypes
import os
import sys

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication


class _WindowsFocusApi:
    """Change only this window's Z order; never intercept another app's input."""

    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.SetWindowPos.restype = wintypes.BOOL
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongW.restype = wintypes.LONG
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self.hook_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.user.SetWindowsHookExW.argtypes = [ctypes.c_int, self.hook_type, wintypes.HINSTANCE, wintypes.DWORD]
        self.user.SetWindowsHookExW.restype = ctypes.c_void_p
        self.user.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self.user.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallNextHookEx.restype = ctypes.c_ssize_t
        self.hook = None
        self.callback = None

    def set_topmost(self, hwnd, enabled):
        # Preserve geometry and activation; avoid recreating the translucent Qt HWND.
        applied = bool(self.user.SetWindowPos(hwnd, -1 if enabled else -2, 0, 0, 0, 0, 0x0213))
        if not enabled and applied:
            foreground = self.user.GetForegroundWindow()
            if (self._belongs_to_app(foreground) is False
                    and not self.user.GetWindowLongW(foreground, -20) & 8):
                # HWND_NOTOPMOST puts us at the front of the normal Z-order.
                # Leave the app the user clicked above us instead.
                self.user.SetWindowPos(hwnd, foreground, 0, 0, 0, 0, 0x0213)
        return applied

    def _belongs_to_app(self, hwnd):
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()

    def watch_outside_click(self, notify):
        # A deactivation event alone misses a click into an already-active app
        # when Windows declined our initial activation. Observe only button-down
        # location/ownership; store no input and pass every event through.
        class MouseInfo(ctypes.Structure):
            _fields_ = [('point', wintypes.POINT), ('data', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                        ('extra', ctypes.c_size_t)]

        def on_mouse(code, message, data):
            if code >= 0 and message in (0x0201, 0x0204, 0x0207, 0x020B):
                try:
                    point = ctypes.cast(data, ctypes.POINTER(MouseInfo)).contents.point
                    if self._belongs_to_app(self.user.WindowFromPoint(point)) is not True:
                        notify()
                except (OSError, ValueError, RuntimeError):
                    pass
            return self.user.CallNextHookEx(self.hook, code, message, data)

        self.callback = self.hook_type(on_mouse)
        self.hook = self.user.SetWindowsHookExW(14, self.callback, self.kernel.GetModuleHandleW(None), 0)
        return bool(self.hook)

    def stop_watching(self):
        if self.hook:
            self.user.UnhookWindowsHookEx(self.hook)
            self.hook = None
        self.callback = None


class StartupFocus(QObject):
    """A one-time topmost handoff, released by minimize or outside interaction."""

    release_requested = pyqtSignal()

    def __init__(self, window, *, platform_api=None):
        super().__init__(window)
        self.window = window
        self.api = platform_api
        if self.api is None and sys.platform == 'win32' and QApplication.platformName() == 'windows':
            self.api = _WindowsFocusApi()
        self._started = False
        self._released = False
        self._pinned = False
        self._hwnd = None
        self.release_requested.connect(self.release, Qt.ConnectionType.QueuedConnection)
        window.installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self.release)

    @property
    def is_pinned(self):
        return self._pinned

    def after_intro(self):
        if self._started or self._released:
            return
        self._started = True
        # Run after the intro's queued final restore, so no later show/raise
        # operation replaces the chosen Z order.
        QTimer.singleShot(0, self._pin)

    def _pin(self):
        if self._released or self.window.isMinimized() or not self.window.isVisible():
            self.release()
            return
        self._hwnd = int(self.window.winId())
        if self.api:
            self.api.watch_outside_click(self.release_requested.emit)
            self._pinned = self.api.set_topmost(self._hwnd, True)
            if not self._pinned:
                self.api.stop_watching()
        else:
            self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.window.show()
            self._pinned = True

    def release(self):
        if self._released:
            return
        self._released = True
        if self.api:
            self.api.stop_watching()
            if self._pinned:
                self.api.set_topmost(self._hwnd, False)
        elif self._pinned:
            was_visible = self.window.isVisible()
            self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            if was_visible:
                self.window.show()
        self._pinned = False

    def eventFilter(self, watched, event):
        if watched is self.window and self._pinned:
            kind = event.type()
            if kind == QEvent.Type.Hide:
                self.release()
            elif kind == QEvent.Type.WindowStateChange and self.window.isMinimized():
                self.release()
            elif kind == QEvent.Type.WindowDeactivate and self.api is None:
                # Qt fallback on non-Windows platforms. Windows observes the
                # outside click itself, so automatic activation cannot unpin us.
                self.release()
        return super().eventFilter(watched, event)
