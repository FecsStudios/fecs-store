# Fecs Store 2.0

## Instalar (usuarios)
Descarga **`Fecs_Store-X.Y.Z-x86_64.AppImage`** desde [Releases](../../releases/latest), dale doble clic y listo
(si no abre: clic derecho → Propiedades → «Permitir ejecutar»). No necesitas instalar Python ni nada más.
O con un solo comando (la añade al menú de aplicaciones):
```bash
curl -fsSL https://github.com/FecsStudios/fecs-store/releases/latest/download/install.sh | bash
```
Sirve en cualquier distro (Arch/CachyOS, Debian/Ubuntu, Fedora, openSUSE…). Si el sistema no tiene FUSE, el instalador lo detecta y lo resuelve solo.
Para desinstalar: `curl -fsSL https://github.com/FecsStudios/fecs-store/releases/latest/download/install.sh | bash -s -- --uninstall`

**Se actualiza sola:** al abrir la tienda avisa si hay versión nueva y la instala con un clic.

## Sacar una versión nueva (desarrollador)
```bash
git add -A && git commit -m "Cambios"
git tag v2.1.0 && git push origin main v2.1.0
```
GitHub Actions compila el AppImage, lo publica en Releases y todos los usuarios reciben el aviso.
La versión sale de la etiqueta (`v2.1.0`), no hace falta editar nada más.

---

Tienda de aplicaciones para Linux con **tres tipos de fuente** por app:

| Fuente | Qué hace |
|---|---|
| **Flatpak** | Instala desde Flathub (sin permisos de root, en tu usuario). |
| **Nativa** | Usa el gestor de TU distro: `pacman`/AUR (Arch, Manjaro, EndeavourOS…), `apt` (Debian, Ubuntu, Mint…), `dnf` (Fedora…), `zypper` (openSUSE). |
| **Web / Descarga** | Web apps con acceso directo en el menú (Photopea, Figma, Google Docs…) y descargas oficiales (Cisco Packet Tracer, DaVinci Resolve, OnlyOffice .deb/.rpm…). |

La tienda detecta la distro al abrir y muestra solo las versiones nativas que existen para ella.
En sistemas inmutables (Silverblue, Bazzite, SteamOS) ofrece solo Flatpak.

## Ejecutar desde el código fuente
```bash
pip install --user PyQt6        # o: sudo pacman -S python-pyqt6 / sudo apt install python3-pyqt6 / sudo dnf install python3-pyqt6
python3 main.py
./dev/install-desde-fuente.sh   # opcional: añade «Fecs Store» al menú de aplicaciones
```
Requisitos para instalar: `flatpak` (la tienda puede instalarlo), `pkexec`/polkit para paquetes nativos, y `yay` o `paru` para el AUR.

## Qué incluye
* ~360 apps curadas (incluye Drift, Concat y toda la suite Craft: ArtCraft Launcher, ArtCraft, FilmCraft, WordCraft…) (juegos, ofimática, video/audio, diseño/3D, desarrollo, redes, internet, educación, utilidades).
* **Todo Flathub** (miles de apps) cargado en segundo plano con `flatpak remote-ls`, con caché de 24 h.
* Buscador instantáneo (Ctrl+K), vista «Instaladas», «Actualizar todo», registro de operaciones, cola de tareas con progreso y cancelar.
* Preferencia de versión: Automática / Flatpak / Nativa.
* Al seleccionar una aplicación se abre su ficha con descripción, creador, imágenes disponibles e instalación.

## Efectos
* Tarjetas con halo y elevación al pasar el ratón, efecto de pulsación y entrada escalonada en cascada.
* Scroll suave con la rueda, iconos que aparecen con fundido.
* Banner con luces animadas, transición de color y puntos de navegación.
* Ficha que aparece con fundido y barra de tareas con destello al terminar.

## Rendimiento
* Lista virtualizada (solo se pinta lo visible) → fluida con miles de apps.
* El estado «instalado» se consulta **una vez** por gestor, no una vez por app.
* Nada bloquea la interfaz: Flathub, iconos e instalaciones corren en hilos.
* Los iconos se bajan de Flathub y se guardan en `~/.cache/fecs-store`.

## Añadir apps al catálogo
Edita `fecs/catalog.py`, bloque `_RAW`, una línea por app:
```
Nombre | categoría | emoji | descripción | fp=<id flathub> | n=arch:pkg,debian:pkg | web=<url> | dl=<url> | by=<creador> | img=<https-url>;<https-url>
```
`n=*:pkg` aplica el mismo paquete a arch/debian/fedora/suse; `arch:aur:pkg` marca un paquete del AUR.
Para las apps de Flathub, el creador y las capturas también se obtienen automáticamente cuando están publicados en sus metadatos.
