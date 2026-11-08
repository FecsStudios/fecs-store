import subprocess
import os
import threading

class LinuxStoreBackend:
    def __init__(self):
        self.distro = self.detect_distro()
        self.flatpak_available = self.check_command("flatpak")
        
    def detect_distro(self):
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        if k == "ID":
                            return v.strip('"').lower()
        except Exception:
            return "unknown"

    def check_command(self, cmd):
        return subprocess.run(f"which {cmd}", shell=True, capture_output=True).returncode == 0

    def is_installed(self, source, pkg):
        if source == "flatpak" and self.flatpak_available:
            res = subprocess.run(f"flatpak info {pkg}", shell=True, capture_output=True)
            return res.returncode == 0
        elif source == "aur":
            res = subprocess.run(f"pacman -Q {pkg}", shell=True, capture_output=True)
            return res.returncode == 0
        elif source == "apt":
            res = subprocess.run(f"dpkg -l | grep {pkg}", shell=True, capture_output=True)
            return res.returncode == 0
        return False

    def get_catalog(self, category="inicio"):
        master_catalog = [
            {
                "name": "Discord",
                "category": "inicio",
                "desc": "Habla con tus amigos mientras juegas.",
                "banner_desc": "La plataforma de comunicación por voz, vídeo y texto líder en el mundo.",
                "icon": "💬",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "com.discordapp.Discord", "info": "Versión oficial en contenedor seguro."},
                    {"type": "Nativo (AUR)", "source": "aur", "pkg": "discord", "info": "Cliente nativo con aceleración por hardware."}
                ]
            },
            {
                "name": "Visual Studio Code",
                "category": "inicio",
                "desc": "Editor de código fuente avanzado por Microsoft.",
                "banner_desc": "Entorno de desarrollo moderno y ligero.",
                "icon": "💻",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "com.visualstudio.code", "info": "VS Code aislado en Flatpak."},
                    {"type": "Nativo (AUR)", "source": "aur", "pkg": "visual-studio-code-bin", "info": "Versión nativa optimizada."}
                ]
            },
            {
                "name": "Spotify",
                "category": "inicio",
                "desc": "Reproductor de música en streaming.",
                "banner_desc": "Escucha millones de canciones sin anuncios.",
                "icon": "🎵",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "com.spotify.Client", "info": "Cliente de música oficial."}
                ]
            },
            {
                "name": "Zoom",
                "category": "inicio",
                "desc": "Videoconferencias y clases virtuales.",
                "banner_desc": "Conéctate a tus reuniones y clases online.",
                "icon": "📹",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "us.zoom.Zoom", "info": "Cliente oficial de Zoom."}
                ]
            },
            {
                "name": "Steam",
                "category": "juegos",
                "desc": "Plataforma de videojuegos de Valve.",
                "banner_desc": "La red social de juegos definitiva.",
                "icon": "🎮",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "com.valvesoftware.Steam", "info": "Steam en Flatpak."},
                    {"type": "Nativo (AUR)", "source": "aur", "pkg": "steam", "info": "Steam oficial de los repositorios."}
                ]
            },
            {
                "name": "Heroic Games",
                "category": "juegos",
                "desc": "Play Epic, GOG and Amazon Games.",
                "banner_desc": "Launcher alternativo para Epic Games y GOG.",
                "icon": "🕹️",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "com.heroicgameslauncher.hgl", "info": "Lanzador sin DRM."}
                ]
            },
            {
                "name": "Prism Launcher",
                "category": "juegos",
                "desc": "Custom Minecraft Launcher.",
                "banner_desc": "Administrador avanzado de instancias de Minecraft.",
                "icon": "🧊",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "org.prismlauncher.PrismLauncher", "info": "Gestor de mods de Minecraft."}
                ]
            },
            {
                "name": "Cisco Packet Tracer",
                "category": "tus apps",
                "desc": "Simulador de redes de Cisco.",
                "banner_desc": "Herramienta oficial para prácticas de networking.",
                "icon": "🌐",
                "options": [
                    {"type": "Nativo (AUR)", "source": "aur", "pkg": "cisco-packet-tracer", "info": "Simulador de redes avanzado."}
                ]
            },
            {
                "name": "GNS3",
                "category": "tus apps",
                "desc": "Emulador de redes profesional.",
                "banner_desc": "Simulación gráfica de topologías complejas.",
                "icon": "🖧",
                "options": [
                    {"type": "Nativo (AUR)", "source": "aur", "pkg": "gns3-gui", "info": "Emulador de redes avanzado."}
                ]
            },
            {
                "name": "OnlyOffice",
                "category": "tus apps",
                "desc": "Suite de oficina compatible con Office.",
                "banner_desc": "Editores de documentos profesionales.",
                "icon": "📄",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "org.onlyoffice.desktopeditors", "info": "Suite ofimática completa."}
                ]
            },
            {
                "name": "Telegram",
                "category": "tus apps",
                "desc": "Mensajería instantánea segura.",
                "banner_desc": "Cliente rápido y cifrado.",
                "icon": "✈️",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "org.telegram.desktop", "info": "Telegram oficial."}
                ]
            },
            {
                "name": "GIMP",
                "category": "tus apps",
                "desc": "Editor avanzado de imágenes.",
                "banner_desc": "Manipulación fotográfica profesional.",
                "icon": "🖼️",
                "options": [
                    {"type": "Flatpak (Universal)", "source": "flatpak", "pkg": "org.gimp.GIMP", "info": "Editor gráfico."}
                ]
            }
        ]

        if category == "inicio":
            apps = master_catalog[:4]
        else:
            apps = [app for app in master_catalog if app["category"] == category]

        for app in apps:
            for opt in app["options"]:
                opt["installed"] = self.is_installed(opt["source"], opt["pkg"])

        return apps

    def search_apps(self, query):
        q = query.strip().lower()
        master_catalog = self.get_catalog("inicio") + self.get_catalog("juegos") + self.get_catalog("tus apps")
        
        if not q:
            return master_catalog

        results = [app for app in master_catalog if q in app["name"].lower() or q in app["desc"].lower()]
        
        for app in results:
            for opt in app["options"]:
                opt["installed"] = self.is_installed(opt["source"], opt["pkg"])

        return results

    def perform_action(self, option_data, action_type, callback_progress):
        def run_cmd():
            pkg = option_data["pkg"]
            source = option_data["source"]
            opt_type = option_data["type"]

            if action_type == "install":
                callback_progress(f"Instalando {pkg} ({opt_type})...")
                if source == "aur":
                    helper = "yay" if self.check_command("yay") else "paru"
                    cmd = f"{helper} -S --noconfirm {pkg}"
                else:
                    cmd = f"flatpak install -y flathub {pkg}"
            elif action_type == "uninstall":
                callback_progress(f"Desinstalando {pkg}...")
                if source == "aur":
                    cmd = f"sudo pacman -R --noconfirm {pkg}"
                else:
                    cmd = f"flatpak uninstall -y {pkg}"

            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                clean = line.strip()
                if clean:
                    callback_progress(clean)
            
            process.wait()
            if process.returncode == 0:
                callback_progress(f"¡Operación completada con éxito para {pkg}!")
            else:
                callback_progress(f"Error al procesar {pkg}. Revisa permisos.")

        threading.Thread(target=run_cmd).start()