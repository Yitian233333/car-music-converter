# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys
import imageio_ffmpeg
import PySide6

project = Path(SPECPATH)
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
qt_dir = Path(PySide6.__file__).parent
# Other development tools may put incompatible ICU DLLs on PATH. Qt uses
# Windows' own ICU; resolve dependencies in a small, deterministic search path.
windows = Path(os.environ.get('WINDIR', 'C:/Windows'))
os.environ['PATH'] = os.pathsep.join(map(str, [qt_dir, Path(sys.base_prefix), Path(sys.prefix) / 'Scripts', windows / 'System32', windows]))
a = Analysis(
    [str(project / 'main.py')],
    pathex=[str(project)],
    binaries=[(ffmpeg, 'tools')],
    datas=[(str(project / 'car_music/kgm_tables.json'), 'car_music'), (str(project / 'assets'), 'assets')],
    hiddenimports=['Crypto.Cipher.AES'],
    excludes=['imageio_ffmpeg', 'tkinter', 'unittest', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtTest'],
    noarchive=False,
    optimize=1,
)
# Give the bundled binary a stable name independent of the wheel's filename.
a.binaries = [('tools/ffmpeg.exe' if name.startswith('tools/') or name.startswith('tools\\') else name, src, kind) for name, src, kind in a.binaries]
# Do not redistribute unrelated ICU builds under Windows system-DLL names.
a.binaries = [(name, src, kind) for name, src, kind in a.binaries if Path(name).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt.dll'}]
# Qt ships a newer, matching MSVC runtime than Python. The bootloader loads
# root-level vcruntime before Qt: prefer the Qt copy consistently in that location.
runtimes = {p.name.lower(): p for p in qt_dir.glob('*.dll') if p.name.lower().startswith(('vcruntime140', 'msvcp140', 'concrt140'))}
a.binaries = [(name, src, kind) for name, src, kind in a.binaries if Path(name).name.lower() not in runtimes]
a.binaries += [(p.name, str(p), 'BINARY') for p in runtimes.values()]
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CarMusic', console=False,
          icon=str(project / 'assets/app.ico'), debug=False, bootloader_ignore_signals=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CarMusicCLI', console=True,
          icon=str(project / 'assets/app.ico'), debug=False, bootloader_ignore_signals=False, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='随行音乐转换器')
