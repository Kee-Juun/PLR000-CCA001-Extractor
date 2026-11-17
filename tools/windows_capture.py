"""Capture a bounded native test window, including DWM acrylic composition."""
import ctypes
from ctypes import wintypes


def window_bounds(hwnd):
    user = ctypes.WinDLL('user32')
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        rect = wintypes.RECT()
        if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
        return rect.left, rect.top, rect.right, rect.bottom
    finally:
        user.SetThreadDpiAwarenessContext(previous)


def capture_region(bounds):
    from PIL import Image

    class Header(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                    ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                    ('bytes', wintypes.DWORD), ('xppm', wintypes.LONG), ('yppm', wintypes.LONG),
                    ('used', wintypes.DWORD), ('important', wintypes.DWORD)]

    user, gdi = ctypes.WinDLL('user32'), ctypes.WinDLL('gdi32')
    user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], ctypes.c_void_p
    user.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [ctypes.c_void_p], ctypes.c_void_p
    gdi.CreateCompatibleBitmap.argtypes, gdi.CreateCompatibleBitmap.restype = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int], ctypes.c_void_p
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p
    gdi.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(Header), wintypes.UINT]
    gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi.DeleteDC.argtypes = [ctypes.c_void_p]
    previous_dpi = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    left, top, right, bottom = bounds
    width, height = right-left, bottom-top
    screen = user.GetDC(None)
    memory = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        assert gdi.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x40CC0020)
        gdi.SelectObject(memory, previous)
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width*height*4)
        assert gdi.GetDIBits(screen, bitmap, 0, height, pixels, ctypes.byref(header), 0) == height
        return Image.frombytes('RGB', (width, height), pixels.raw, 'raw', 'BGRX')
    finally:
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(None, screen)
        user.SetThreadDpiAwarenessContext(previous_dpi)
