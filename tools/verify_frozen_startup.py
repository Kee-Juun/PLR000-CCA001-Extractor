"""Check the rebuilt EXE's native splash and handoff without running extraction."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time
import socket

import psutil
from PIL import ImageChops
from tools.windows_capture import capture_region, window_bounds
from tools.verify_startup_focus import configure_user, start_outside_window, click_outside_window


def main():
    # Initialize the same interactive Qt/DPI context used by the app before
    # testing cross-process hit targets on a scaled Windows desktop.
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--stay-minimized', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / 'outputs/startup-preview'
    output.mkdir(parents=True, exist_ok=True)
    user = configure_user()
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = ctypes.c_void_p
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = wintypes.LONG
    outside, outside_hwnd = start_outside_window()
    started = time.monotonic()
    process = subprocess.Popen([str(args.exe.resolve())], cwd=str(root), creationflags=subprocess.CREATE_NO_WINDOW)
    observed = {'process_created_seconds': round(time.monotonic()-started, 2),
                'splash_seconds': None, 'ready_seconds': None, 'glass_mode': None,
                'activity_changed': False, 'percentage_changed': False,
                'minimize_button': False, 'restored': False, 'main_minimized': None,
                'topmost_after_intro': None, 'outside_click_released': None, 'clicked_window_in_front': None}
    owned, captures = [], []

    def windows():
        nonlocal owned
        try:
            parent = psutil.Process(process.pid)
            owned = [parent, *parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []
        ids, result = {item.pid for item in owned}, []

        @callback
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in ids and user.IsWindowVisible(hwnd):
                title, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
                user.GetWindowTextW(hwnd, title, len(title))
                user.GetClassNameW(hwnd, kind, len(kind))
                result.append((hwnd, title.value, kind.value))
            return True

        user.EnumWindows(visit, 0)
        return result

    def capture(hwnd, filename):
        image = capture_region(window_bounds(hwnd))
        image.save(output / filename)
        return image

    def water(image):
        scale = image.width/560
        return image.crop(tuple(round(value*scale) for value in (30, 195, 530, 275)))

    def click_minimize(hwnd):
        bounds = window_bounds(hwnd)
        scale = (bounds[2]-bounds[0])/560
        x, y = bounds[0]+round(524*scale), bounds[1]+round(32*scale)
        assert user.GetAncestor(user.WindowFromPhysicalPoint(wintypes.POINT(x,y)), 2) == hwnd, 'Minimize button is obscured'
        old = wintypes.POINT()
        user.GetCursorPos(ctypes.byref(old))
        try:
            user.SetCursorPos(x,y)
            user.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(.06)
            user.mouse_event(0x0004, 0, 0, 0, 0)
        finally:
            user.SetCursorPos(old.x,old.y)

    try:
        first_capture_time, first_progress, minimized_at = None, None, None
        deadline = started+args.timeout
        while time.monotonic() < deadline:
            app.processEvents()
            current = windows()
            error = next((hwnd for hwnd, title, kind in current
                          if kind == '#32770' and title in ('Error', 'PLR000-CCA001 Extractor')), None)
            if error:
                messages = []

                @callback
                def read_error(hwnd, _):
                    message = ctypes.create_unicode_buffer(4096)
                    user.SendMessageW(hwnd, 0x000D, len(message), ctypes.addressof(message))
                    if message.value and message.value != 'OK':
                        messages.append(message.value)
                    return True

                user.EnumChildWindows(error, read_error, 0)
                raise AssertionError('Startup error: '+'\n'.join(messages))
            splash = next((hwnd for hwnd, title, _kind in current
                           if title == 'PLR000-CCA001 Extractor - Starting'
                           and user.GetPropW(hwnd, 'PLRGlassMode') in (1, 2)), None)
            main_window = next((hwnd for hwnd, title, kind in current
                                if title == 'PLR000-CCA001 Extractor' and kind.startswith('Qt')), None)
            if splash and observed['splash_seconds'] is None:
                observed['splash_seconds'] = round(time.monotonic()-started, 2)
                observed['glass_mode'] = user.GetPropW(splash, 'PLRGlassMode')
                assert main_window is None, 'Main window appeared before startup feedback'
                assert user.GetWindowLongW(splash, -20) & 0x40000, 'Splash lacks a taskbar entry'
                assert user.GetWindowLongW(splash, -16) & 0x20000, 'Splash cannot minimize'
                first_progress = user.GetPropW(splash, 'PLRStartupProgress')
                first_capture_time = time.monotonic()
                if args.stay_minimized:
                    # Exercise an early real click; animation is checked in the
                    # normal run, before its separate minimize/restore cycle.
                    observed['progress_at_minimize'] = first_progress
                    click_minimize(splash)
                    minimized_at = time.monotonic()
                    observed['activity_changed'] = None
                else:
                    captures.append(capture(splash, 'frozen_startup_first.png'))
                print('Native acrylic splash visible at', observed['splash_seconds'], 'seconds', flush=True)
            if splash:
                value = user.GetPropW(splash, 'PLRStartupProgress')
                if first_progress and value and value != first_progress:
                    observed['percentage_changed'] = True
                if not args.stay_minimized and len(captures) == 1 and time.monotonic()-first_capture_time > .65:
                    captures.append(capture(splash, 'frozen_startup_activity.png'))
                    observed['activity_changed'] = ImageChops.difference(water(captures[0]), water(captures[1])).getbbox() is not None
                    assert observed['activity_changed'], 'Startup water did not move'
                    scale = captures[1].width/560
                    x, y = round(524*scale), round(32*scale)
                    user.SendMessageW(splash, 0x0201, 1, (y << 16) | x)
                    user.SendMessageW(splash, 0x0202, 0, (y << 16) | x)
                    minimized_at = time.monotonic()
                if minimized_at is not None:
                    observed['minimize_button'] |= bool(user.IsIconic(splash))
                    if args.stay_minimized:
                        observed['native_currently_minimized'] = bool(user.IsIconic(splash))
                        if 'runtime_control_env_present' not in observed:
                            children = [p for p in owned if p.pid != process.pid and p.name().lower() == 'plr-cca extractor.exe']
                            if children:
                                env = children[0].environ()
                                port, token = env.get('PLR_STARTUP_CONTROL_PORT'), env.get('PLR_STARTUP_CONTROL_TOKEN')
                                observed['runtime_control_env_present'] = bool(port and token)
                                if port and token:
                                    try:
                                        with socket.create_connection(('127.0.0.1',int(port)),timeout=.5) as channel:
                                            channel.sendall(('QUERY\t'+token+'\n').encode('utf-8'))
                                            observed['runtime_reported_minimized'] = channel.recv(256).decode('utf-8').split('\t')[1]
                                    except (OSError,ValueError,IndexError):
                                        observed['runtime_reported_minimized'] = 'unavailable'
                    if not args.stay_minimized and time.monotonic()-minimized_at > .15 and not observed['restored']:
                        user.ShowWindow(splash, 9)
                        observed['restored'] = not user.IsIconic(splash)
            if main_window and not splash and observed['splash_seconds'] is not None:
                observed['ready_seconds'] = round(time.monotonic()-started, 2)
                observed['main_minimized'] = bool(user.IsIconic(main_window))
                assert observed['main_minimized'] == args.stay_minimized, ('Main app did not preserve the minimized choice',observed)
                if args.stay_minimized:
                    observed['topmost_after_intro'] = bool(user.GetWindowLongW(main_window, -20) & 8)
                    assert not observed['topmost_after_intro'], 'Background startup unexpectedly pinned the main app'
                else:
                    until = time.monotonic()+8
                    while time.monotonic() < until and not user.GetWindowLongW(main_window, -20) & 8:
                        app.processEvents()
                        time.sleep(.025)
                    observed['topmost_after_intro'] = bool(user.GetWindowLongW(main_window, -20) & 8)
                    assert observed['topmost_after_intro'], 'Main app did not stay above other windows after the intro'
                    bounds = window_bounds(main_window)
                    center = wintypes.POINT((bounds[0]+bounds[2])//2, (bounds[1]+bounds[3])//2)
                    click_outside_window(main_window, outside_hwnd)
                    until = time.monotonic()+3
                    while time.monotonic() < until and user.GetWindowLongW(main_window, -20) & 8:
                        app.processEvents()
                        time.sleep(.025)
                    observed['outside_click_released'] = not bool(user.GetWindowLongW(main_window, -20) & 8)
                    assert observed['outside_click_released'], 'Outside click did not release the main-window startup pin'
                    observed['clicked_window_in_front'] = user.GetAncestor(user.WindowFromPhysicalPoint(center), 2) == outside_hwnd
                    assert observed['clicked_window_in_front'], 'The window clicked remained behind the main app'
                user.PostMessageW(main_window, 0x0010, 0, 0)
                break
            if process.poll() is not None:
                raise AssertionError('EXE exited before the main window appeared')
            time.sleep(.03)
        assert observed['ready_seconds'] is not None, 'Startup did not reach the main window before timeout'
        assert (args.stay_minimized or observed['activity_changed']) and observed['percentage_changed'], 'Startup feedback did not advance'
        assert observed['minimize_button'], 'Minimize button did not work'
        assert args.stay_minimized or observed['restored'], 'Taskbar restore failed'
        process.wait(timeout=60)
        assert process.returncode == 0, process.returncode
        name = 'frozen_startup_minimized_check.json' if args.stay_minimized else 'frozen_startup_check.json'
        (output / name).write_text(json.dumps(observed, indent=2), encoding='utf-8')
        print('PASS: packaged acrylic, water, percentage, minimize, restore/handoff and clean exit', observed)
    finally:
        if process.poll() is None:
            for child in reversed(owned):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
        user.PostMessageW(outside_hwnd, 0x0010, 0, 0)
        try:
            outside.wait(timeout=10)
        except subprocess.TimeoutExpired:
            outside.terminate()


if __name__ == '__main__':
    main()
