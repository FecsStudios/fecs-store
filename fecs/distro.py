"""Detección de la distribución Linux y de su gestor de paquetes nativo."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Distro:
    id: str            # p. ej. "manjaro"
    name: str          # p. ej. "Manjaro Linux"
    family: str        # arch | debian | fedora | suse | alpine | void | unknown
    pm_label: str      # etiqueta visible: pacman, apt, dnf...
    immutable: bool    # Silverblue, Bazzite, SteamOS... (sin instalación nativa)
    aur_helper: str    # "yay", "paru" o "" (solo familia arch)


# primer id (ID o ID_LIKE) que coincida gana
_FAMILY_BY_ID = {
    "arch": "arch", "manjaro": "arch", "endeavouros": "arch", "cachyos": "arch",
    "garuda": "arch", "artix": "arch", "arcolinux": "arch", "steamos": "arch",
    "quantum": "arch", "quantumlinux": "arch", "blendos": "arch", "archcraft": "arch", "biglinux": "arch",
    "debian": "debian", "ubuntu": "debian", "linuxmint": "debian", "pop": "debian",
    "elementary": "debian", "zorin": "debian", "kali": "debian", "raspbian": "debian",
    "neon": "debian", "mx": "debian", "parrot": "debian", "deepin": "debian",
    "fedora": "fedora", "rhel": "fedora", "centos": "fedora", "rocky": "fedora",
    "almalinux": "fedora", "nobara": "fedora", "bazzite": "fedora",
    "opensuse": "suse", "suse": "suse", "sles": "suse", "opensuse-tumbleweed": "suse",
    "opensuse-leap": "suse",
    "alpine": "alpine", "postmarketos": "alpine",
    "void": "void",
}

PM_LABEL = {
    "arch": "pacman", "debian": "apt", "fedora": "dnf", "suse": "zypper",
    "alpine": "apk", "void": "xbps", "unknown": "—",
}


def parse_os_release(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def family_from(os_id: str, id_like: str) -> str:
    for token in [os_id.lower(), *id_like.lower().split()]:
        if token in _FAMILY_BY_ID:
            return _FAMILY_BY_ID[token]
    return "unknown"


def family_from_tools() -> str:
    """Último recurso: si el ID de la distro no se reconoce, usar el gestor de paquetes que exista."""
    for tool, fam in (("pacman", "arch"), ("apt-get", "debian"), ("dnf", "fedora"),
                      ("zypper", "suse"), ("apk", "alpine"), ("xbps-install", "void")):
        if shutil.which(tool):
            return fam
    return "unknown"


def detect_distro(os_release_path: str = "/etc/os-release") -> Distro:
    data: dict[str, str] = {}
    for path in (os_release_path, "/usr/lib/os-release"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = parse_os_release(fh.read())
            if data:
                break
        except OSError:
            continue
    os_id = data.get("ID", "unknown").lower()
    family = family_from(os_id, data.get("ID_LIKE", ""))
    if family == "unknown":
        family = family_from_tools()
    immutable = os.path.exists("/run/ostree-booted") or data.get("VARIANT_ID", "") in {
        "silverblue", "kinoite", "sericea", "onyx", "coreos"}
    if os_id == "steamos":
        immutable = True
    helper = ""
    if family == "arch":
        helper = "yay" if shutil.which("yay") else ("paru" if shutil.which("paru") else "")
    return Distro(
        id=os_id,
        name=data.get("PRETTY_NAME") or data.get("NAME") or os_id.title(),
        family=family,
        pm_label=PM_LABEL.get(family, "—"),
        immutable=immutable,
        aur_helper=helper,
    )
