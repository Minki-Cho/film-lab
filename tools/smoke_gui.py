"""Render a real Qt window and optionally load a photo through the spawned worker."""
import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source')
    parser.add_argument('--output', default='test-output/gui.png')
    args = parser.parse_args()
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['FILM_LAB_DATA'] = str(ROOT / 'test-output' / 'gui-state')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from film_lab.gui import FilmLabWindow
    app = QApplication([])
    app.setApplicationName('FilmLabSmoke')
    window = FilmLabWindow(start_worker=bool(args.source))
    window.resize(1480, 960)
    window.show()
    deadline = time.monotonic() + 180
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.source:
        window.add_paths([str(Path(args.source).resolve())])
    def check():
        done = not args.source or (window.session.photos and not window.preview.item.after.isNull())
        if done:
            window.grab().save(str(output))
            print(str(output), flush=True)
            window.close()
            if not args.source:
                app.quit()
        elif time.monotonic() > deadline:
            print('GUI render timeout', file=sys.stderr, flush=True)
            window.close()
    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(500)
    return app.exec()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
