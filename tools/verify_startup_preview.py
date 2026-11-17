"""Exercise and capture the bootloader's fallback startup card."""
from pathlib import Path
import time
import tkinter

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage, QPainter
from tools.startup_splash_build import (
    ExtractorSplash, STATUS_POSITION, WATER_FRAMES, create_startup_artwork, create_water_frames,
)


def main():
    output = Path(__file__).resolve().parents[1] / 'outputs' / 'startup-preview' / 'fallback'
    output.mkdir(parents=True, exist_ok=True)
    image = create_startup_artwork(output / 'startup_splash.png', output.parents[1])
    # Generate exactly the script used by the bootloader without running Analysis.
    splash = object.__new__(ExtractorSplash)
    splash.startup_files = {'PyQt6/Qt6/bin/Qt6Core.dll': 100, 'assets/test.dat': 100}
    splash.water_frames = create_water_frames(output / 'startup_water')
    splash.script_name = str(output / 'startup_preview.tcl')
    splash.minify_script = False
    splash.text_pos = STATUS_POSITION
    splash.text_size = 11
    splash.text_font = '{Segoe UI}'
    splash.text_color = '#d4e6f4'
    splash.text_default = '@stage:0|Preparing application...'
    splash.always_on_top = True
    script = splash.generate_script()
    app = QApplication.instance() or QApplication([])
    root = tkinter.Tk()
    root.withdraw()
    try:
        root.tk.call('set', '_image_data', image.read_bytes())
        root.tk.eval(script)
        assert root.tk.call('wm', 'attributes', '.', '-transparentcolor') == 'magenta'
        root.deiconify()
        root.update()
        canvas = '.root.canvas'
        original = root.tk.call(canvas, 'itemcget', 'vartext', '-text')
        root.tk.call('set', 'status_text', 'PyQt6/Qt6/bin/Qt6Core.dll')
        root.update()
        assert root.tk.call(canvas, 'itemcget', 'vartext', '-text') == original
        assert root.tk.call(canvas, 'itemcget', 'startup_percentage', '-text') == '0%'
        root.tk.call('set', 'status_text', 'assets\\test.dat')
        assert root.tk.call(canvas, 'itemcget', 'startup_percentage', '-text') == '27%'
        root.tk.call('set', 'status_text', 'next-file')
        assert root.tk.call(canvas, 'itemcget', 'startup_percentage', '-text') == '55%'
        root.tk.call('set', 'status_text', '@stage:64|Loading application components...')
        root.update()
        assert root.tk.call(canvas, 'itemcget', 'vartext', '-text') == 'Loading application components...'
        assert root.tk.call(canvas, 'itemcget', 'startup_percentage', '-text') == '64%'
        root.tk.call('set', 'status_text', '@stage:56|Loading application components...')
        assert root.tk.call(canvas, 'itemcget', 'startup_percentage', '-text') == '64%', 'Progress regressed'
        assert int(root.tk.call('font', 'measure', 'startup_percent_font', '100%')) <= 160, '100% crosses the divider'
        deadline = time.monotonic() + 5
        while int(root.tk.call('set', 'startup_loaded')) < WATER_FRAMES and time.monotonic() < deadline:
            root.update()
            time.sleep(.005)
        assert int(root.tk.call('set', 'startup_loaded')) == WATER_FRAMES
        before = root.tk.call(canvas, 'itemcget', 'startup_water', '-image')
        deadline = time.monotonic() + .4
        while time.monotonic() < deadline:
            root.update()
            app.processEvents()
            time.sleep(.01)
        assert before != root.tk.call(canvas, 'itemcget', 'startup_water', '-image')

        def capture():
            shot = app.primaryScreen().grabWindow(int(root.winfo_id()))
            assert not shot.isNull()
            # Match the transparent outline Windows composites on screen.
            rendered = shot.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(rendered)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(QRect(0, 0, rendered.width(), rendered.height()), QImage(str(image)))
            painter.end()
            for y in range(rendered.height()):
                for x in range(rendered.width()):
                    if rendered.pixel(x, y) == 0xffff00ff:
                        rendered.setPixel(x, y, 0)
            return rendered

        rendered = capture()
        assert rendered.save(str(output / 'startup_window.png'))
        # Record each native-rendered frame for an exact looping design preview.
        from PIL import Image
        frames = []
        for frame in range(WATER_FRAMES):
            root.tk.call(canvas, 'itemconfigure', 'startup_water', '-image', f'startup_water_{frame}')
            root.update_idletasks()
            rendered = capture().convertToFormat(QImage.Format.Format_RGBA8888)
            native = Image.frombytes('RGBA', (rendered.width(), rendered.height()),
                                     rendered.constBits().asstring(rendered.sizeInBytes()))
            background = Image.new('RGBA', native.size, '#0b1220')
            frames.append(Image.alpha_composite(background, native).convert('RGB'))
        palette = frames[0].quantize(colors=256)
        frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
        frames[0].save(output / 'startup_water_preview.gif', save_all=True, append_images=frames[1:],
                       duration=40, loop=0, disposal=2)
        print('PASS: flowing water, large percentage, byte progress, monotonic milestones, and hidden filenames')
        print(output / 'startup_window.png')
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
