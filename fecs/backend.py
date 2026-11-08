"""Lógica de Fecs Store sin dependencias de Qt (fácil de probar).

* Estado de instalación en bloque (una sola consulta por gestor, no una por app).
* Planes de instalación / desinstalación para Flatpak, pacman, AUR, apt, dnf, zypper...
* Catálogo completo de Flathub vía `flatpak remote-ls`, con caché en disco.
* Iconos descargados en segundo plano y cacheados.
"""
from __future__ import annotations

import json
import os
import platform
import pty
import fcntl
import struct
import termios
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import quote, urlparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .catalog import App, Source
from .distro import Distro, detect_distro

HOME = Path.home()
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "fecs-store"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "fecs-store"
DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))
APPS_DIR = DATA / "applications"
WEB_PREFIX = "fecs-web-"
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
FLATHUB_TTL = 24 * 3600


# --------------------------------------------------------------------------- #
#  Utilidades
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    label: str = ""
    argv: Optional[list] = None
    fn: Optional[Callable[[], None]] = None


def _run(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=_env())
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _env() -> dict:
    env = dict(os.environ)
    # Empaquetada (AppImage/PyInstaller) las libs propias no deben filtrarse a flatpak, pacman, etc.
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    env["LC_ALL"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return env


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def _open_pty():
    """Pseudo-terminal para que pacman/flatpak/apt muestren su progreso real de descarga."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
    return master, slave


class Settings:
    def __init__(self):
        self.path = CONFIG / "settings.json"
        self.data = {"prefer": "auto"}
        try:
            self.data.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        try:
            CONFIG.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data))
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Iconos
# --------------------------------------------------------------------------- #
class IconStore:
    """Descarga iconos en hilos y los guarda en ~/.cache/fecs-store/icons."""

    def __init__(self):
        self.dir = CACHE / "icons"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.pending: set[str] = set()
        self.failed: set[str] = set()
        self.lock = threading.Lock()
        self.on_ready: Callable[[str], None] = lambda key: None
        arch = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(
            platform.machine(), "x86_64")
        self.arch = arch
        self.local_dirs = []
        for base in (DATA / "flatpak", Path("/var/lib/flatpak")):
            for size in ("128x128", "64x64"):
                self.local_dirs.append(base / "appstream" / "flathub" / arch / "active" / "icons" / size)

    def local(self, key: str) -> Optional[str]:
        p = self.dir / f"{key}.png"
        if p.exists() and p.stat().st_size > 0:
            return str(p)
        for d in self.local_dirs:
            q = d / f"{key}.png"
            if q.exists():
                return str(q)
        return None

    def request(self, key: str, urls: list[str]):
        if not urls:
            return
        with self.lock:
            if key in self.pending or key in self.failed:
                return
            self.pending.add(key)
        self.pool.submit(self._fetch, key, urls)

    def _fetch(self, key: str, urls: list[str]):
        ok = False
        for url in urls:
            if url.startswith("flathub:"):
                url = f"https://dl.flathub.org/repo/appstream/{self.arch}/icons/128x128/{url[8:]}.png"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "FecsStore/2.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = r.read()
                if len(data) > 100:
                    tmp = self.dir / f"{key}.png.part"
                    tmp.write_bytes(data)
                    tmp.replace(self.dir / f"{key}.png")
                    ok = True
                    break
            except Exception:
                continue
        with self.lock:
            self.pending.discard(key)
            if not ok:
                self.failed.add(key)
        if ok:
            self.on_ready(key)


# --------------------------------------------------------------------------- #
#  Backend principal
# --------------------------------------------------------------------------- #
class Backend:
    def __init__(self, distro: Optional[Distro] = None):
        self.distro = distro or detect_distro()
        self.settings = Settings()
        self.has_flatpak = shutil.which("flatpak") is not None
        self.has_pkexec = shutil.which("pkexec") is not None
        self.installed_flatpak: set[str] = set()
        self.installed_native: set[str] = set()
        self.installed_web: set[str] = set()
        self.icons = IconStore()
        self._preview_cache: dict[str, tuple[str, list[bytes]]] = {}
        self._preview_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        CACHE.mkdir(parents=True, exist_ok=True)

    def load_preview(self, app: App) -> tuple[str, list[bytes]]:
        """Obtiene el creador y capturas de Flathub para la ficha de una app."""
        with self._preview_lock:
            cached = self._preview_cache.get(app.fp) if app.fp else None
        if cached is not None:
            creator, images = cached
            return creator or app.creator, list(images)

        creator = app.creator
        image_urls = list(app.screenshots)
        if app.fp:
            url = f"https://flathub.org/api/v2/appstream/{quote(app.fp, safe='')}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "FecsStore/2.0"})
                with urllib.request.urlopen(req, timeout=8) as response:
                    metadata = json.loads(response.read(2_000_001))
                if isinstance(metadata, dict):
                    creator = creator or str(metadata.get("developer_name", "")).strip()
                    screenshots = metadata.get("screenshots", [])
                    if isinstance(screenshots, list):
                        for screenshot in screenshots:
                            if isinstance(screenshot, dict):
                                image_url = screenshot.get("url") or screenshot.get("source", "")
                                if not image_url:
                                    sizes = screenshot.get("sizes", [])
                                    candidates = [
                                        size for size in sizes
                                        if isinstance(size, dict)
                                        and isinstance(size.get("src"), str)
                                        and str(size.get("width", "")).isdigit()
                                    ] if isinstance(sizes, list) else []
                                    if candidates:
                                        image_size = min(
                                            candidates,
                                            key=lambda size: (
                                                int(size.get("width", 10000)) > 800,
                                                abs(int(size.get("width", 10000)) - 752),
                                            ),
                                        )
                                        image_url = image_size["src"]
                                if isinstance(image_url, str) and image_url not in image_urls:
                                    image_urls.append(image_url)
            except (OSError, ValueError, TimeoutError):
                pass

        images: list[bytes] = []
        for image_url in image_urls:
            if len(images) >= 4:
                break
            if not isinstance(image_url, str) or urlparse(image_url).scheme != "https":
                continue
            try:
                req = urllib.request.Request(image_url, headers={"User-Agent": "FecsStore/2.0"})
                with urllib.request.urlopen(req, timeout=8) as response:
                    data = response.read(4_000_001)
                if 100 < len(data) <= 4_000_000:
                    images.append(data)
            except (OSError, TimeoutError):
                continue

        if app.fp:
            with self._preview_lock:
                self._preview_cache[app.fp] = (creator, list(images))
        return creator, images

    # ---------------- estado instalado (consulta en bloque) ----------------
    def refresh_installed(self):
        fp: set[str] = set()
        if self.has_flatpak:
            rc, out = _run(["flatpak", "list", "--app", "--columns=application"])
            if rc == 0:
                fp = {l.strip() for l in out.splitlines() if l.strip()}
        self.installed_native = self._native_installed()
        self.installed_flatpak = fp
        self.refresh_web()

    def refresh_web(self):
        web = set()
        try:
            for p in APPS_DIR.glob(f"{WEB_PREFIX}*.desktop"):
                web.add(p.name[len(WEB_PREFIX):-len(".desktop")])
        except OSError:
            pass
        self.installed_web = web

    def _native_installed(self) -> set[str]:
        fam = self.distro.family
        names: set[str] = set()
        if fam == "arch":
            rc, out = _run(["pacman", "-Qq"], 30)
            names = {l.strip() for l in out.splitlines()} if rc == 0 else set()
        elif fam == "debian":
            rc, out = _run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n"], 30)
            if rc == 0:
                for l in out.splitlines():
                    st, _, pkg = l.partition("\t")
                    if st.strip().startswith("ii"):
                        names.add(pkg.strip())
        elif fam in ("fedora", "suse"):
            rc, out = _run(["rpm", "-qa", "--qf", "%{NAME}\n"], 30)
            names = {l.strip() for l in out.splitlines()} if rc == 0 else set()
        elif fam == "alpine":
            rc, out = _run(["apk", "info"], 30)
            names = {l.strip() for l in out.splitlines()} if rc == 0 else set()
        elif fam == "void":
            rc, out = _run(["xbps-query", "-l"], 30)
            for l in out.splitlines():
                parts = l.split()
                if len(parts) > 1:
                    names.add(parts[1].rsplit("-", 1)[0])
        return names

    def source_installed(self, app: App, src: Source) -> bool:
        if src.kind == "flatpak":
            return src.pkg in self.installed_flatpak
        if src.kind in ("native", "aur"):
            return src.pkg in self.installed_native
        if src.kind == "web":
            return app.key in self.installed_web
        return False

    def app_installed(self, app: App) -> bool:
        return any(self.source_installed(app, s) for s in app.sources(self.distro)
                   if s.kind != "download")

    # ---------------- orden / recomendación de fuentes ----------------
    def ordered_sources(self, app: App) -> list[Source]:
        srcs = app.sources(self.distro)
        prefer = self.settings.get("prefer", "auto")
        if prefer == "flatpak" or (prefer == "auto" and self.distro.immutable):
            order = ["flatpak", "native", "aur", "web", "download"]
        else:
            order = ["native", "flatpak", "aur", "web", "download"]
            if prefer == "native":
                order = ["native", "aur", "flatpak", "web", "download"]
        return sorted(srcs, key=lambda s: order.index(s.kind))

    # ---------------- Flathub completo ----------------
    def load_flathub(self, force: bool = False) -> dict:
        cache = CACHE / "flathub.json"
        if not force:
            try:
                if time.time() - cache.stat().st_mtime < FLATHUB_TTL:
                    data = json.loads(cache.read_text())
                    if len(data) > 200:
                        return {k: tuple(v) for k, v in data.items()}
            except (OSError, ValueError):
                pass
        if not self.has_flatpak:
            return self._stale_cache(cache)
        cols = "--columns=application,name,description"
        rc, out = _run(["flatpak", "remote-ls", "--app", "--cached", cols, "flathub"], 90)
        if rc != 0 or out.count("\n") < 200:
            _run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO], 60)
            rc, out = _run(["flatpak", "remote-ls", "--app", cols, "flathub"], 180)
        data: dict = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 1 and "." in parts[0]:
                data[parts[0].strip()] = (parts[1].strip() if len(parts) > 1 else "",
                                          parts[2].strip() if len(parts) > 2 else "")
        if len(data) > 200:
            try:
                cache.write_text(json.dumps(data))
            except OSError:
                pass
            return data
        return self._stale_cache(cache)

    @staticmethod
    def _stale_cache(cache: Path) -> dict:
        try:
            data = json.loads(cache.read_text())
            return {k: tuple(v) for k, v in data.items()}
        except (OSError, ValueError):
            return {}

    # ---------------- planes de comandos ----------------
    def _root(self, cmds: list[list[str]]) -> list[str]:
        """Comando que ejecuta una o varias órdenes como root mediante pkexec."""
        script = " && ".join(shlex.join(c) for c in cmds)
        return ["pkexec", "sh", "-c", script]

    def _native_cmds(self, action: str, pkg: str) -> list[list[str]]:
        fam = self.distro.family
        if fam == "arch":
            return [["pacman", "-S", "--noconfirm", "--needed", pkg]] if action == "install" \
                else [["pacman", "-Rns", "--noconfirm", pkg]]
        if fam == "debian":
            if action == "install":
                return [["apt-get", "update"], ["apt-get", "install", "-y", pkg]]
            return [["apt-get", "remove", "-y", pkg]]
        if fam == "fedora":
            return [["dnf", "install", "-y", pkg]] if action == "install" else [["dnf", "remove", "-y", pkg]]
        if fam == "suse":
            base = ["zypper", "--non-interactive"]
            return [base + ["install", pkg]] if action == "install" else [base + ["remove", pkg]]
        if fam == "alpine":
            return [["apk", "add", pkg]] if action == "install" else [["apk", "del", pkg]]
        if fam == "void":
            return [["xbps-install", "-y", pkg]] if action == "install" else [["xbps-remove", "-y", pkg]]
        return []

    def _ensure_flatpak_steps(self) -> list[Step]:
        steps: list[Step] = []
        if not self.has_flatpak:
            cmds = self._native_cmds("install", "flatpak")
            if cmds:
                steps.append(Step("Instalando Flatpak", self._root(cmds)))
        steps.append(Step("Activando Flathub", ["flatpak", "remote-add", "--user", "--if-not-exists",
                                                "flathub", FLATHUB_REPO]))
        return steps

    def plan(self, app: App, src: Source, action: str) -> tuple[list[Step], str]:
        """Devuelve (pasos, error). ``action``: install | uninstall."""
        d = self.distro
        if src.kind == "flatpak":
            if action == "install":
                if not self.has_flatpak and (d.immutable or d.family == "unknown"):
                    return [], "Flatpak no está instalado en este sistema."
                steps = self._ensure_flatpak_steps()
                steps.append(Step(f"Instalando {app.name} (Flatpak)",
                                  ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", src.pkg]))
                return steps, ""
            return [Step(f"Desinstalando {app.name} (Flatpak)",
                         ["flatpak", "uninstall", "-y", "--noninteractive", src.pkg])], ""

        if src.kind in ("native", "aur"):
            if d.immutable:
                return [], "Este sistema es inmutable: usa la versión Flatpak."
            if not self.has_pkexec:
                return [], "Falta pkexec (polkit) para pedir permisos de administrador."
            if src.kind == "aur":
                if action == "install":
                    helper = d.aur_helper or ("yay" if shutil.which("yay") else ("paru" if shutil.which("paru") else ""))
                    if not helper:
                        return [], "Para instalar desde el AUR necesitas yay o paru."
                    argv = [helper, "-S", "--noconfirm", "--needed", "--sudo", "pkexec"]
                    argv += ["--skipreview"] if helper == "paru" else \
                        ["--answerdiff", "None", "--answerclean", "None", "--answeredit", "None"]
                    argv.append(src.pkg)
                    return [Step(f"Compilando e instalando {app.name} (AUR)", argv)], ""
                return [Step(f"Desinstalando {app.name}", self._root(self._native_cmds("uninstall", src.pkg)))], ""
            cmds = self._native_cmds(action, src.pkg)
            if not cmds:
                return [], "Esta distribución no tiene gestor de paquetes compatible."
            verb = "Instalando" if action == "install" else "Desinstalando"
            return [Step(f"{verb} {app.name} ({d.pm_label})", self._root(cmds))], ""

        if src.kind == "web":
            if action == "install":
                return [Step(f"Creando acceso directo de {app.name}", fn=lambda: self.create_web_launcher(app))], ""
            return [Step(f"Quitando acceso directo de {app.name}", fn=lambda: self.remove_web_launcher(app))], ""
        return [], "Esta fuente no se puede instalar automáticamente."

    def update_all_steps(self) -> list[Step]:
        steps: list[Step] = []
        if self.has_flatpak:
            steps.append(Step("Actualizando Flatpak", ["flatpak", "update", "-y", "--noninteractive"]))
        if self.has_pkexec and not self.distro.immutable:
            fam = self.distro.family
            cmds = {
                "arch": [["pacman", "-Syu", "--noconfirm"]],
                "debian": [["apt-get", "update"], ["apt-get", "upgrade", "-y"]],
                "fedora": [["dnf", "upgrade", "-y"]],
                "suse": [["zypper", "--non-interactive", "update"]],
                "alpine": [["apk", "upgrade"]],
                "void": [["xbps-install", "-Syu"]],
            }.get(fam)
            if cmds:
                steps.append(Step(f"Actualizando sistema ({self.distro.pm_label})", self._root(cmds)))
        return steps

    def speedup_steps(self) -> list[Step]:
        """Arch/CachyOS/Manjaro…: más descargas en paralelo y, si existe, ordenar los espejos por velocidad."""
        if self.distro.family != "arch" or not self.has_pkexec:
            return []
        conf = "/etc/pacman.conf"
        script = (
            f"sed -i -E 's/^#?ParallelDownloads.*/ParallelDownloads = 10/' {conf} && "
            f"(grep -q '^ParallelDownloads' {conf} || sed -i '/^\\[options\\]/a ParallelDownloads = 10' {conf})"
        )
        steps = [Step("Activando 10 descargas en paralelo", ["pkexec", "sh", "-c", script])]
        if shutil.which("cachyos-rate-mirrors"):
            steps.append(Step("Buscando los espejos más rápidos (puede tardar un minuto)",
                              ["pkexec", "cachyos-rate-mirrors"]))
        elif shutil.which("rate-mirrors"):
            steps.append(Step("Buscando los espejos más rápidos (puede tardar un minuto)",
                              ["pkexec", "sh", "-c",
                               "rate-mirrors --save=/etc/pacman.d/mirrorlist arch --max-delay=21600"]))
        return steps

    def install_flatpak_steps(self) -> list[Step]:
        return self._ensure_flatpak_steps()

    # ---------------- web apps / lanzar ----------------
    def _browser_cmd(self) -> list[str]:
        for b in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
                  "brave-browser", "brave", "microsoft-edge-stable", "vivaldi-stable"):
            if shutil.which(b):
                return [b]
        return []

    def create_web_launcher(self, app: App):
        APPS_DIR.mkdir(parents=True, exist_ok=True)
        browser = self._browser_cmd()
        exec_line = (f"{browser[0]} --app={app.web}" if browser else f"xdg-open {app.web}")
        icon = self.icons.local(app.icon_key) or "applications-internet"
        content = (
            "[Desktop Entry]\nType=Application\n"
            f"Name={app.name}\nComment={app.desc}\nExec={exec_line}\nIcon={icon}\n"
            "Terminal=false\nCategories=Network;\nStartupWMClass=" + app.key + "\n"
        )
        (APPS_DIR / f"{WEB_PREFIX}{app.key}.desktop").write_text(content, encoding="utf-8")
        self.installed_web.add(app.key)

    def remove_web_launcher(self, app: App):
        try:
            (APPS_DIR / f"{WEB_PREFIX}{app.key}.desktop").unlink()
        except FileNotFoundError:
            pass
        self.installed_web.discard(app.key)

    def launch(self, app: App, src: Source) -> tuple[bool, str]:
        try:
            if src.kind == "flatpak":
                argv = ["flatpak", "run", src.pkg]
            elif src.kind in ("native", "aur"):
                exe = next((c for c in (src.pkg, app.key, app.name.lower().replace(" ", "-"))
                            if shutil.which(c)), "")
                if not exe:
                    return False, f"No encontré el ejecutable de {app.name}. Ábrelo desde el menú de aplicaciones."
                argv = [exe]
            elif src.kind == "web":
                b = self._browser_cmd()
                argv = [b[0], f"--app={src.url}"] if b else ["xdg-open", src.url]
            else:
                return False, "Nada que abrir."
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, env=_env())
            return True, f"Abriendo {app.name}…"
        except OSError as e:
            return False, str(e)

    # ---------------- ejecución de pasos ----------------
    def cancel(self):
        self._cancelled = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def run_steps(self, steps: list[Step], on_line: Callable[[str], None],
                  on_pct: Callable[[int], None]) -> tuple[bool, str]:
        self._cancelled = False
        pct_re = re.compile(r"(\d{1,3})\s?%")
        for step in steps:
            if self._cancelled:
                return False, "Cancelado"
            if step.label:
                on_line(f"▶ {step.label}")
            if step.fn:
                try:
                    step.fn()
                except Exception as e:  # noqa: BLE001
                    return False, f"{step.label}: {e}"
                continue
            on_pct(-1)
            try:
                master, slave = _open_pty()
            except OSError:
                master = slave = None
            env = _env()
            env["TERM"] = "xterm"
            env["NO_COLOR"] = "1"
            try:
                if slave is not None:
                    proc = subprocess.Popen(step.argv, stdin=subprocess.DEVNULL, stdout=slave,
                                            stderr=slave, env=env, close_fds=True)
                    os.close(slave)
                else:
                    proc = subprocess.Popen(step.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, env=env, bufsize=0)
            except FileNotFoundError:
                return False, f"No se encontró el comando «{step.argv[0]}»."
            except OSError as e:
                return False, str(e)
            self._proc = proc
            buf = b""
            fd = master if master is not None else proc.stdout.fileno()
            last_emit = 0.0
            last_pct_sent = -2
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]+", buf)
                buf = parts.pop()
                for part in parts:
                    text = _ANSI.sub("", part.decode("utf-8", "replace")).strip()
                    if not text:
                        continue
                    m = pct_re.search(text)
                    is_progress = bool(m) and ("/s" in text or "[" in text)
                    if m and 0 <= int(m.group(1)) <= 100:
                        on_pct(int(m.group(1)))
                    if is_progress:  # las barras se reescriben muchas veces por segundo: limitar al registro
                        now = time.time()
                        pct_now = int(m.group(1))
                        if now - last_emit < 1.0 and not (pct_now == 100 and last_pct_sent != 100):
                            continue
                        last_emit, last_pct_sent = now, pct_now
                    on_line(text)
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            rc = proc.wait()
            self._proc = None
            if self._cancelled:
                return False, "Cancelado"
            if rc in (126, 127) and step.argv and step.argv[0] == "pkexec":
                return False, "Autorización cancelada o denegada."
            if rc != 0:
                return False, f"«{step.label or step.argv[0]}» terminó con error ({rc}). Revisa el registro."
        return True, "Listo"
