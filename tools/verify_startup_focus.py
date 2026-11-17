"""Exercise temporary topmost behavior using only generated test windows."""
import argparse
import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import time

from tools.windows_capture import window_bounds


def configure_user():
    user = ctypes.WinDLL('user32', use_last_error=True)
    # This helper also runs without a Qt application; keep hit testing in the
    # same physical coordinates returned by window_bounds on scaled displays.
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    user.GetWindowLongW.argtypes, user.GetWindowLongW.restype = [wintypes.HWND, ctypes.c_int], wintypes.LONG
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.WindowFromPoint.argtypes, user.WindowFromPoint.restype = [wintypes.POINT], wintypes.HWND
    user.WindowFromPhysicalPoint.argtypes, user.WindowFromPhysicalPoint.restype = [wintypes.POINT], wintypes.HWND
    user.GetAncestor.argtypes, user.GetAncestor.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
    user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    return user


def start_outside_window():
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-m', 'tools.verify_startup_focus', '--outside'],
                               cwd=root, stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    executor = ThreadPoolExecutor(1)
    try:
        line = executor.submit(process.stdout.readline).result(timeout=40)
        assert line.startswith('READY '), line
        return process, int(line[6:].strip())
    except Exception:
        process.terminate()
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def click_outside_window(main_hwnd, outside_hwnd):
    """Click only an unobscured client point of our generated outside window."""
    user = configure_user()
    if user.IsIconic(outside_hwnd):
        user.ShowWindow(outside_hwnd, 9)
    # The test background can sit behind the host app during a long EXE launch.
    # Raise only that generated window, without activating it or covering a pin.
    user.SetWindowPos(outside_hwnd, 0, 0, 0, 0, 0, 0x0213)
    left, top, right, bottom = window_bounds(outside_hwnd)
    ml, mt, mr, mb = window_bounds(main_hwnd)
    candidates = [(left+30, top+90), (right-30, top+90), (left+30, bottom-50), (right-30, bottom-50)]
    hits = [(x,y,user.GetAncestor(user.WindowFromPhysicalPoint(wintypes.POINT(x,y)), 2)) for x,y in candidates]
    if not any(root == outside_hwnd for _,_,root in hits):
        # Keep the test window directly behind our pinned app if another
        # always-on-top host window is covering the generated background.
        user.SetWindowPos(outside_hwnd, -1, 0, 0, 0, 0, 0x0213)
        user.SetWindowPos(outside_hwnd, main_hwnd, 0, 0, 0, 0, 0x0213)
        hits = [(x,y,user.GetAncestor(user.WindowFromPhysicalPoint(wintypes.POINT(x,y)), 2)) for x,y in candidates]
    point = next(((x,y) for x,y,root in hits if not (ml <= x < mr and mt <= y < mb) and root == outside_hwnd), None)
    assert point, ('Generated outside window is obscured',outside_hwnd,(left,top,right,bottom),(ml,mt,mr,mb),hits)
    old = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(old))
    try:
        user.SetCursorPos(*point)
        user.mouse_event(0x0002, 0, 0, 0, 0)
        user.mouse_event(0x0004, 0, 0, 0, 0)
    finally:
        user.SetCursorPos(old.x, old.y)


def outside_worker():
    from PyQt6.QtWidgets import QApplication, QLabel
    from PyQt6.QtCore import Qt
    app = QApplication([])
    window = QLabel('Generated window for startup focus verification')
    window.setWindowTitle('Startup focus verification background')
    window.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window.setStyleSheet('background:#263851;color:#c9e7f0;font-size:16px;')
    window.resize(900, 650)
    window.move(app.primaryScreen().availableGeometry().center()-window.rect().center())
    window.show()
    print('READY '+str(int(window.winId())), flush=True)
    app.exec()


def main():
    from PyQt6.QtWidgets import QApplication, QLabel
    from PyQt6.QtCore import Qt
    from gui.startup_focus import StartupFocus
    app = QApplication([])
    outside, outside_hwnd = start_outside_window()
    user = configure_user()
    windows = []

    def pump(condition, timeout=3):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return True
            time.sleep(.01)
        return condition()

    def new_window():
        window = QLabel('Temporary startup foreground check')
        window.setWindowTitle('Startup foreground verification')
        window.setAlignment(Qt.AlignmentFlag.AlignCenter)
        window.setStyleSheet('background:#0b1829;color:#67e8f9;font-size:17px;')
        window.resize(440, 280)
        window.move(app.primaryScreen().availableGeometry().center()-window.rect().center())
        window.show()
        window.startup_focus = StartupFocus(window)
        window.startup_focus.after_intro()
        windows.append(window)
        assert pump(lambda: window.startup_focus.is_pinned)
        assert window.startup_focus.api.hook, 'Outside-click observation did not install'
        return window, int(window.winId())

    try:
        window, hwnd = new_window()
        assert user.GetWindowLongW(hwnd, -20) & 8
        # Another app can activate automatically; our main window must stay above it.
        user.SetWindowPos(outside_hwnd, 0, 0, 0, 0, 0, 0x0213)
        user.SetForegroundWindow(outside_hwnd)
        pump(lambda: False, .25)
        assert user.GetWindowLongW(hwnd, -20) & 8, 'Automatic activation removed the startup pin'
        bounds = window_bounds(hwnd)
        center = wintypes.POINT((bounds[0]+bounds[2])//2, (bounds[1]+bounds[3])//2)
        assert user.GetAncestor(user.WindowFromPoint(center), 2) == hwnd, 'Another window covered the pinned main window'
        click_outside_window(hwnd, outside_hwnd)
        assert pump(lambda: not window.startup_focus.is_pinned)
        assert not user.GetWindowLongW(hwnd, -20) & 8
        assert user.GetAncestor(user.WindowFromPoint(center), 2) == outside_hwnd, 'The clicked window remained behind the main app'
        assert window.startup_focus.api.hook is None
        window.startup_focus.after_intro()
        pump(lambda: False, .1)
        assert not window.startup_focus.is_pinned, 'The released startup pin re-armed'
        window.close()

        window, hwnd = new_window()
        window.showMinimized()
        assert pump(lambda: not window.startup_focus.is_pinned)
        assert not user.GetWindowLongW(hwnd, -20) & 8
        window.showNormal()
        pump(lambda: False, .1)
        assert not user.GetWindowLongW(hwnd, -20) & 8, 'Restore re-applied the temporary pin'
        print('PASS: main stays above automatic activation, outside click releases, minimize releases, restore does not re-pin', flush=True)
    finally:
        for window in windows:
            window.close()
        user.PostMessageW(outside_hwnd, 0x0010, 0, 0)
        try:
            outside.wait(timeout=10)
        except subprocess.TimeoutExpired:
            outside.terminate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outside', action='store_true')
    args = parser.parse_args()
    outside_worker() if args.outside else main()
