"""Tiny startup status bridge; importing this must not load Qt or Office modules."""
import sys

try:
    import pyi_splash
except ImportError:
    pyi_splash = None

_closed = False


def startup_is_minimized() -> bool:
    """Carry the user's taskbar choice through the loader-to-application handoff."""
    if _closed or pyi_splash is None:
        return False
    import os
    import socket
    try:
        port = int(os.environ.get('PLR_STARTUP_CONTROL_PORT', '0'))
        token = os.environ.get('PLR_STARTUP_CONTROL_TOKEN', '')
        if not 0 < port < 65536 or not token:
            return False
        with socket.create_connection(('127.0.0.1', port), timeout=.4) as channel:
            channel.sendall(f'QUERY\t{token}\n'.encode('utf-8'))
            response = channel.recv(256).decode('utf-8').split('\t')
            return len(response) >= 2 and response[:2] == ['STATE', '1']
    except (OSError, ValueError, UnicodeError):
        return False


def report_startup(message: str, progress: int | None = None) -> None:
    """Advance a real startup milestone without interrupting normal source runs."""
    if _closed or pyi_splash is None:
        return
    try:
        if pyi_splash.is_alive():
            prefix = '' if progress is None else f'{max(0, min(100, int(progress)))}|'
            pyi_splash.update_text('@stage:' + prefix + message)
    except (OSError, RuntimeError, ConnectionError):
        pass


def close_startup() -> None:
    """Close once the existing intro has painted its first frame."""
    global _closed
    if _closed:
        return
    report_startup('Ready', 100)
    _closed = True
    if pyi_splash is not None:
        try:
            pyi_splash.close()
        except (OSError, RuntimeError, ConnectionError):
            pass


def show_startup_error(error: Exception) -> None:
    """Avoid leaving the splash hanging if imports or window creation fail."""
    close_startup()
    import tempfile
    import traceback
    from pathlib import Path
    details = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    log = Path(tempfile.gettempdir()) / 'PLR000-CCA001 Extractor' / 'startup-error.log'
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(details, encoding='utf-8')
        message = f'The application could not start.\n\n{error}\n\nDetails: {log}'
    except OSError:
        message = f'The application could not start.\n\n{error}'
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'PLR000-CCA001 Extractor', 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)
