#!/usr/bin/env python3
"""Fecs Store - punto de entrada."""
import os
import sys

HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main() -> int:
    if "--version" in sys.argv:
        from fecs import __version__
        print(__version__)
        return 0
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("Falta PyQt6. Instálalo con el gestor de tu distro:\n"
              "  Arch/Manjaro:   sudo pacman -S python-pyqt6\n"
              "  Debian/Ubuntu:  sudo apt install python3-pyqt6\n"
              "  Fedora:         sudo dnf install python3-pyqt6\n"
              "  openSUSE:       sudo zypper install python3-qt6\n"
              "  o con pip:      pip install --user PyQt6")
        return 1
    from fecs.ui import StoreWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Fecs Store")
    app.setDesktopFileName("fecs-store")
    qss = os.path.join(HERE, "styles.qss")
    try:
        with open(qss, "r", encoding="utf-8") as fh:
            app.setStyleSheet(fh.read())
    except OSError:
        pass
    win = StoreWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
