"""Keep unrelated command-line tools out of Windows dependency discovery."""
import os
from pathlib import Path
import sys


def isolate_windows_dll_search_path() -> None:
    """Use Python/Windows DLLs; package hooks add Qt and numpy's own directories.

    An inherited PATH can contain another application's ICU, OpenSSL, or CRT.
    Those DLLs may share a filename while exporting incompatible functions.
    Change only this build process's environment, never the user's system PATH.
    """
    if sys.platform != 'win32':
        return
    windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    candidates = [Path(sys.executable).parent, Path(sys.prefix), Path(sys.base_prefix),
                  Path(sys.base_prefix) / 'DLLs', Path(sys.prefix) / 'Scripts',
                  windows / 'System32', windows, windows / 'System32' / 'Wbem']
    paths = {}
    for path in candidates:
        if path.is_dir():
            paths.setdefault(str(path).casefold(), str(path))
    os.environ['PATH'] = os.pathsep.join(paths.values())
