"""Actualizaciones automáticas desde GitHub.

Cómo se detecta una versión nueva (lo que pase primero):
  1. La última *Release* de GitHub (etiqueta tipo v2.1.0).
  2. Si no hay Releases (o el código fuente va más adelante): el número ``__version__`` de
     ``fecs/__init__.py`` en la rama principal del repositorio. Basta con subir el código con la versión cambiada.

Cómo se instala, según cómo se ejecute la tienda:
  * AppImage ........ descarga el AppImage nuevo (verifica sha256) y reemplaza su propio archivo.
  * Código fuente ... descarga el .zip del repositorio y reemplaza los archivos de la tienda.
  * Copia con .git .. hace ``git pull --ff-only`` (no pisa tus cambios locales).
En todos los casos la tienda se reinicia sola al terminar.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import __version__

REPO = "FecsStudios/fecs-store"  # el flujo de GitHub lo reemplaza solo al compilar (o se detecta del remoto de git / ajustes)
UA = {"User-Agent": f"FecsStore/{__version__}", "Accept": "application/vnd.github+json"}
APP_DIR = Path(__file__).resolve().parent.parent
_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fecs-store" / "settings.json"
_SKIP = {".git", ".github", ".gitignore", "__pycache__"}


# --------------------------------------------------------------------------- #
#  Repositorio y versiones
# --------------------------------------------------------------------------- #
def _ver(text: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4]) or (0,)


def _repo_from_git() -> str:
    try:
        out = subprocess.run(["git", "-C", str(APP_DIR), "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=5, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", out)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def resolve_repo() -> str:
    """'usuario/repositorio' de GitHub, o '' si no se puede saber."""
    env = os.environ.get("FECS_REPO", "").strip()
    if re.fullmatch(r"[\w.-]+/[\w.-]+", env):
        return env
    try:
        cfg = json.loads(_CONFIG.read_text()).get("repo", "")
        if re.fullmatch(r"[\w.-]+/[\w.-]+", cfg or ""):
            return cfg
    except (OSError, ValueError):
        pass
    if not REPO.startswith("OWNER"):
        return REPO
    return _repo_from_git()


def appimage_path() -> Optional[Path]:
    p = os.environ.get("APPIMAGE")
    return Path(p) if p and Path(p).is_file() else None


def mode() -> str:
    """appimage | git | source | manual"""
    ai = appimage_path()
    if ai:
        return "appimage" if os.access(ai.parent, os.W_OK) else "manual"
    if getattr(sys, "frozen", False):
        return "manual"
    if (APP_DIR / ".git").exists() and shutil.which("git"):
        return "git"
    if (APP_DIR / "main.py").is_file() and os.access(APP_DIR, os.W_OK) and os.access(APP_DIR / "fecs", os.W_OK):
        return "source"
    return "manual"


def can_self_update() -> bool:
    return mode() != "manual"


def _get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _latest_release(repo: str, timeout: int) -> Optional[dict]:
    try:
        rel = json.loads(_get(f"https://api.github.com/repos/{repo}/releases/latest", timeout))
    except Exception:  # noqa: BLE001
        return None
    tag = rel.get("tag_name", "")
    if not tag:
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
        "zip_url": rel.get("zipball_url", ""),
    }


def _branch_version(repo: str, timeout: int) -> Optional[dict]:
    for rel_path in ("fecs/__init__.py", "Fecs_Store/fecs/__init__.py"):
        try:
            text = _get(f"https://raw.githubusercontent.com/{repo}/HEAD/{rel_path}", timeout).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", text)
        if m:
            return {"version": m.group(1), "notes": "", "page": f"https://github.com/{repo}",
                    "url": "", "sha_url": "", "zip_url": f"https://github.com/{repo}/archive/HEAD.zip"}
    return None


def check(timeout: int = 10) -> Optional[dict]:
    """Datos de la versión nueva o None si ya estás al día / sin conexión / sin repositorio configurado."""
    repo = resolve_repo()
    if not repo:
        return None
    m = mode()
    cands = []
    rel = _latest_release(repo, timeout)
    if rel:
        cands.append(rel)
    if m != "appimage":      # el AppImage solo se actualiza desde una Release (ahí está el archivo compilado)
        br = _branch_version(repo, timeout)
        if br:
            cands.append(br)
    cands = [c for c in cands if _ver(c["version"]) > _ver(__version__)]
    if not cands:
        return None
    best = max(cands, key=lambda c: (_ver(c["version"]), bool(c.get("notes"))))
    best["mode"] = m
    best["repo"] = repo
    if m == "appimage" and not best["url"]:
        best["mode"] = "manual"
    elif m == "source" and not best["zip_url"]:
        best["mode"] = "manual"
    return best


# --------------------------------------------------------------------------- #
#  Descarga e instalación
# --------------------------------------------------------------------------- #
def _download(url: str, dest: Path, progress: Callable[[int], None], scale: tuple = (0, 100)) -> str:
    """Descarga a ``dest`` y devuelve el sha256. progress recibe 0-100 (o -1 si el tamaño es desconocido)."""
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as out:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        if not total:
            progress(-1)
        while chunk := r.read(256 * 1024):
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total:
                progress(scale[0] + int(done * (scale[1] - scale[0]) / total))
    return h.hexdigest()


def _apply_appimage(info: dict, progress: Callable[[int], None]) -> None:
    target = appimage_path()
    if not target or not info.get("url"):
        raise RuntimeError("Esta instalación no se puede actualizar sola.")
    tmp = target.with_name(target.name + ".new")
    try:
        digest = _download(info["url"], tmp, progress)
        if info.get("sha_url"):
            want = _get(info["sha_url"], 15).decode().split()[0].lower()
            if want != digest:
                raise RuntimeError("La descarga está dañada (no coincide el sha256).")
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)  # atómico; el proceso actual sigue funcionando
    finally:
        tmp.unlink(missing_ok=True)


def _project_root(base: Path) -> Optional[Path]:
    level1 = [p for p in base.iterdir() if p.is_dir()]
    level2 = [d for p in level1 for d in p.iterdir() if d.is_dir()]
    for cand in [base, *level1, *level2]:
        if (cand / "main.py").is_file() and (cand / "fecs" / "__init__.py").is_file():
            return cand
    return None


def _apply_source(info: dict, progress: Callable[[int], None]) -> None:
    work = Path(tempfile.mkdtemp(prefix="fecs-update-"))
    backup = work / "backup"
    try:
        zpath = work / "update.zip"
        _download(info["zip_url"], zpath, progress, (0, 85))
        extract = work / "new"
        with zipfile.ZipFile(zpath) as zf:
            for name in zf.namelist():      # protección contra rutas maliciosas dentro del zip
                if name.startswith("/") or ".." in Path(name).parts:
                    raise RuntimeError("El archivo de actualización no es seguro.")
            zf.extractall(extract)
        root = _project_root(extract)
        if not root:
            raise RuntimeError("El archivo descargado no contiene la tienda (falta main.py o fecs/).")
        progress(90)
        backup.mkdir()
        touched = []
        try:
            for item in root.iterdir():
                if item.name in _SKIP:
                    continue
                dest = APP_DIR / item.name
                if dest.exists():
                    (shutil.copytree if dest.is_dir() else shutil.copy2)(dest, backup / item.name)
                touched.append(item.name)
                if item.is_dir():
                    if item.name == "fecs" and dest.exists():
                        shutil.rmtree(dest)      # sin restos de archivos viejos
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        except Exception:
            for name in touched:                 # algo falló: volver a como estaba
                src = backup / name
                dest = APP_DIR / name
                try:
                    if src.exists():
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        if src.is_dir():
                            shutil.copytree(src, dest)
                        else:
                            shutil.copy2(src, dest)
                except OSError:
                    pass
            raise
        progress(100)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _apply_git(progress: Callable[[int], None]) -> None:
    progress(-1)
    p = subprocess.run(["git", "-C", str(APP_DIR), "pull", "--ff-only"], capture_output=True, text=True,
                       timeout=120, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError("git pull falló (¿tienes cambios locales?):\n" + (p.stderr or p.stdout).strip()[-400:])
    progress(100)


def apply(info: dict, progress: Callable[[int], None]) -> None:
    """Instala la actualización según el modo. Lanza excepción con el motivo si falla."""
    m = info.get("mode") or mode()
    if m == "appimage":
        _apply_appimage(info, progress)
    elif m == "source":
        _apply_source(info, progress)
    elif m == "git":
        _apply_git(progress)
    else:
        raise RuntimeError("Esta instalación no se puede actualizar sola.")


def restart() -> None:
    """Abre la tienda de nuevo (ya actualizada). El llamador debe cerrar la instancia actual."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    ai = appimage_path()
    argv = [str(ai)] if ai else [sys.executable, str(APP_DIR / "main.py")]
    subprocess.Popen(argv, env=env, start_new_session=True, cwd=str(APP_DIR),
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
