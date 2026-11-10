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
import select
import shlex
import signal
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
    heal: Optional[list] = None   # orden a ejecutar (y reintentar) si falla por bases de datos desactualizadas
    pw_lines: int = 0             # veces que hay que enviar la contraseña de sudo por stdin (AUR)
    fallback: Optional[list] = None  # pasos alternativos (Flatpak) si el paquete nativo no existe en esta distro


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


_AGENT_RE = re.compile(
    r"polkit.*agent|polkit-gnome|xfce-polkit|lxpolkit|lxqt-policykit|polkit-mate|mate-polkit|"
    r"polkit-kde|plasma-polkit|hyprpolkitagent|pantheon-agent-polkit|ukui-polkit|gnome-shell|cinnamon-session|budgie",
    re.I)


def _polkit_agent_running() -> bool:
    """¿Hay un agente de polkit que pueda mostrar la ventana de contraseña? (XFCE/i3/etc. a veces no)."""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if _AGENT_RE.search(cmd):
                return True
    except OSError:
        pass
    return False


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def _open_pty():
    """Pseudo-terminal para que pacman/flatpak/apt muestren su progreso real de descarga."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
    return master, slave


def _rx_bytes() -> int:
    """Bytes recibidos por las interfaces de red reales (para medir la velocidad de descarga)."""
    total = 0
    try:
        with open("/proc/net/dev", encoding="utf-8") as fh:
            for ln in fh.readlines()[2:]:
                name, data = ln.split(":", 1)
                name = name.strip()
                if name == "lo" or name.startswith(("veth", "docker", "br-", "virbr", "tun", "tap")):
                    continue
                total += int(data.split()[0])
    except (OSError, ValueError, IndexError):
        return 0
    return total


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


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
        has_pk, has_sudo = shutil.which("pkexec") is not None, shutil.which("sudo") is not None
        if has_pk and _polkit_agent_running():
            self.priv = "pkexec"
        elif has_sudo:
            self.priv = "sudo"          # contraseña pedida por la propia tienda (funciona en cualquier escritorio)
        else:
            self.priv = "pkexec" if has_pk else "none"
        self.has_pkexec = self.priv != "none"   # (nombre histórico) = «se puede pedir permisos de administrador»
        self.sudo_password: Optional[str] = None
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
    def _root_script(self, script: str) -> list[str]:
        # pkexec/sudo limpian el entorno: fijamos idioma (para reconocer errores) y apt sin diálogos
        script = "export LC_ALL=C.UTF-8 LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive; " + script
        if self.priv == "sudo":
            return ["sudo", "-S", "-p", "", "sh", "-c", script]
        return ["pkexec", "sh", "-c", script]

    def _root(self, cmds: list[list[str]]) -> list[str]:
        """Comando que ejecuta una o varias órdenes como root (pkexec, o sudo con contraseña de la tienda)."""
        return self._root_script(" && ".join(shlex.join(c) for c in cmds))

    def sudo_ready(self) -> bool:
        """True si sudo ya se puede usar sin pedir contraseña (guardada en memoria o NOPASSWD)."""
        if self.priv != "sudo" or self.sudo_password is not None:
            return True
        try:
            if subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=10, env=_env()).returncode == 0:
                self.sudo_password = ""
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        return False

    def check_sudo(self, password: str) -> tuple[bool, str]:
        try:
            p = subprocess.run(["sudo", "-S", "-p", "", "-v"], input=(password + "\n").encode(),
                               capture_output=True, timeout=30, env=_env())
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
        if p.returncode == 0:
            self.sudo_password = password
            return True, ""
        if "sudoers" in out:
            return False, "nosudo"
        return False, "badpass"

    def _native_cmds(self, action: str, pkg: str) -> list[list[str]]:
        fam = self.distro.family
        if fam == "arch":
            return [["pacman", "-S", "--noconfirm", "--needed", pkg]] if action == "install" \
                else [["pacman", "-Rns", "--noconfirm", pkg]]
        if fam == "debian":
            apt = ["apt-get", "-o", "DPkg::Lock::Timeout=120", "-o", "Acquire::Retries=3"]
            if action == "install":   # un repositorio de terceros roto no debe impedir instalar
                return [["sh", "-c", "apt-get update || true"], apt + ["install", "-y", pkg]]
            return [apt + ["remove", "-y", pkg]]
        if fam == "fedora":
            dnf = ["dnf", "-y", "--setopt=max_parallel_downloads=10", "--setopt=fastestmirror=True"]
            return [dnf + ["install", pkg]] if action == "install" else [dnf + ["remove", pkg]]
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

    def _flatpak_install_steps(self, app: App, pkg: str) -> list[Step]:
        steps = self._ensure_flatpak_steps()
        steps.append(Step(f"Instalando {app.name} (Flatpak)",
                          ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", pkg]))
        return steps

    def _fallback_for(self, app: App) -> Optional[list]:
        """Si el paquete nativo no existe en esta distro, la tienda prueba con Flatpak (si lo hay y es posible)."""
        if not app.fp or self.distro.family == "unknown":
            return None
        if not self.has_flatpak and self.distro.immutable:
            return None
        return self._flatpak_install_steps(app, app.fp)

    def _aur_prereq_steps(self) -> tuple[list[Step], str]:
        """Pasos para tener git, base-devel y un ayudante del AUR (yay) antes de compilar desde el AUR."""
        steps: list[Step] = []
        helper = self.distro.aur_helper or ("yay" if shutil.which("yay") else ("paru" if shutil.which("paru") else ""))
        tools_ok = all(shutil.which(t) for t in ("git", "make", "gcc", "fakeroot", "makepkg"))
        heal = self._root([["pacman", "-Syu", "--noconfirm"]])
        if not tools_ok or not helper:
            steps.append(Step("Instalando herramientas de compilación (git, base-devel)",
                              self._root([["pacman", "-S", "--needed", "--noconfirm", "git", "base-devel"]]), heal=heal))
        if not helper:
            build = Path.home() / ".cache" / "fecs-store" / "aur-bootstrap"
            q = shlex.quote
            steps += [
                Step("Descargando yay (ayudante del AUR)", ["sh", "-c",
                     f"rm -rf {q(str(build))} && mkdir -p {q(str(build))} && "
                     f"git clone --depth 1 https://aur.archlinux.org/yay-bin.git {q(str(build / 'yay-bin'))}"]),
                Step("Compilando yay", ["sh", "-c", f"cd {q(str(build / 'yay-bin'))} && makepkg --noconfirm"]),
                Step("Instalando yay", self._root_script(
                    f"pacman -U --noconfirm {q(str(build / 'yay-bin'))}/yay-bin-*.pkg.tar.*")),
            ]
            helper = "yay"
        return steps, helper

    def plan(self, app: App, src: Source, action: str) -> tuple[list[Step], str]:
        """Devuelve (pasos, error). ``action``: install | uninstall."""
        d = self.distro
        if src.kind == "flatpak":
            if action == "install":
                if not self.has_flatpak and (d.immutable or d.family == "unknown"):
                    return [], "Flatpak no está instalado en este sistema."
                return self._flatpak_install_steps(app, src.pkg), ""
            return [Step(f"Desinstalando {app.name} (Flatpak)",
                         ["flatpak", "uninstall", "-y", "--noninteractive", src.pkg])], ""

        if src.kind in ("native", "aur"):
            if d.immutable:
                return [], "Este sistema es inmutable: usa la versión Flatpak."
            if not self.has_pkexec:
                return [], "No hay forma de pedir permisos de administrador (instala sudo o polkit)."
            if src.kind == "aur":
                if action == "install":
                    pre, helper = self._aur_prereq_steps()   # instala git/base-devel/yay si faltan
                    if self.priv == "sudo":
                        argv = [helper, "-S", "--noconfirm", "--needed", "--sudo", "sudo", "--sudoflags", "-S"]
                    else:
                        argv = [helper, "-S", "--noconfirm", "--needed", "--sudo", "pkexec"]
                    argv += ["--skipreview"] if helper == "paru" else \
                        ["--answerdiff", "None", "--answerclean", "None", "--answeredit", "None"]
                    argv.append(src.pkg)
                    return pre + [Step(f"Compilando e instalando {app.name} (AUR)", argv,
                                       pw_lines=10 if self.priv == "sudo" else 0,
                                       fallback=self._fallback_for(app))], ""
                return [Step(f"Desinstalando {app.name}", self._root(self._native_cmds("uninstall", src.pkg)))], ""
            cmds = self._native_cmds(action, src.pkg)
            if not cmds:
                return [], "Esta distribución no tiene gestor de paquetes compatible."
            verb = "Instalando" if action == "install" else "Desinstalando"
            heal = None
            if action == "install" and d.family == "arch":     # bases de datos viejas -> 404: actualizar y reintentar
                heal = self._root([["pacman", "-Syu", "--noconfirm"]])
            elif action == "install" and d.family == "suse":
                heal = self._root([["zypper", "--non-interactive", "refresh"]])
            return [Step(f"{verb} {app.name} ({d.pm_label})", self._root(cmds), heal=heal,
                         fallback=self._fallback_for(app) if action == "install" else None)], ""

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
                "fedora": [["dnf", "upgrade", "-y", "--setopt=max_parallel_downloads=10"]],
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
        steps = [Step("Activando 10 descargas en paralelo", self._root_script(script))]
        if shutil.which("cachyos-rate-mirrors"):
            steps.append(Step("Buscando los espejos más rápidos (puede tardar un minuto)",
                              self._root([["cachyos-rate-mirrors"]])))
        elif shutil.which("rate-mirrors"):
            steps.append(Step("Buscando los espejos más rápidos (puede tardar un minuto)",
                              self._root_script("rate-mirrors --save=/etc/pacman.d/mirrorlist arch --max-delay=21600")))
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
    @staticmethod
    def _kill(p, group: bool) -> None:
        """Termina el proceso (y, si tiene grupo propio, también a sus hijos)."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                if group:
                    os.killpg(p.pid, sig)
                else:
                    p.send_signal(sig)
            except (OSError, ProcessLookupError):
                return
            for _ in range(30):
                if p.poll() is not None:
                    return
                time.sleep(0.1)

    def cancel(self):
        self._cancelled = True
        p = self._proc
        if p and p.poll() is None:
            self._kill(p, getattr(self, "_proc_group", False))

    STALL_SECS = 240   # Flatpak sin red, sin salida y sin CPU durante este tiempo = descarga atascada

    @staticmethod
    def _cpu_ticks(pid: int) -> int:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                rest = fh.read().rsplit(")", 1)[1].split()
            return int(rest[11]) + int(rest[12])
        except (OSError, ValueError, IndexError):
            return 0

    def _net_monitor(self, stop: threading.Event, last_line: list, on_status, proc=None,
                     stall: int = 0, stalled: Optional[list] = None) -> None:
        """Informa velocidad de descarga y, para Flatpak, corta las descargas que se quedan paradas."""
        start = time.time()
        prev = _rx_bytes()
        prev_ticks = self._cpu_ticks(proc.pid) if proc else 0
        got = idle = 0
        while not stop.wait(1.0):
            cur = _rx_bytes()
            delta = max(0, cur - prev)
            prev = cur
            got += delta
            recent = time.time() - last_line[0] < 2.0
            if stall and proc is not None:
                ticks = self._cpu_ticks(proc.pid)
                active = recent or delta > 2048 or ticks != prev_ticks
                prev_ticks = ticks
                idle = 0 if active else idle + 1
                if idle >= stall:
                    if stalled is not None:
                        stalled.append(True)
                    self._kill(proc, True)
                    return
            if recent or not on_status:
                continue
            el = int(time.time() - start)
            t = f"{el // 60}:{el % 60:02d}"
            if delta > 50 * 1024:
                on_status(f"Descargando… {_human(delta)}/s · {t}")
            elif el > 15:
                on_status(f"Sin tráfico de red ahora ({t}): instalando o procesando… "
                          f"si dura mucho, tu conexión o el espejo van lentos")

    _LOCK_RE = re.compile(r"db\.lck|could not get lock|unable to lock|dpkg frontend lock|unable to acquire the dpkg|"
                          r"waiting for cache lock|system management is locked|rpm.*lock|is another process", re.I)
    _STALE_RE = re.compile(r"\b404\b|target not found|failed to retrieve some files|not found in sync db", re.I)
    _NET_RE = re.compile(r"timed out|timeout|connection reset|could not resolve|temporary failure|name or service|"
                         r"connection refused|network is unreachable|tls|ssl|transfer closed|premature end", re.I)
    _MISSING_RE = re.compile(r"target not found|no package found|nothing provides|unable to locate package|"
                             r"no match for argument|unable to find a match|not found in package names|"
                             r"no such package|not found in repository|could not find all required packages|"
                             r"ningún paquete", re.I)
    _ERR_RE = re.compile(r"error|fail|E:|cannot|not found|conflict|denied|unable|could not|no space", re.I)

    def _exec_once(self, argv: list, pw_lines: int, on_line, on_pct, on_status) -> tuple[int, list[str]]:
        """Ejecuta una orden mostrando su progreso real. Devuelve (código, últimas líneas)."""
        pct_re = re.compile(r"(\d{1,3})\s?%")
        on_pct(-1)
        try:
            master, slave = _open_pty()
        except OSError:
            master = slave = None
        env = _env()
        env["TERM"] = "xterm"
        env["NO_COLOR"] = "1"
        needs_pw = bool(pw_lines) or (argv[:2] == ["sudo", "-S"])
        stdin = subprocess.PIPE if needs_pw else subprocess.DEVNULL
        own_group = bool(argv) and argv[0] == "flatpak"   # grupo propio: se puede cortar con todos sus hijos
        if slave is not None:
            proc = subprocess.Popen(argv, stdin=stdin, stdout=slave, stderr=slave, env=env, close_fds=True,
                                    start_new_session=own_group)
            os.close(slave)
        else:
            proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=env, bufsize=0, start_new_session=own_group)
        self._proc = proc
        self._proc_group = own_group
        if needs_pw:
            try:
                proc.stdin.write(((self.sudo_password or "") + "\n").encode() * max(1, pw_lines))
                proc.stdin.close()
            except OSError:
                pass
        last_line = [time.time()]
        stop_mon = threading.Event()
        stalled: list = []
        self._last_stalled = stalled
        stall = self.STALL_SECS if argv and argv[0] == "flatpak" else 0
        if on_status or stall:
            threading.Thread(target=self._net_monitor, args=(stop_mon, last_line, on_status, proc, stall, stalled),
                             daemon=True).start()
        tail: list[str] = []
        state = {"buf": b"", "last_emit": 0.0, "last_pct": -2}
        fd = master if master is not None else proc.stdout.fileno()

        def feed(data: bytes, final: bool = False) -> None:
            state["buf"] += data
            parts = re.split(rb"[\r\n]+", state["buf"])
            state["buf"] = b"" if final else parts.pop()
            for part in parts:
                text = _ANSI.sub("", part.decode("utf-8", "replace")).strip()
                if not text:
                    continue
                last_line[0] = time.time()
                m = pct_re.search(text)
                is_progress = bool(m) and ("/s" in text or "[" in text)
                if m and 0 <= int(m.group(1)) <= 100:
                    on_pct(int(m.group(1)))
                if is_progress:  # las barras se reescriben muchas veces por segundo: limitar el registro
                    now = time.time()
                    pct_now = int(m.group(1))
                    if now - state["last_emit"] < 1.0 and not (pct_now == 100 and state["last_pct"] != 100):
                        continue
                    state["last_emit"], state["last_pct"] = now, pct_now
                else:
                    tail.append(text)
                    del tail[:-60]
                on_line(text)

        # IMPORTANTE: no se puede esperar a que el pseudo-terminal "se cierre" (EOF/EIO). Si algún proceso hijo
        # (gpg-agent, dbus, un demonio del instalador...) hereda el terminal y sigue vivo, la lectura se queda
        # bloqueada para siempre aunque la instalación ya haya terminado: la tienda mostraba "Descargando…"
        # eternamente. Por eso se lee con tiempo límite y se termina en cuanto el proceso principal acaba.
        exited_at = 0.0
        try:
            while True:
                if not exited_at and proc.poll() is not None:
                    exited_at = time.time()
                try:
                    ready, _, _ = select.select([fd], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if not ready:
                    if proc.poll() is not None:
                        break      # proceso terminado y sin más salida pendiente
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                feed(chunk)
                if exited_at and time.time() - exited_at > 3.0:
                    break          # el proceso ya acabó y algo sigue escribiendo en el terminal: no esperar más
            feed(b"", final=True)
        finally:
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            try:
                rc = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._kill(proc, own_group)
                rc = proc.wait()
            stop_mon.set()
            self._proc = None
        return rc, tail

    def _wait(self, seconds: int) -> None:
        for _ in range(seconds):
            if self._cancelled:
                return
            time.sleep(1)

    def run_steps(self, steps: list[Step], on_line: Callable[[str], None],
                  on_pct: Callable[[int], None], on_status: Optional[Callable[[str], None]] = None,
                  _nested: bool = False) -> tuple[bool, str]:
        if not _nested:
            self._cancelled = False
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
            lock_tries = net_tries = 0
            healed = False
            while True:
                try:
                    rc, tail = self._exec_once(step.argv, step.pw_lines, on_line, on_pct, on_status)
                except FileNotFoundError:
                    return False, f"No se encontró el comando «{step.argv[0]}»."
                except OSError as e:
                    return False, str(e)
                if self._cancelled:
                    return False, "Cancelado"
                if rc == 0:
                    break
                blob = "\n".join(tail)
                if step.argv and step.argv[0] == "pkexec" and rc in (126, 127):
                    return False, ("Autorización cancelada o denegada. Si no te apareció la ventana de contraseña, "
                                   "reinicia la tienda: usará sudo y te la pedirá ella misma.")
                if re.search(r"incorrect password|sorry, try again|no password was provided", blob, re.I):
                    self.sudo_password = None
                    return False, "Contraseña incorrecta. Vuelve a intentarlo."
                if self._LOCK_RE.search(blob) and lock_tries < 8:
                    lock_tries += 1
                    on_line("⏳ Otro gestor de paquetes está en uso; esperando para reintentar…")
                    self._wait(8)
                    continue
                if step.heal and not healed and self._STALE_RE.search(blob):
                    healed = True
                    on_line("↻ Las listas de paquetes están desactualizadas: actualizando y reintentando…")
                    try:
                        hrc, _ = self._exec_once(step.heal, step.pw_lines, on_line, on_pct, on_status)
                    except OSError:
                        hrc = 1
                    if self._cancelled:
                        return False, "Cancelado"
                    if hrc == 0:
                        continue
                if getattr(self, "_last_stalled", None) and net_tries < 2:
                    net_tries += 1
                    on_line("↻ La descarga se quedó parada; cancelando y reintentando…")
                    continue
                if self._NET_RE.search(blob) and net_tries < 2:
                    net_tries += 1
                    on_line("↻ Falló la conexión; reintentando…")
                    self._wait(5)
                    continue
                if step.fallback and self._MISSING_RE.search(blob):
                    on_line("↪ Ese paquete no existe en los repositorios de tu distro: instalando la versión Flatpak…")
                    ok2, msg2 = self.run_steps(step.fallback, on_line, on_pct, on_status, _nested=True)
                    if ok2:
                        break
                    return False, msg2
                key = [ln for ln in tail if self._ERR_RE.search(ln)][-5:] or tail[-5:]
                detail = "\n".join(key)
                return False, (f"«{step.label or step.argv[0]}» terminó con error ({rc}).\n{detail}").strip()
        return True, "Listo"
