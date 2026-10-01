# Reproducible Windows folder distribution; both launchers share dependencies.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

data = collect_data_files('film_lab')
for package in ('rawpy', 'numpy', 'Pillow', 'tifffile', 'ExifRead', 'PySide6', 'shiboken6'):
    data += copy_metadata(package)
a = Analysis(['run.py'], pathex=['.'],
             binaries=collect_dynamic_libs('rawpy'), datas=data,
             hiddenimports=['PIL.ImageCms', 'exifread'],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtQuick', 'PySide6.QtQml', 'pytest'],
             noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FilmLab',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='film-lab-cli',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='FilmLab')
