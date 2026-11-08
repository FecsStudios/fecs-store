"""Actualizaciones automáticas desde GitHub Releases.

Si la tienda corre como AppImage, descarga la versión nueva y reemplaza su propio archivo.
Si corre desde el código fuente, solo avisa y abre la página de la versión.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import __version__

REPO = "OWNER/REPO"  # el flujo de GitHub lo reemplaza solo al compilar
API = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = {"User-Agent": f"FecsStore/{__version__}", "Accept": "application/vnd.github+json"}


def _ver(text: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4])


def appimage_path() -> Optional[Path]:
    p = os.environ.get("APPIMAGE")
    return Path(p) if p and Path(p).is_file() else None


def check(timeout: int = 10) -> Optional[dict]:
    """Devuelve datos de la versión nueva, o None si ya estás al día / no hay conexión."""
    if "OWNER" in REPO:
        return None
    try:
        req = urllib.request.Request(API, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rel = json.load(r)
    except Exception:  # noqa: BLE001
        return None
    tag = rel.get("tag_name", "")
    if not tag or _ver(tag) <= _ver(__version__):
        return None
    assets = rel.get("assets", [])
    img = next((a for a in assets if a["name"].endswith(".AppImage")), None)
    sha = next((a for a in assets if img and a["name"] == img["name"] + ".sha256"), None)
    return {
        "version": tag.lstrip("vV"),
        "notes": (rel.get("body") or "").strip(),
        "page": rel.get("html_url", ""),
        "url": img["browser_download_url"] if img else "",
        "sha_url": sha["browser_download_url"] if sha else "",
    }


def can_self_update() -> bool:
    p = appimage_path()
    return bool(p and os.access(p.parent, os.W_OK))


def download(info: dict, progress: Callable[[int], None]) -> Path:
    """Descarga, verifica (sha256) y reemplaza el AppImage actual. Lanza excepción si falla."""
    target = appimage_path()
    if not target or not info.get("url"):
        raise RuntimeError("Esta instalación no se puede actualizar sola.")
    tmp = target.with_name(target.name + ".new")
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(info["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(256 * 1024):
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    progress(int(done * 100 / total))
        if info.get("sha_url"):
            req = urllib.request.Request(info["sha_url"], headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                want = r.read().decode().split()[0].lower()
            if want != h.hexdigest():
                raise RuntimeError("La descarga está dañada (no coincide el sha256).")
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)  # atómico; el proceso actual sigue funcionando
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return target


def restart(path: Path) -> None:
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    subprocess.Popen([str(path)], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_menu_entry() -> None:
    """Si se abre el AppImage suelto (sin instalador), crea el acceso directo en el menú una sola vez."""
    path = appimage_path()
    if not path:
        return
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    desktop = data / "applications" / "fecs-store.desktop"
    icon = data / "icons" / "hicolor" / "256x256" / "apps" / "fecs-store.png"
    appdir = os.environ.get("APPDIR", "")
    src_icon = Path(appdir) / "fecs-store.png"
    if desktop.exists():  # ya lo creó el instalador (o una ejecución previa): no lo pisamos
        return
    try:
        if src_icon.is_file():
            icon.parent.mkdir(parents=True, exist_ok=True)
            icon.write_bytes(src_icon.read_bytes())
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=Fecs Store\n"
            "Comment=Tienda de aplicaciones: Flatpak, nativas y web\n"
            f"Exec=\"{path}\"\nIcon=fecs-store\nTerminal=false\n"
            "Categories=System;PackageManager;\nStartupWMClass=fecs-store\n",
            encoding="utf-8")
    except OSError:
        pass
