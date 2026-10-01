"""Copy installed distribution licenses and record exact runtime versions."""
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGES = ['numpy', 'Pillow', 'PySide6', 'PySide6_Addons', 'PySide6_Essentials',
            'shiboken6', 'rawpy', 'tifffile', 'ExifRead', 'pyinstaller']


def main():
    destination = ROOT / 'dist' / 'FilmLab' / 'licenses'
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, destination / 'Python-LICENSE.txt')
    bundled_notices = ROOT / 'licenses'
    if bundled_notices.is_dir():
        shutil.copytree(bundled_notices, destination / 'upstream', dirs_exist_ok=True)
    for package in PACKAGES:
        dist = metadata.distribution(package)
        files = []
        for entry in dist.files or []:
            name = str(entry).replace('\\', '/')
            if any(word in name.lower() for word in ('license', 'licence', 'copying', 'copyright', 'notice')):
                source = Path(dist.locate_file(entry))
                if source.is_file() and '..' not in entry.parts:
                    target = destination / package / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    files.append(str(target.relative_to(destination)))
        records.append({'name': package, 'version': dist.version,
                        'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                        'homepage': dist.metadata.get('Home-page'), 'files': files})
    import rawpy
    from film_lab.models import atomic_json
    atomic_json(destination / 'components.json', {'components': records,
        'python': sys.version, 'libraw': list(rawpy.libraw_version),
        'notes': 'Qt/PySide6 and LibRaw shared libraries remain replaceable in _internal. See individual licenses.'})
    shutil.copyfile(ROOT / 'film_lab/data/profiles/LICENSE.txt', destination / 'profile-LICENSE.txt')
    if (ROOT / 'THIRD_PARTY_NOTICES.md').exists():
        shutil.copyfile(ROOT / 'THIRD_PARTY_NOTICES.md', destination / 'THIRD_PARTY_NOTICES.md')
    print(destination)


if __name__ == '__main__':
    main()
