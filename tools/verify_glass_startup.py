"""Check real desktop acrylic, the minimize button, taskbar restore and IPC."""
import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import time
import tkinter
from unittest.mock import patch

import psutil
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from tools.native_startup_build import build_native_startup
from tools.startup_splash_build import ExtractorSplash, STATUS_POSITION, create_startup_artwork, create_water_frames
from utils import startup_feedback
from tools.windows_capture import capture_region, window_bounds


class Backdrop(QWidget):
    flipped = False

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = ['#d9edf5', '#166c91', '#ad729b'] if not self.flipped else ['#80bbbd', '#493b6b', '#d3dbed']
        for y in range(0, self.height(), 24):
            for x in range(0, self.width(), 24):
                painter.fillRect(x, y, 24, 24, QColor(colors[(x//24+y//24)%3]))
        painter.end()


def main():
    from PIL import Image, ImageChops, ImageStat
    root_path = Path(__file__).resolve().parents[1]
    output = root_path / 'outputs/startup-preview'
    output.mkdir(parents=True, exist_ok=True)
    opaque = create_startup_artwork(output / 'startup_splash.png', root_path)
    glass = create_startup_artwork(output / 'startup_glass.png', root_path, frosted=True)
    frames = sorted((output / 'startup_water').glob('water_*.png')) or create_water_frames(output / 'startup_water')
    native = build_native_startup(root_path, output, glass, frames)
    splash = object.__new__(ExtractorSplash)
    splash.startup_files = {'PyQt6/Qt6/bin/Qt6Core.dll': 100, 'assets/test.dat': 100}
    splash.water_frames = frames
    splash.script_name = str(output / 'startup_preview.tcl')
    splash.minify_script = False
    splash.text_pos, splash.text_size, splash.text_font = STATUS_POSITION, 11, '{Segoe UI}'
    splash.text_color, splash.text_default = '#d4e6f4', '@stage:0|Preparing application...'
    splash.always_on_top = True
    script = splash.generate_script()
    app = QApplication([])
    background = Backdrop()
    background.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    background.setWindowTitle('Startup glass verification background')
    background.resize(1100, 700)
    background.move(app.primaryScreen().availableGeometry().center()-background.rect().center())
    background.show()
    background.raise_()
    tk = tkinter.Tk()
    tk.withdraw()
    process = None
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = ctypes.c_void_p
    user.IsIconic.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    old_cursor = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(old_cursor))

    def pump(until=lambda: False, timeout=.2):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            tk.update()
            app.processEvents()
            if until():
                return True
            time.sleep(.01)
        return until()

    try:
        tk.tk.call('set', 'startup_native_executable', str(native))
        tk.tk.call('set', '_image_data', opaque.read_bytes())
        tk.tk.eval(script)
        assert pump(lambda: int(tk.tk.call('set', 'startup_native_active')) == 1, 20), 'Native loader did not connect'
        process = psutil.Process(int(tk.tk.call('set', 'startup_native_pid')[0]))
        handles = []

        @callback
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid and user.GetPropW(hwnd, 'PLRGlassMode'):
                handles.append(hwnd)
            return True

        user.EnumWindows(visit, 0)
        assert handles, 'Native glass window was not found'
        hwnd = handles[0]
        assert user.GetPropW(hwnd, 'PLRGlassMode') in (1, 2), 'Native acrylic/backdrop was not enabled'
        bounds = window_bounds(hwnd)
        width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
        print('Native window size:',width,height,flush=True)

        def shot():
            return capture_region(bounds)

        def region(image, box):
            return image.crop(tuple(round(value*image.width/560) for value in box))

        tk.tk.call('set', 'status_text', 'PyQt6/Qt6/bin/Qt6Core.dll')
        tk.tk.call('set', 'status_text', 'assets\\test.dat')
        assert tk.tk.call('.root.canvas', 'itemcget', 'startup_percentage', '-text') == '27%'
        tk.tk.call('set', 'status_text', '@stage:64|Loading application components...')
        assert pump(lambda: user.GetPropW(hwnd, 'PLRStartupProgress') == 65, 3)
        first = shot()
        first.save(output / 'startup_glass_desktop.png')
        pump(timeout=.3)
        second = shot()
        second.save(output / 'startup_glass_next.png')
        assert ImageChops.difference(region(first, (30, 195, 530, 275)), region(second, (30, 195, 530, 275))).getbbox(), 'Water did not animate'

        # Drive the actual drawn minimize control, rather than ShowWindow alone.
        scale = width/560
        x, y = round(524*scale), round(32*scale)
        user.SetCursorPos(bounds[0]+round(230*scale), bounds[1]+round(120*scale))
        pump(timeout=.1)
        normal_button = shot()
        user.SetCursorPos(bounds[0]+x, bounds[1]+y)
        pump(timeout=.15)
        hovered_button = shot()
        hovered_button.save(output / 'minimize_hover.png')
        user.SendMessageW(hwnd, 0x0201, 1, (y << 16) | x)
        pump(timeout=.12)
        assert not user.IsIconic(hwnd), 'Minimize should occur on button release'
        pressed_button = shot()
        pressed_button.save(output / 'minimize_pressed.png')

        def check_round_highlight(image, name):
            difference = ImageChops.difference(normal_button, image)
            corner = sum(ImageStat.Stat(region(difference, (508, 18, 510, 20))).mean)/3
            middle = sum(ImageStat.Stat(region(difference, (519, 22, 530, 26))).mean)/3
            assert middle > 2 and corner < middle*.6+2, (name, 'Highlight has square corners',corner,middle)

        check_round_highlight(hovered_button, 'Hover')
        check_round_highlight(pressed_button, 'Pressed')
        user.SendMessageW(hwnd, 0x0202, 0, (y << 16) | x)
        assert pump(lambda: user.IsIconic(hwnd) and int(tk.tk.call('set', 'startup_minimized')) == 1, 3), ('Minimize state',user.IsIconic(hwnd),tk.tk.call('set','startup_minimized'),(x,y))
        user.SetCursorPos(old_cursor.x, old_cursor.y)
        config = {key: str(tk.tk.call('set', 'env('+key+')')) for key in ('PLR_STARTUP_CONTROL_PORT', 'PLR_STARTUP_CONTROL_TOKEN')}
        with patch.object(startup_feedback, 'pyi_splash', object()), patch.object(startup_feedback, '_closed', False), patch.dict(os.environ, config), ThreadPoolExecutor(1) as executor:
            future = executor.submit(startup_feedback.startup_is_minimized)
            assert pump(future.done, 3) and future.result(), 'Minimized choice did not reach Python'
        background_only = shot()
        tk.tk.call('set', 'status_text', '@stage:80|Preparing your workspace...')
        assert pump(lambda: user.GetPropW(hwnd, 'PLRStartupProgress') == 81, 3), 'Loading stalled while minimized'
        pump(timeout=.75)
        assert user.IsIconic(hwnd) and int(tk.tk.call('set','startup_minimized')) == 1, 'A redraw restored the minimized splash'
        user.ShowWindow(hwnd, 9)
        assert pump(lambda: not user.IsIconic(hwnd) and int(tk.tk.call('set', 'startup_minimized')) == 0, 3)
        pump(timeout=.3)
        frosted = shot()
        sample = (25, 164, 180, 175)
        contrast_before = sum(ImageStat.Stat(region(background_only, sample)).stddev)
        contrast_after = sum(ImageStat.Stat(region(frosted, sample)).stddev)
        assert contrast_after < contrast_before*.75, ('Backdrop was not blurred', contrast_before, contrast_after)
        background.flipped = True
        background.update()
        pump(timeout=.5)
        changed = shot()
        assert ImageChops.difference(region(frosted, sample), region(changed, sample)).getbbox(), 'Desktop did not show through the frosted card'
        changed.save(output / 'startup_window.png')
        captures = []
        for _ in range(24):
            captures.append(shot())
            pump(timeout=.08)
        palette = captures[0].quantize(colors=256)
        captures = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in captures]
        captures[0].save(output / 'startup_water_preview.gif', save_all=True, append_images=captures[1:], duration=80, loop=0)
        print('PASS: real frosted backdrop, moving water, minimize button, restore, progress while minimized and Python state handoff', flush=True)
        print({'glass_mode': user.GetPropW(hwnd, 'PLRGlassMode'), 'contrast_before': round(contrast_before,2), 'contrast_after': round(contrast_after,2), 'window_pixels': (width,height)})
    finally:
        user.SetCursorPos(old_cursor.x, old_cursor.y)
        channel = tk.tk.call('set', 'startup_native_channel') if tk.tk.eval('info exists startup_native_channel') == '1' else ''
        if channel:
            tk.tk.call('close', channel)
        tk.destroy()
        background.close()
        if process:
            try:
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                process.terminate()


if __name__ == '__main__':
    main()
