"""Catálogo curado de Fecs Store.

Cada línea de ``_RAW`` describe una app:

    Nombre | categoría | emoji | descripción | clave=valor | ...

Claves opcionales:
    fp=<id Flathub>                      versión Flatpak
    n=<familia>:<paquete>,...            paquete nativo por familia de distro
                                         (``*`` = arch, debian, fedora y suse;
                                          ``aur:<pkg>`` = paquete del AUR)
    web=<url>                            web app (se abre / se crea acceso directo)
    dl=<url>                             descarga oficial en el sitio del fabricante
    by=<creador>                         desarrollador o empresa responsable
    img=<url>;<url>                      imágenes de vista previa (URLs HTTPS)
    note=<texto>                         aviso mostrado en la ficha
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FAMILIES = ("arch", "debian", "fedora", "suse")

CATEGORIES = {
    "juegos": "Juegos",
    "oficina": "Ofimática",
    "multimedia": "Video y audio",
    "diseno": "Diseño y 3D",
    "dev": "Desarrollo",
    "redes": "Redes",
    "internet": "Internet y chat",
    "educacion": "Educación y ciencia",
    "utiles": "Utilidades",
    "otros": "Otras apps",
}

# Orden de la pantalla de inicio y del banner destacado.
POPULAR = [
    "Steam", "Discord", "OnlyOffice", "Cisco Packet Tracer", "Visual Studio Code",
    "Kdenlive", "Blender", "OBS Studio", "GIMP", "LibreOffice", "Heroic Games Launcher",
    "VLC", "Spotify", "Telegram", "GNS3", "Wireshark", "DaVinci Resolve", "Krita",
    "Inkscape", "Lutris", "Prism Launcher", "Google Chrome", "Firefox", "Zoom",
    "WPS Office", "Photopea", "Figma", "Canva", "Google Docs", "Bottles",
]
FEATURED = ["Steam", "OnlyOffice", "Cisco Packet Tracer", "Kdenlive", "Blender", "Discord"]

_RAW = """
Steam | juegos | 🎮 | Tienda y biblioteca de juegos de Valve | fp=com.valvesoftware.Steam | n=arch:steam,debian:steam-installer
Heroic Games Launcher | juegos | 🕹️ | Juegos de Epic, GOG y Amazon sin complicaciones | fp=com.heroicgameslauncher.hgl | n=arch:aur:heroic-games-launcher-bin
Lutris | juegos | 🍷 | Gestor de juegos de Windows, emuladores y launchers | fp=net.lutris.Lutris | n=*:lutris
Bottles | juegos | 🍾 | Ejecuta programas de Windows con Wine fácilmente | fp=com.usebottles.bottles | n=arch:aur:bottles
Prism Launcher | juegos | 🧊 | Lanzador de Minecraft con instancias y mods | fp=org.prismlauncher.PrismLauncher | n=arch:prismlauncher
ProtonUp-Qt | juegos | ⚙️ | Instala Proton-GE y Wine-GE para Steam y Lutris | fp=net.davidotek.pupgui2 | n=arch:aur:protonup-qt
Minecraft Bedrock Launcher | juegos | ⛏️ | Lanzador no oficial de Minecraft Bedrock | fp=io.mrarm.mcpelauncher
Luanti (Minetest) | juegos | 🧱 | Motor de juegos de bloques de código abierto | fp=org.luanti.luanti
SuperTuxKart | juegos | 🏎️ | Carreras de karts 3D con mucha diversión | fp=net.supertuxkart.SuperTuxKart | n=*:supertuxkart
0 A.D. | juegos | 🏛️ | Estrategia histórica en tiempo real | fp=com.play0ad.zeroad | n=*:0ad
Battle for Wesnoth | juegos | 🧙 | Estrategia por turnos de fantasía | fp=org.wesnoth.Wesnoth | n=*:wesnoth
OpenTTD | juegos | 🚂 | Simulador de transporte y empresas | fp=org.openttd.OpenTTD | n=*:openttd
Xonotic | juegos | 🔫 | Shooter arena rápido y gratuito | fp=org.xonotic.Xonotic | n=arch:xonotic,debian:xonotic,fedora:xonotic
Mindustry | juegos | 🏭 | Automatización y defensa de torres | fp=com.github.Anuken.Mindustry
RetroArch | juegos | 👾 | Frontend para emuladores y juegos retro | fp=org.libretro.RetroArch | n=arch:retroarch,debian:retroarch
PCSX2 | juegos | 🎮 | Emulador de PlayStation 2 | fp=net.pcsx2.PCSX2 | n=arch:pcsx2
RPCS3 | juegos | 🎮 | Emulador de PlayStation 3 | fp=net.rpcs3.RPCS3 | n=arch:aur:rpcs3-bin
PPSSPP | juegos | 🎮 | Emulador de PSP | fp=org.ppsspp.PPSSPP | n=arch:ppsspp
DuckStation | juegos | 🎮 | Emulador de PlayStation 1 | fp=org.duckstation.DuckStation
Dolphin Emulator | juegos | 🎮 | Emulador de GameCube y Wii | fp=org.DolphinEmu.dolphin-emu | n=arch:dolphin-emu,debian:dolphin-emu
Cemu | juegos | 🎮 | Emulador de Wii U | fp=info.cemu.Cemu
mGBA | juegos | 🎮 | Emulador de Game Boy Advance | fp=io.mgba.mGBA | n=arch:mgba-qt,debian:mgba-qt
melonDS | juegos | 🎮 | Emulador de Nintendo DS | fp=net.kuribo64.melonDS
ScummVM | juegos | 🗝️ | Aventuras gráficas clásicas | fp=org.scummvm.ScummVM | n=*:scummvm
itch.io | juegos | 🧩 | Juegos indie y herramientas de itch.io | fp=io.itch.itch
Vinegar | juegos | 🧱 | Roblox Player y Studio en Linux | fp=org.vinegarhq.Vinegar
GNOME Chess | juegos | ♟️ | Ajedrez sencillo y elegante | fp=org.gnome.Chess | n=*:gnome-chess
Lichess | juegos | ♞ | Ajedrez online gratuito | web=https://lichess.org
Chess.com | juegos | ♟️ | Ajedrez online y puzzles | web=https://www.chess.com
GeForce NOW | juegos | ☁️ | Juega en la nube con NVIDIA | web=https://play.geforcenow.com
Xbox Cloud Gaming | juegos | ☁️ | Juegos de Xbox en el navegador | web=https://www.xbox.com/play
Albion Online | juegos | ⚔️ | MMORPG sandbox multiplataforma | dl=https://albiononline.com/download
LibreOffice | oficina | 📄 | Suite ofimática completa y libre | fp=org.libreoffice.LibreOffice | n=arch:libreoffice-fresh,debian:libreoffice,fedora:libreoffice,suse:libreoffice
OnlyOffice | oficina | 📝 | Editores compatibles con Word, Excel y PowerPoint | fp=org.onlyoffice.desktopeditors | n=arch:aur:onlyoffice-bin | dl=https://www.onlyoffice.com/download-desktop.aspx
WPS Office | oficina | 🗒️ | Suite ofimática con interfaz tipo Microsoft Office | fp=com.wps.Office | n=arch:aur:wps-office | dl=https://www.wps.com/office/linux/
FreeOffice | oficina | 📊 | Suite de SoftMaker gratuita y muy compatible | n=arch:aur:freeoffice | dl=https://www.freeoffice.com/en/download
Calligra | oficina | 🗂️ | Suite ofimática de KDE: Words, Sheets, Stage | n=arch:calligra,debian:calligra
AbiWord | oficina | ✍️ | Procesador de textos ligero | fp=com.abisource.AbiWord | n=*:abiword
Gnumeric | oficina | 📈 | Hoja de cálculo potente y ligera | fp=org.gnome.gnumeric | n=*:gnumeric
Okular | oficina | 📕 | Visor de PDF y documentos de KDE | fp=org.kde.okular | n=*:okular
Xournal++ | oficina | 🖊️ | Notas manuscritas y anotación de PDF | fp=com.github.xournalpp.xournalpp | n=arch:xournalpp,debian:xournalpp,fedora:xournalpp
Obsidian | oficina | 🔮 | Notas en Markdown con enlaces entre ellas | fp=md.obsidian.Obsidian | n=arch:obsidian
Joplin | oficina | 📓 | Notas y tareas con sincronización | fp=net.cozic.joplin_desktop | n=arch:aur:joplin-desktop
Logseq | oficina | 🧠 | Base de conocimiento en esquemas | fp=com.logseq.Logseq
Zotero | oficina | 📚 | Gestor de referencias y bibliografía | fp=org.zotero.Zotero | n=arch:aur:zotero-bin
Calibre | oficina | 📖 | Biblioteca y conversor de libros electrónicos | fp=com.calibre_ebook.calibre | n=*:calibre
Scribus | oficina | 📰 | Maquetación y diseño editorial | fp=net.scribus.Scribus | n=*:scribus
TeXstudio | oficina | 🧮 | Editor LaTeX completo | fp=org.texstudio.TeXstudio | n=arch:texstudio,debian:texstudio,fedora:texstudio
Foliate | oficina | 📘 | Lector de libros electrónicos minimalista | fp=com.github.johnfactotum.Foliate | n=arch:foliate,fedora:foliate
Microsoft 365 | oficina | 🅾️ | Word, Excel y PowerPoint en el navegador | web=https://www.office.com
Google Docs | oficina | 📃 | Documentos de texto en la nube | web=https://docs.google.com/document
Google Sheets | oficina | 🟩 | Hojas de cálculo en la nube | web=https://docs.google.com/spreadsheets
Google Slides | oficina | 🟨 | Presentaciones en la nube | web=https://docs.google.com/presentation
Google Drive | oficina | ☁️ | Almacenamiento en la nube de Google | web=https://drive.google.com
Notion | oficina | ⬜ | Notas, tareas y wikis en un solo lugar | web=https://www.notion.so
Trello | oficina | 🗃️ | Tableros kanban para proyectos | web=https://trello.com
Kdenlive | multimedia | 🎞️ | Editor de video profesional no lineal | fp=org.kde.kdenlive | n=*:kdenlive
Shotcut | multimedia | 🎬 | Editor de video multiplataforma | fp=org.shotcut.Shotcut | n=arch:shotcut,debian:shotcut
OpenShot | multimedia | 🎥 | Editor de video fácil de usar | fp=org.openshot.OpenShot | n=arch:openshot,debian:openshot-qt,fedora:openshot
Olive | multimedia | 🫒 | Editor de video no lineal moderno | fp=org.olivevideoeditor.Olive
Flowblade | multimedia | 🌊 | Editor de video multipista | fp=io.github.jliljebl.Flowblade | n=arch:flowblade,debian:flowblade
Pitivi | multimedia | 🎞️ | Editor de video de GNOME | fp=org.pitivi.Pitivi | n=*:pitivi
DaVinci Resolve | multimedia | 🎬 | Edición, color, VFX y audio de nivel cine | n=arch:aur:davinci-resolve | dl=https://www.blackmagicdesign.com/products/davinciresolve | by=Blackmagic Design | note=Descarga gratuita desde Blackmagic (pide registro). Necesita una GPU compatible.
OBS Studio | multimedia | 🔴 | Grabación y streaming en directo | fp=com.obsproject.Studio | n=arch:obs-studio,debian:obs-studio
HandBrake | multimedia | 🍍 | Convierte y comprime video | fp=fr.handbrake.ghb | n=arch:handbrake,debian:handbrake
Audacity | multimedia | 🎙️ | Editor de audio multipista | fp=org.audacityteam.Audacity | n=*:audacity
Tenacity | multimedia | 🎚️ | Editor de audio libre basado en Audacity | fp=org.tenacityaudio.Tenacity
Ardour | multimedia | 🎛️ | Estación de trabajo de audio digital | fp=org.ardour.Ardour | n=*:ardour
LMMS | multimedia | 🎹 | Producción musical y beats | fp=io.lmms.LMMS | n=arch:lmms,debian:lmms,fedora:lmms
MuseScore | multimedia | 🎼 | Escribe y reproduce partituras | fp=org.musescore.MuseScore | n=arch:musescore
Mixxx | multimedia | 🎧 | Software para DJ | fp=org.mixxx.Mixxx | n=arch:mixxx,debian:mixxx
VLC | multimedia | 🔶 | Reproduce cualquier formato de video y audio | fp=org.videolan.VLC | n=arch:vlc,debian:vlc
mpv | multimedia | ▶️ | Reproductor minimalista y potente | fp=io.mpv.Mpv | n=arch:mpv,debian:mpv
Kodi | multimedia | 📺 | Centro multimedia para tu sala | fp=tv.kodi.Kodi | n=arch:kodi,debian:kodi
Stremio | multimedia | 🍿 | Centro de streaming y addons | fp=com.stremio.Stremio
Spotify | multimedia | 🎵 | Música en streaming | fp=com.spotify.Client | n=arch:aur:spotify | web=https://open.spotify.com
YouTube Music | multimedia | 🎶 | Música y videoclips de YouTube | web=https://music.youtube.com
YouTube | multimedia | ▶️ | Videos de todo el mundo | web=https://www.youtube.com
Netflix | multimedia | 🎬 | Series y películas en streaming | web=https://www.netflix.com
Disney+ | multimedia | 🏰 | Streaming de Disney, Marvel y Star Wars | web=https://www.disneyplus.com
Prime Video | multimedia | 📽️ | Streaming de Amazon | web=https://www.primevideo.com
Twitch | multimedia | 💜 | Directos y comunidades de streaming | web=https://www.twitch.tv
SoundCloud | multimedia | ☁️ | Música independiente y podcasts | web=https://soundcloud.com
CapCut | multimedia | ✂️ | Editor de video online rápido | web=https://www.capcut.com/editor
Clipchamp | multimedia | 🎬 | Editor de video online de Microsoft | web=https://app.clipchamp.com
Kapwing | multimedia | 🎞️ | Editor de video y memes en línea | web=https://www.kapwing.com
GIMP | diseno | 🖼️ | Editor de imágenes avanzado | fp=org.gimp.GIMP | n=*:gimp
Krita | diseno | 🖌️ | Pintura digital y animación | fp=org.kde.krita | n=*:krita
Inkscape | diseno | ✒️ | Gráficos vectoriales profesionales | fp=org.inkscape.Inkscape | n=*:inkscape
Blender | diseno | 🧊 | Modelado 3D, animación y render | fp=org.blender.Blender | n=*:blender
Darktable | diseno | 📷 | Revelado de fotos RAW | fp=org.darktable.Darktable | n=*:darktable
RawTherapee | diseno | 🌄 | Procesador de fotos RAW | fp=com.rawtherapee.RawTherapee | n=*:rawtherapee
Pinta | diseno | 🎨 | Dibujo y edición de imágenes simple | fp=com.github.PintaProject.Pinta | n=arch:pinta,debian:pinta,fedora:pinta
Drawing | diseno | ✏️ | Dibujo sencillo para GNOME | fp=com.github.maoschanz.drawing | n=arch:drawing,debian:drawing,fedora:drawing
MyPaint | diseno | 🖍️ | Lienzo de pintura con pinceles | fp=org.mypaint.MyPaint | n=arch:mypaint,debian:mypaint,fedora:mypaint
Aseprite | diseno | 👾 | Editor de pixel art y animaciones | n=arch:aseprite | dl=https://www.aseprite.org
FreeCAD | diseno | 📐 | Modelado paramétrico 3D | fp=org.freecad.FreeCAD | n=*:freecad
LibreCAD | diseno | 📏 | CAD 2D libre | fp=org.librecad.librecad | n=*:librecad
OpenSCAD | diseno | 🧩 | CAD 3D por código | fp=org.openscad.OpenSCAD | n=*:openscad
KiCad | diseno | 🔌 | Diseño de circuitos electrónicos y PCB | fp=org.kicad.KiCad | n=*:kicad
Sweet Home 3D | diseno | 🏠 | Diseña la distribución de tu casa | fp=com.sweethome3d.Sweethome3d | n=arch:sweethome3d,debian:sweethome3d
Blockbench | diseno | 🧊 | Modelos 3D estilo bloques | fp=net.blockbench.Blockbench | web=https://web.blockbench.net
Upscayl | diseno | 🔍 | Escala imágenes con IA | fp=org.upscayl.Upscayl
Flameshot | diseno | 📸 | Capturas de pantalla con anotaciones | fp=org.flameshot.Flameshot | n=*:flameshot
draw.io | diseno | 🔷 | Diagramas y flujos | fp=com.jgraph.drawio.desktop | web=https://app.diagrams.net
Photopea | diseno | 🖼️ | Editor tipo Photoshop en el navegador | web=https://www.photopea.com | by=Photopea
Figma | diseno | 🎨 | Diseño de interfaces colaborativo | web=https://www.figma.com | by=Figma, Inc.
Canva | diseno | 🪄 | Diseño gráfico fácil para todos | web=https://www.canva.com | by=Canva
Penpot | diseno | 🖋️ | Diseño y prototipos de código abierto | web=https://design.penpot.app
SketchUp Web | diseno | 🏗️ | Modelado 3D en el navegador | web=https://app.sketchup.com
Excalidraw | diseno | 🧑‍🎨 | Pizarra con estilo dibujado a mano | web=https://excalidraw.com
Visual Studio Code | dev | 💻 | Editor de código moderno de Microsoft | fp=com.visualstudio.code | n=arch:aur:visual-studio-code-bin | dl=https://code.visualstudio.com/Download
VSCodium | dev | 🧑‍💻 | VS Code sin telemetría de Microsoft | fp=com.vscodium.codium | n=arch:aur:vscodium-bin | dl=https://vscodium.com
Cursor | dev | ⌨️ | Editor de código con IA integrada | n=arch:aur:cursor-bin | dl=https://cursor.com/downloads
Android Studio | dev | 🤖 | IDE oficial para apps Android | fp=com.google.AndroidStudio | n=arch:aur:android-studio
IntelliJ IDEA Community | dev | ☕ | IDE de JetBrains para Java y Kotlin | fp=com.jetbrains.IntelliJ-IDEA-Community | n=arch:intellij-idea-community-edition
PyCharm Community | dev | 🐍 | IDE de JetBrains para Python | fp=com.jetbrains.PyCharm-Community | n=arch:pycharm-community-edition
Eclipse IDE | dev | 🌑 | IDE clásico para Java | fp=org.eclipse.Java
Qt Creator | dev | 🧰 | IDE para C++ y Qt | fp=io.qt.QtCreator | n=*:qtcreator
Sublime Text | dev | 📜 | Editor de texto rápido y elegante | fp=com.sublimetext.three
Zed | dev | ⚡ | Editor colaborativo ultrarrápido | fp=dev.zed.Zed | n=arch:zed
Neovim | dev | 📟 | Editor modal extensible | fp=io.neovim.nvim | n=*:neovim
Emacs | dev | 🦬 | El editor extensible | fp=org.gnu.emacs | n=*:emacs
Geany | dev | 🪶 | IDE ligero y rápido | fp=org.geany.Geany | n=*:geany
Kate | dev | 📝 | Editor avanzado de KDE | fp=org.kde.kate | n=*:kate
Thonny | dev | 🐍 | IDE de Python para principiantes | fp=org.thonny.Thonny | n=arch:thonny,debian:thonny,fedora:thonny
Arduino IDE | dev | 🔧 | Programa placas Arduino | fp=cc.arduino.IDE2 | n=arch:arduino-ide
GitHub Desktop | dev | 🐙 | Cliente Git de GitHub | fp=io.github.shiftey.Desktop
GitKraken | dev | 🦑 | Cliente Git visual | fp=com.axosoft.GitKraken
Meld | dev | 🔀 | Compara y fusiona archivos | fp=org.gnome.meld | n=*:meld
Postman | dev | 📮 | Prueba y documenta APIs | fp=com.getpostman.Postman | n=arch:aur:postman-bin
Insomnia | dev | 🌙 | Cliente de APIs REST y GraphQL | fp=rest.insomnia.Insomnia
DBeaver | dev | 🦫 | Cliente universal de bases de datos | fp=io.dbeaver.DBeaverCommunity | n=arch:dbeaver,debian:dbeaver-ce
Podman Desktop | dev | 🐳 | Gestiona contenedores con Podman | fp=io.podman_desktop.PodmanDesktop
Docker | dev | 🐋 | Contenedores para desarrollo | n=arch:docker,debian:docker.io,suse:docker
Godot Engine | dev | 🎲 | Motor de videojuegos 2D y 3D | fp=org.godotengine.Godot | n=arch:godot,fedora:godot
Unity Hub | dev | 🎮 | Gestor de proyectos de Unity | n=arch:aur:unityhub | dl=https://unity.com/download | by=Unity Technologies
Replit | dev | 🔁 | Programa en el navegador con IA | web=https://replit.com | by=Replit
GitHub | dev | 🐙 | Repositorios y colaboración | web=https://github.com | by=GitHub
Cisco Packet Tracer | redes | 🌐 | Simulador de redes de Cisco Networking Academy | dl=https://www.netacad.com/cisco-packet-tracer | by=Cisco | note=Requiere una cuenta gratuita de Cisco Networking Academy: descarga el instalador (.deb o .tar.gz) del sitio oficial. Cisco no permite instalarlo automáticamente.
GNS3 | redes | 🖧 | Emulador de redes con equipos reales | n=arch:gns3-gui,debian:gns3-gui,fedora:gns3-gui | dl=https://www.gns3.com/software/download | by=GNS3 Project
Wireshark | redes | 🦈 | Analizador de tráfico de red | fp=org.wireshark.Wireshark | n=arch:wireshark-qt,debian:wireshark,fedora:wireshark,suse:wireshark
EVE-NG | redes | 🔬 | Laboratorios de redes virtuales | dl=https://www.eve-ng.net/index.php/download/
Winbox | redes | 📡 | Configuración de equipos MikroTik | dl=https://mikrotik.com/download
FileZilla | redes | 📂 | Cliente FTP y SFTP | fp=org.filezillaproject.Filezilla | n=*:filezilla
Remmina | redes | 🖥️ | Escritorio remoto RDP, VNC y SSH | fp=org.remmina.Remmina | n=*:remmina
PuTTY | redes | ⌨️ | Cliente SSH y serie | n=*:putty
Termius | redes | 🔐 | Cliente SSH moderno | fp=com.termius.Termius
Nmap | redes | 🛰️ | Escáner de red y puertos | n=*:nmap
Tailscale | redes | 🧵 | VPN en malla fácil de usar | n=arch:tailscale | dl=https://tailscale.com/download/linux
Discord | internet | 💬 | Chat de voz, video y texto para comunidades | fp=com.discordapp.Discord | n=arch:discord | dl=https://discord.com/download
Telegram | internet | ✈️ | Mensajería rápida y cifrada | fp=org.telegram.desktop | n=arch:telegram-desktop,debian:telegram-desktop,suse:telegram-desktop
Signal | internet | 🔒 | Mensajería privada | fp=org.signal.Signal | n=arch:signal-desktop
Slack | internet | 💼 | Comunicación para equipos | fp=com.slack.Slack | n=arch:aur:slack-desktop
Zoom | internet | 📹 | Videoconferencias y clases virtuales | fp=us.zoom.Zoom | n=arch:aur:zoom | dl=https://zoom.us/download
Microsoft Teams | internet | 👥 | Reuniones y chat de Microsoft | fp=com.github.IsmaelMartinez.teams_for_linux | web=https://teams.microsoft.com
Element | internet | 🔗 | Chat descentralizado con Matrix | fp=im.riot.Riot
Thunderbird | internet | 📧 | Cliente de correo de Mozilla | fp=org.mozilla.Thunderbird | n=*:thunderbird
Firefox | internet | 🦊 | Navegador libre de Mozilla | fp=org.mozilla.firefox | n=arch:firefox,fedora:firefox,suse:MozillaFirefox
Google Chrome | internet | 🌐 | Navegador de Google | fp=com.google.Chrome | n=arch:aur:google-chrome | dl=https://www.google.com/chrome/
Chromium | internet | 🔵 | Navegador de código abierto base de Chrome | fp=org.chromium.Chromium | n=*:chromium
Brave | internet | 🦁 | Navegador privado con bloqueo de anuncios | fp=com.brave.Browser | n=arch:aur:brave-bin | dl=https://brave.com/linux/
Vivaldi | internet | 🎻 | Navegador muy personalizable | fp=com.vivaldi.Vivaldi | n=arch:vivaldi | dl=https://vivaldi.com/download/
Opera | internet | 🅾️ | Navegador con VPN integrada | fp=com.opera.Opera
Microsoft Edge | internet | 🌊 | Navegador de Microsoft | fp=com.microsoft.Edge | n=arch:aur:microsoft-edge-stable-bin | dl=https://www.microsoft.com/edge/download
LibreWolf | internet | 🐺 | Firefox endurecido para privacidad | fp=io.gitlab.librewolf-community | n=arch:aur:librewolf-bin
Tor Browser | internet | 🧅 | Navegación anónima | fp=org.torproject.torbrowser-launcher | n=arch:torbrowser-launcher,debian:torbrowser-launcher,fedora:torbrowser-launcher
qBittorrent | internet | 🧲 | Cliente BitTorrent sin anuncios | fp=org.qbittorrent.qBittorrent | n=*:qbittorrent
Transmission | internet | 📥 | Cliente BitTorrent ligero | fp=com.transmissionbt.Transmission | n=arch:transmission-gtk,debian:transmission-gtk,fedora:transmission,suse:transmission-gtk
WhatsApp | internet | 🟢 | Mensajería de WhatsApp Web | web=https://web.whatsapp.com
Messenger | internet | 💙 | Chat de Facebook Messenger | web=https://www.messenger.com
Gmail | internet | ✉️ | Correo de Google | web=https://mail.google.com
Outlook | internet | 📬 | Correo y calendario de Microsoft | web=https://outlook.live.com
Google Meet | internet | 🎥 | Videollamadas de Google | web=https://meet.google.com
ChatGPT | internet | 🤖 | Asistente de IA de OpenAI | web=https://chatgpt.com
Claude | internet | ✨ | Asistente de IA de Anthropic | web=https://claude.ai
Gemini | internet | ♊ | Asistente de IA de Google | web=https://gemini.google.com
Flatseal | utiles | 🛡️ | Gestiona los permisos de tus Flatpak | fp=com.github.tchx84.Flatseal
Warehouse | utiles | 📦 | Administra apps Flatpak y sus datos | fp=io.github.flattool.Warehouse
Gear Lever | utiles | ⚙️ | Integra AppImages en tu sistema | fp=it.mijorus.gearlever
Bitwarden | utiles | 🔑 | Gestor de contraseñas | fp=com.bitwarden.desktop | n=arch:bitwarden
KeePassXC | utiles | 🗝️ | Gestor de contraseñas local | fp=org.keepassxc.KeePassXC | n=*:keepassxc
Mission Center | utiles | 📊 | Monitor del sistema moderno | fp=io.missioncenter.MissionCenter
Resources | utiles | 📉 | Monitor de recursos para GNOME | fp=net.nokyan.Resources | n=arch:resources
Pika Backup | utiles | 💾 | Copias de seguridad sencillas | fp=org.gnome.World.PikaBackup | n=arch:pika-backup,debian:pika-backup,fedora:pika-backup
Timeshift | utiles | ⏪ | Puntos de restauración del sistema | n=arch:timeshift,debian:timeshift,fedora:timeshift
BleachBit | utiles | 🧹 | Limpia archivos innecesarios | fp=org.bleachbit.BleachBit | n=*:bleachbit
GParted | utiles | 💽 | Editor de particiones | n=*:gparted
Balena Etcher | utiles | 💿 | Crea USB booteables | n=arch:aur:etcher-bin | dl=https://etcher.balena.io
VirtualBox | utiles | 📦 | Máquinas virtuales | n=arch:virtualbox,debian:virtualbox,suse:virtualbox
GNOME Boxes | utiles | 🗃️ | Máquinas virtuales simples | fp=org.gnome.Boxes | n=*:gnome-boxes
KDE Connect | utiles | 📱 | Conecta tu móvil con el PC | fp=org.kde.kdeconnect | n=*:kdeconnect
AnyDesk | utiles | 🖱️ | Escritorio remoto | n=arch:aur:anydesk-bin | dl=https://anydesk.com/en/downloads/linux
RustDesk | utiles | 🖥️ | Escritorio remoto de código abierto | fp=com.rustdesk.RustDesk | n=arch:aur:rustdesk-bin
TeamViewer | utiles | 🛰️ | Soporte y escritorio remoto | fp=com.teamviewer.TeamViewer | n=arch:aur:teamviewer | dl=https://www.teamviewer.com/download/linux/
OpenRGB | utiles | 🌈 | Control de iluminación RGB | fp=org.openrgb.OpenRGB | n=arch:openrgb
Solaar | utiles | 🖱️ | Gestiona dispositivos Logitech | fp=io.github.pwr_solaar.solaar | n=*:solaar
Cryptomator | utiles | 🔐 | Cifra tus archivos en la nube | fp=org.cryptomator.Launcher | n=arch:cryptomator
btop | utiles | 📈 | Monitor de recursos en terminal | n=*:btop
htop | utiles | 📊 | Monitor de procesos en terminal | n=*:htop
GeoGebra | educacion | 📐 | Matemáticas dinámicas: álgebra, geometría, cálculo | fp=org.geogebra.GeoGebra
Stellarium | educacion | 🔭 | Planetario virtual | fp=org.stellarium.Stellarium | n=*:stellarium
Anki | educacion | 🧠 | Tarjetas de estudio con repetición espaciada | fp=net.ankiweb.Anki | n=arch:anki
GNU Octave | educacion | 🧮 | Cálculo numérico compatible con MATLAB | fp=org.octave.Octave | n=*:octave
Kalzium | educacion | ⚗️ | Tabla periódica interactiva | fp=org.kde.kalzium | n=*:kalzium
Scratch | educacion | 🐱 | Aprende a programar con bloques | web=https://scratch.mit.edu
Tinkercad | educacion | 🧱 | Diseño 3D y circuitos para empezar | web=https://www.tinkercad.com
Wokwi | educacion | 🔌 | Simula Arduino, ESP32 y Raspberry Pi | web=https://wokwi.com
CircuitJS | educacion | ⚡ | Simulador de circuitos electrónicos | web=https://www.falstad.com/circuit/circuitjs.html
MATLAB Online | educacion | 🧪 | MATLAB en el navegador | web=https://matlab.mathworks.com
Desmos | educacion | 📉 | Calculadora gráfica online | web=https://www.desmos.com/calculator
WolframAlpha | educacion | 🧠 | Motor de conocimiento computacional | web=https://www.wolframalpha.com
Duolingo | educacion | 🦉 | Aprende idiomas jugando | web=https://www.duolingo.com
Google Classroom | educacion | 🏫 | Clases y tareas en línea | web=https://classroom.google.com
Drift | multimedia | 🌀 | Editor de video de escritorio gratuito y fácil (Qt 6 + FFmpeg) | fp=org.cutwire.Drift | dl=https://github.com/CutWire-Studios/Drift/releases/latest | by=CutWire Studios | note=Si no sale la opción Flatpak, descarga desde GitHub el AppImage, el Flatpak o el paquete para Arch.
Concat | multimedia | ✂️ | Editor de video libre y multiplataforma, alternativa a CapCut | dl=https://github.com/jub0t/concat/releases/latest | by=jub0t | note=En GitHub hay paquetes .deb, .rpm, .pkg.tar.zst (Arch) y AppImage.
ArtCraft Launcher | utiles | 🚀 | Instala y actualiza toda la suite de apps Craft | dl=https://github.com/storytold/craft-launcher/releases/latest | by=ArtCraft (Storytold) | note=Descarga el AppImage (x86_64 o ARM64), dale permisos de ejecución y úsalo para instalar FilmCraft, WordCraft y el resto de apps Craft.
ArtCraft | diseno | 🎨 | Estudio de imágenes y video con IA para artistas | dl=https://getartcraft.com | by=ArtCraft (Storytold)
FilmCraft | multimedia | 🎞️ | Editor de video profesional no lineal hecho en Rust | dl=https://getartcraft.com/apps/filmcraft | by=ArtCraft (Storytold) | note=En desarrollo. Hay AppImage, Flatpak, .deb, .rpm y tarball en su página, o instálalo con ArtCraft Launcher.
WordCraft | oficina | 📝 | Procesador de textos de código abierto de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
GridCraft | oficina | 🧮 | Hojas de cálculo y análisis de datos de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
DeckCraft | oficina | 📽️ | Presentaciones y diapositivas de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
PdfCraft | oficina | 📑 | Lee, une, divide y protege PDFs | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=Versión alfa. Instálalo con ArtCraft Launcher.
PhotoCraft | diseno | 📸 | Edición de imágenes de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=Versión alfa. Instálalo con ArtCraft Launcher.
VectorCraft | diseno | 🖋️ | Ilustración vectorial de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
LightCraft | diseno | 🌅 | Biblioteca de fotos y revelado RAW | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
DesignCraft | diseno | 📐 | Maquetación y publicación de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
FigCraft | diseno | 🧩 | Diseño de interfaces y prototipos | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=Versión alfa. Instálalo con ArtCraft Launcher.
CADCraft | diseno | 🏗️ | CAD 2D y 3D de la suite Craft | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
TextureCraft | diseno | 🖼️ | Pintura de texturas 3D | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
MaterialCraft | diseno | 🧱 | Materiales procedurales por nodos | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
EffectCraft | multimedia | ✨ | Motion graphics y efectos visuales | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
SoundCraft | multimedia | 🔊 | Grabación, edición y mezcla de audio | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=En desarrollo. Instálalo con ArtCraft Launcher.
Craft Anything | dev | 🛠️ | Explora cómo funciona el software y crea tu propia versión | dl=https://getartcraft.com/apps | by=ArtCraft (Storytold) | note=Versión alfa. Instálalo con ArtCraft Launcher.
Avidemux | multimedia | 🎬 | Cortar, filtrar y recodificar video rápido | fp=org.avidemux.Avidemux | n=arch:avidemux-qt,debian:avidemux,fedora:avidemux
Natron | multimedia | 🌌 | Composición de efectos visuales por nodos | fp=fr.natron.Natron
Synfig Studio | multimedia | 🎞️ | Animación 2D vectorial | fp=org.synfig.SynfigStudio | n=*:synfigstudio
Pencil2D | multimedia | ✏️ | Animación 2D tradicional sencilla | fp=org.pencil2d.Pencil2D | n=*:pencil2d
OpenToonz | multimedia | 🎨 | Animación 2D profesional | fp=io.github.opentoonz.OpenToonz
Kooha | multimedia | ⏺️ | Grabador de pantalla simple | fp=io.github.seadve.Kooha | n=arch:kooha,fedora:kooha
SimpleScreenRecorder | multimedia | 🔴 | Grabador de pantalla ligero | n=*:simplescreenrecorder
Strawberry | multimedia | 🍓 | Reproductor de música para tu colección | fp=org.strawberrymusicplayer.strawberry | n=*:strawberry
Lollypop | multimedia | 🍭 | Reproductor de música para GNOME | fp=org.gnome.Lollypop | n=arch:lollypop,debian:lollypop,fedora:lollypop
Amberol | multimedia | 🎧 | Reproductor de música minimalista | fp=io.bassi.Amberol | n=arch:amberol,fedora:amberol
Celluloid | multimedia | 🎞️ | Reproductor de video basado en mpv | fp=io.github.celluloid_player.Celluloid | n=*:celluloid
Haruna | multimedia | 🎦 | Reproductor de video de KDE | fp=org.kde.haruna | n=arch:haruna,fedora:haruna
Parabolic | multimedia | ⬇️ | Descarga videos y audio de internet | fp=org.nickvision.tubeconverter
Plex | multimedia | 📀 | Tu biblioteca multimedia en cualquier pantalla | fp=tv.plex.PlexDesktop
Ocenaudio | multimedia | 🌊 | Editor de audio fácil y rápido | n=arch:aur:ocenaudio-bin | dl=https://www.ocenaudio.com/download
Reaper | multimedia | 🎚️ | Estación de audio digital completa | n=arch:reaper | dl=https://www.reaper.fm/download.php
Spotify Web | multimedia | 🟢 | Reproductor web de Spotify | web=https://open.spotify.com
Tidal | multimedia | 🌊 | Música en alta calidad | web=https://listen.tidal.com
digiKam | diseno | 🗃️ | Gestor profesional de fotografías | fp=org.kde.digikam | n=*:digikam
Gwenview | diseno | 🖼️ | Visor de imágenes de KDE | fp=org.kde.gwenview | n=*:gwenview
Pencil Project | diseno | 📋 | Prototipado de interfaces y diagramas | fp=com.evolus.Pencil
Figma Linux | diseno | 🎨 | Cliente de escritorio no oficial de Figma | fp=io.github.Figma_Linux.figma_linux
Gravit Designer | diseno | 🖌️ | Diseño vectorial en el navegador | web=https://designer.gravit.io
Pixlr | diseno | 🪄 | Editor de fotos online | web=https://pixlr.com
Remove.bg | diseno | ✂️ | Quita fondos de imágenes con IA | web=https://www.remove.bg
Unsplash | diseno | 📷 | Fotos gratuitas de alta calidad | web=https://unsplash.com
Pinterest | diseno | 📌 | Inspiración visual e ideas | web=https://www.pinterest.com
Collabora Office | oficina | 🧾 | Suite de oficina basada en LibreOffice | fp=com.collabora.Office
Evince | oficina | 📄 | Visor de documentos de GNOME | fp=org.gnome.Evince | n=*:evince
PDF Arranger | oficina | 🗂️ | Une, divide y reordena PDFs | fp=com.github.jeromerobert.pdfarranger | n=arch:pdfarranger,debian:pdfarranger,fedora:pdfarranger
Master PDF Editor | oficina | 📕 | Edita PDFs de forma completa | dl=https://code-industry.net/get-master-pdf-editor-for-linux/
Apostrophe | oficina | ✍️ | Editor de Markdown limpio y sin distracciones | fp=org.gnome.gitlab.somas.Apostrophe | n=arch:apostrophe,fedora:apostrophe
Ghostwriter | oficina | 📝 | Editor de Markdown para escribir sin distracciones | fp=org.kde.ghostwriter | n=*:ghostwriter
MarkText | oficina | ✒️ | Editor de Markdown con vista previa en vivo | fp=com.github.marktext.marktext
Standard Notes | oficina | 🔏 | Notas privadas y cifradas | fp=org.standardnotes.standardnotes
Super Productivity | oficina | ✅ | Tareas y seguimiento del tiempo | fp=com.super_productivity.SuperProductivity
Evolution | oficina | 📆 | Correo, calendario y contactos | fp=org.gnome.Evolution | n=*:evolution
Google Calendar | oficina | 📅 | Calendario de Google | web=https://calendar.google.com
Google Keep | oficina | 🗒️ | Notas rápidas de Google | web=https://keep.google.com
Google Photos | oficina | 🖼️ | Fotos en la nube de Google | web=https://photos.google.com
Google Maps | oficina | 🗺️ | Mapas y rutas | web=https://maps.google.com
Overleaf | oficina | 🧮 | Editor LaTeX colaborativo online | web=https://www.overleaf.com
Miro | oficina | 🟡 | Pizarra colaborativa online | web=https://miro.com
Slack Web | oficina | 💼 | Slack en el navegador | web=https://app.slack.com
Zoom Web | oficina | 📹 | Reuniones de Zoom en el navegador | web=https://app.zoom.us/wc
ATLauncher | juegos | 🧊 | Lanzador de modpacks de Minecraft | fp=com.atlauncher.ATLauncher
Modrinth App | juegos | 🟩 | Lanzador de Minecraft con mods de Modrinth | fp=com.modrinth.ModrinthApp
TLauncher | juegos | ⛏️ | Lanzador de Minecraft gratuito | dl=https://tlauncher.org/en/
Minigalaxy | juegos | 🌌 | Cliente simple de GOG para Linux | fp=io.github.sharkwouter.Minigalaxy | n=arch:aur:minigalaxy
Cartridges | juegos | 🗃️ | Biblioteca unificada de todos tus juegos | fp=page.kramo.Cartridges | n=arch:cartridges
Ludusavi | juegos | 💾 | Copias de seguridad de partidas guardadas | fp=com.github.mtkennerly.ludusavi
SuperTux | juegos | 🐧 | Plataformas al estilo Super Mario | fp=org.supertuxproject.SuperTux | n=*:supertux
Warzone 2100 | juegos | 🪖 | Estrategia en tiempo real postapocalíptica | fp=net.wz2100.wz2100 | n=*:warzone2100
OpenRA | juegos | 🛡️ | Recrea Command & Conquer y Red Alert | fp=net.openra.OpenRA
Hedgewars | juegos | 🦔 | Guerra de erizos por turnos | fp=org.hedgewars.Hedgewars | n=*:hedgewars
GNOME Mines | juegos | 💣 | Buscaminas clásico | fp=org.gnome.Mines | n=*:gnome-mines
GNOME Sudoku | juegos | 🔢 | Sudoku para relajarte | fp=org.gnome.Sudoku | n=arch:gnome-sudoku,debian:gnome-sudoku,fedora:gnome-sudoku
Fortnite (Epic Games) | juegos | 🪂 | Juega a través de Heroic o la tienda de Epic | web=https://www.fortnite.com
Roblox | juegos | 🧱 | Mundos y juegos creados por la comunidad | web=https://www.roblox.com
Minecraft | juegos | ⛏️ | Sitio oficial y descargas | dl=https://www.minecraft.net/download
Epic Games Store | juegos | 🛒 | Tienda de juegos de Epic | web=https://store.epicgames.com
GOG | juegos | 🟣 | Juegos sin DRM | web=https://www.gog.com
Itch Web | juegos | 🎮 | Juegos indie en el navegador | web=https://itch.io
Wine | utiles | 🍷 | Capa de compatibilidad para programas de Windows | n=arch:wine,debian:wine,fedora:wine,suse:wine
Webex | internet | 🎥 | Reuniones y clases de Cisco | fp=com.cisco.Webex | web=https://web.webex.com | by=Cisco
Vesktop | internet | 💜 | Cliente de Discord con mejoras y Vencord | fp=dev.vencord.Vesktop | n=arch:aur:vesktop
Ferdium | internet | 🗂️ | Todas tus apps de mensajería en una | fp=org.ferdium.Ferdium
WhatsApp para Linux | internet | 🟢 | Cliente de escritorio no oficial de WhatsApp | fp=com.github.eneshecan.WhatsAppForLinux
Zen Browser | internet | 🧘 | Navegador moderno basado en Firefox | fp=app.zen_browser.zen
Floorp | internet | 🌸 | Navegador personalizable basado en Firefox | fp=one.ablaze.floorp
Mullvad Browser | internet | 🔒 | Navegador centrado en la privacidad | fp=net.mullvad.MullvadBrowser
Ungoogled Chromium | internet | 🔵 | Chromium sin servicios de Google | fp=io.github.ungoogled_software.ungoogled_chromium
Instagram | internet | 📷 | Fotos y reels | web=https://www.instagram.com
TikTok | internet | 🎵 | Videos cortos | web=https://www.tiktok.com
X (Twitter) | internet | ✖️ | Red social de noticias en tiempo real | web=https://x.com
Reddit | internet | 👽 | Comunidades y foros | web=https://www.reddit.com
Facebook | internet | 🔵 | Red social | web=https://www.facebook.com
LinkedIn | internet | 💼 | Red profesional | web=https://www.linkedin.com
Telegram Web | internet | ✈️ | Telegram en el navegador | web=https://web.telegram.org
Discord Web | internet | 💬 | Discord en el navegador | web=https://discord.com/app
Bruno | dev | 🐶 | Cliente de APIs de código abierto | fp=com.usebruno.Bruno | n=arch:aur:bruno-bin
Beekeeper Studio | dev | 🐝 | Editor SQL moderno | fp=io.beekeeperstudio.Studio
Pulsar | dev | 🔆 | Editor hackeable sucesor de Atom | fp=dev.pulsar_edit.Pulsar
JetBrains Toolbox | dev | 🧰 | Gestor de IDEs de JetBrains | n=arch:aur:jetbrains-toolbox | dl=https://www.jetbrains.com/toolbox-app/
RStudio | dev | 📊 | IDE para R y ciencia de datos | n=arch:aur:rstudio-desktop-bin | dl=https://posit.co/download/rstudio-desktop/
Ollama | dev | 🦙 | Ejecuta modelos de IA en tu equipo | n=arch:ollama | dl=https://ollama.com/download
LM Studio | dev | 🧠 | Descarga y usa modelos de IA locales | dl=https://lmstudio.ai
Docker Desktop | dev | 🐳 | Docker con interfaz gráfica | dl=https://www.docker.com/products/docker-desktop/
Anaconda | dev | 🐍 | Python para ciencia de datos | dl=https://www.anaconda.com/download
CodePen | dev | ✏️ | Editor de front-end en el navegador | web=https://codepen.io
Google Colab | dev | 📓 | Jupyter en la nube con GPU gratis | web=https://colab.research.google.com
Angry IP Scanner | redes | 📡 | Escáner de IPs y puertos | dl=https://angryip.org/download/
Cisco Networking Academy | redes | 🎓 | Cursos y laboratorios de Cisco | web=https://www.netacad.com | by=Cisco
Mullvad VPN | redes | 🛡️ | VPN privada | fp=net.mullvad.MullvadVPN | n=arch:aur:mullvad-vpn-bin
Proton VPN | redes | 🔐 | VPN gratuita y privada | fp=com.protonvpn.www | dl=https://protonvpn.com/download-linux
Cisco Secure Client | redes | 🔒 | VPN corporativa de Cisco | dl=https://www.cisco.com/c/en/us/support/security/secure-client-5/series.html
Logisim Evolution | educacion | 🔣 | Simulador de circuitos lógicos digitales | dl=https://github.com/logisim-evolution/logisim-evolution/releases/latest
Fritzing | educacion | 🔌 | Diseña prototipos electrónicos | fp=org.fritzing.Fritzing | n=arch:fritzing,debian:fritzing,fedora:fritzing
SimulIDE | educacion | ⚡ | Simulador de circuitos con microcontroladores | dl=https://simulide.com
GCompris | educacion | 🧒 | Actividades educativas para niños | fp=org.kde.gcompris | n=*:gcompris
Tux Paint | educacion | 🎨 | Dibujo para niños | fp=org.tuxpaint.TuxPaint | n=*:tuxpaint
Celestia | educacion | 🌍 | Explora el universo en 3D | fp=space.celestia.Celestia | n=arch:celestia,debian:celestia,fedora:celestia
Marble | educacion | 🗺️ | Atlas y globo virtual de KDE | fp=org.kde.marble | n=*:marble
KTurtle | educacion | 🐢 | Aprende a programar con la tortuga Logo | fp=org.kde.kturtle | n=*:kturtle
wxMaxima | educacion | ∑ | Álgebra computacional con Maxima | fp=io.github.wxmaxima_developers.wxMaxima | n=*:wxmaxima
Khan Academy | educacion | 🎓 | Lecciones gratuitas de todas las materias | web=https://www.khanacademy.org
Coursera | educacion | 🏛️ | Cursos universitarios online | web=https://www.coursera.org
Platzi | educacion | 🚀 | Cursos de tecnología en español | web=https://platzi.com
Impression | utiles | 💿 | Crea USB booteables fácilmente | fp=io.gitlab.adhami3310.Impression | n=arch:impression
Fedora Media Writer | utiles | 🎩 | Escribe imágenes ISO en USB | fp=org.fedoraproject.MediaWriter | n=*:mediawriter
Ventoy | utiles | 💽 | USB multi-ISO booteable | dl=https://www.ventoy.net/en/download.html
LocalSend | utiles | 📲 | Envía archivos entre dispositivos sin internet | fp=org.localsend.localsend_app | n=arch:aur:localsend-bin
Warp | utiles | 🌀 | Transfiere archivos de forma segura | fp=app.drey.Warp | n=arch:warp,fedora:warp
Nextcloud | utiles | ☁️ | Sincroniza tus archivos en la nube | fp=com.nextcloud.desktopclient.nextcloud | n=arch:nextcloud-client,debian:nextcloud-desktop,fedora:nextcloud-client
MEGA | utiles | 🔴 | Almacenamiento cifrado en la nube | fp=nz.mega.MEGAsync | dl=https://mega.io/desktop
Dropbox | utiles | 📦 | Sincroniza y comparte archivos | n=arch:aur:dropbox | dl=https://www.dropbox.com/install-linux
Authenticator | utiles | 🔢 | Códigos de verificación en dos pasos | fp=com.belmoussaoui.Authenticator | n=arch:authenticator,fedora:authenticator
ClamTk | utiles | 🛡️ | Antivirus ClamAV con interfaz gráfica | fp=com.gitlab.davem.ClamTk | n=*:clamtk
Déjà Dup | utiles | 💾 | Copias de seguridad fáciles | fp=org.gnome.DejaDup | n=*:deja-dup
Ark | utiles | 🗜️ | Comprime y descomprime archivos | fp=org.kde.ark | n=*:ark
PeaZip | utiles | 🗜️ | Gestor de archivos comprimidos | fp=io.github.peazip.PeaZip
Cheese | utiles | 📷 | Webcam y fotos divertidas | fp=org.gnome.Cheese | n=*:cheese
Proton Mail | utiles | ✉️ | Correo cifrado | web=https://mail.proton.me
Speedtest | utiles | ⚡ | Mide la velocidad de tu internet | web=https://www.speedtest.net
"""


@dataclass
class Source:
    kind: str            # native | aur | flatpak | web | download
    label: str
    pkg: str = ""        # paquete / id Flatpak
    url: str = ""
    official: bool = True
    note: str = ""


@dataclass
class App:
    key: str
    name: str
    cat: str
    emoji: str
    desc: str
    fp: str = ""
    native: dict = field(default_factory=dict)
    web: str = ""
    dl: str = ""
    creator: str = ""
    screenshots: list[str] = field(default_factory=list)
    note: str = ""
    curated: bool = True
    _search: str = ""

    def __post_init__(self):
        self._search = f"{self.name} {self.desc} {self.fp} {self.key}".lower()

    @property
    def icon_key(self) -> str:
        return self.fp or self.key

    @property
    def icon_urls(self) -> list[str]:
        urls = []
        if self.fp:
            urls.append(f"flathub:{self.fp}")
        if self.web:
            host = re.sub(r"^https?://", "", self.web).split("/")[0]
            urls.append(f"https://www.google.com/s2/favicons?domain={host}&sz=128")
        return urls

    def sources(self, distro) -> list[Source]:
        out: list[Source] = []
        pm = distro.pm_label
        if not distro.immutable:
            pkg = self.native.get(distro.family)
            if pkg:
                if pkg.startswith("aur:"):
                    out.append(Source("aur", "Nativo · AUR", pkg[4:], official=False,
                                      note="Paquete de la comunidad (se compila con yay/paru)."))
                else:
                    out.append(Source("native", f"Nativo · {pm}", pkg,
                                      note=f"Paquete de los repositorios de {distro.name}."))
        if self.fp:
            out.append(Source("flatpak", "Flatpak · Flathub", self.fp,
                              note="Corre aislado en su propio contenedor; funciona igual en cualquier distro."))
        if self.web:
            out.append(Source("web", "Web app", url=self.web,
                              note="Se abre en tu navegador; puedes crear un acceso directo como si fuera una app."))
        if self.dl:
            out.append(Source("download", "Descarga oficial", url=self.dl,
                              note="Instalador del sitio del fabricante."))
        return out


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _parse_native(spec: str) -> dict:
    out: dict = {}
    for part in spec.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        fam, pkg = part.split(":", 1)
        if fam == "*":
            for f in FAMILIES:
                out[f] = pkg
        else:
            out[fam] = pkg
    return out


def load_curated() -> list[App]:
    apps: list[App] = []
    seen: set[str] = set()
    for raw in _RAW.strip().splitlines():
        parts = [p.strip() for p in raw.split(" | ")]
        if len(parts) < 5:
            continue
        name, cat, emoji, desc, *extras = parts
        kv = {}
        for e in extras:
            if "=" in e:
                k, v = e.split("=", 1)
                kv[k.strip()] = v.strip()
        key = slug(name)
        if key in seen:
            continue
        seen.add(key)
        apps.append(App(key=key, name=name, cat=cat, emoji=emoji, desc=desc,
                        fp=kv.get("fp", ""), native=_parse_native(kv.get("n", "")),
                        web=kv.get("web", ""), dl=kv.get("dl", ""),
                        creator=kv.get("by", ""),
                        screenshots=[url for url in kv.get("img", "").split(";") if url],
                        note=kv.get("note", "")))
    order = {slug(n): i for i, n in enumerate(POPULAR)}
    apps.sort(key=lambda a: order.get(a.key, 10_000))
    return apps


_GUESS = [
    ("juegos", r"\b(game|juego|arcade|puzzle|emulator|racing|strategy|shooter|chess|minecraft|steam)\b"),
    ("multimedia", r"\b(video|audio|music|player|podcast|media|recorder|stream|movie|photo ?editor)\b"),
    ("diseno", r"\b(3d|cad|vector|draw|paint|graphic|design|image|photo|illustrat|render)\b"),
    ("dev", r"\b(ide|editor|developer|programming|code|git|debug|compiler|terminal|database|api)\b"),
    ("redes", r"\b(network|ssh|ftp|vpn|packet|wireless|wifi|remote desktop)\b"),
    ("internet", r"\b(browser|chat|mail|messag|torrent|irc|social|feed|rss)\b"),
    ("oficina", r"\b(office|document|pdf|spreadsheet|note|ebook|book|latex|todo|task)\b"),
    ("educacion", r"\b(learn|science|math|astronomy|education|language|chemistry|physics)\b"),
    ("utiles", r"\b(system|monitor|backup|disk|file manager|clipboard|password|utility|tool)\b"),
]


def guess_category(name: str, desc: str) -> str:
    text = f"{name} {desc}".lower()
    for cat, pat in _GUESS:
        if re.search(pat, text):
            return cat
    return "otros"


class Catalog:
    """Catálogo curado + todo Flathub (cuando está disponible)."""

    def __init__(self):
        self.curated = load_curated()
        self.apps: list[App] = list(self.curated)
        self.by_key = {a.key: a for a in self.apps}
        self.by_fp = {a.fp: a for a in self.apps if a.fp}
        self.flathub_loaded = False

    def merge_flathub(self, data: dict[str, tuple[str, str]]) -> int:
        """``data``: id Flatpak -> (nombre, descripción). Devuelve nº de apps añadidas."""
        if len(data) < 200:      # lista incompleta: no tocar nada
            return 0
        for app in self.curated:           # descarta ids Flatpak que no existen
            if app.fp and app.fp not in data:
                app.fp = ""
        self.by_fp = {a.fp: a for a in self.curated if a.fp}
        extras: list[App] = []
        for fid, (name, desc) in data.items():
            if fid in self.by_fp:
                continue
            name = name or fid.split(".")[-1]
            extras.append(App(key=f"fp-{fid.lower()}", name=name, cat=guess_category(name, desc),
                              emoji="", desc=desc or "Aplicación de Flathub", fp=fid, curated=False))
        extras.sort(key=lambda a: a.name.lower())
        self.apps = list(self.curated) + extras
        self.by_key = {a.key: a for a in self.apps}
        self.flathub_loaded = True
        return len(extras)

    def find(self, name: str) -> App | None:
        return self.by_key.get(slug(name))
