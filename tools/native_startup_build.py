"""Compile the small early-startup window with Windows' .NET Framework compiler."""
import os
from pathlib import Path
import subprocess


def build_native_startup(root: Path, work_directory: Path, artwork: Path, frames: list[Path]) -> Path:
    windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    compiler = windows / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise RuntimeError('The startup window build requires the Windows .NET Framework 4 compiler (csc.exe).')
    output = work_directory / 'plr_startup.exe'
    arguments = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                 '/out:' + str(output), '/reference:System.Drawing.dll',
                 '/reference:System.Windows.Forms.dll', '/reference:Accessibility.dll',
                 '/win32icon:' + str(root / 'assets/app_icon.ico'),
                 '/resource:' + str(artwork) + ',startup_artwork']
    arguments.extend('/resource:' + str(frame) + f',water_{index:03d}' for index, frame in enumerate(frames))
    arguments.append(str(root / 'tools/native_startup/StartupWindow.cs'))
    subprocess.run(arguments, check=True, capture_output=True, text=True)
    return output
