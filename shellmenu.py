"""Entrées du clic droit de l'Explorateur : « Convertir en ▸ » et « Modifier l'image ▸ » (ou PDF, vidéo…).

Le nom DragShift n'apparaît pas au premier niveau : quelqu'un à qui on installe l'outil doit juste
voir « Convertir en ». Deux mécanismes, mêmes entrées :
  - menu classique : verbes dans HKCU (Windows 10, et « Afficher d'autres options » sous Windows 11) ;
  - menu moderne de Windows 11 : paquet « sparse » + DragShiftMenu.dll (dossier winmenu/), qui lit
    winmenu/menu.tsv. Sans signature, Windows ne l'accepte qu'en mode développeur.
"""
import ctypes
import os
import subprocess
import sys
import winreg
from pathlib import Path

import actions
from i18n import LANG, tr

HERE = Path(__file__).resolve().parent
PIPE_NAME = f"DragShift-{os.environ.get('USERNAME', 'user')}"  # doit correspondre à send.pyw
ICON = HERE / "assets" / "dragshift.ico"
SHELL = r"Software\Classes\SystemFileAssociations\.{ext}\shell"
VERB_CONVERT, VERB_TOOLS = "DragShift.Convert", "DragShift.Tools"
LEGACY_VERBS = ("DragShift",)  # anciennes versions, nettoyées à l'installation
HKCU = winreg.HKEY_CURRENT_USER
SEPARATOR_BEFORE = 0x20  # ECF_SEPARATORBEFORE

# Ordre global des formats : le nom de sous-clé (ex. « f04_pdf ») désigne toujours la même action,
# quel que soit le type de fichier. Indispensable quand on sélectionne un PNG et un JPG ensemble.
FORMAT_ORDER = ["pdf", "jpg", "png", "webp", "avif", "gif", "ico", "bmp", "tiff",
                "docx", "odt", "pptx", "odp", "xlsx", "ods", "csv", "txt", "md", "html",
                "mp4", "webm", "mov", "mkv", "mp3", "wav", "flac", "ogg", "m4a", "opus"]
FORMAT_LABELS = {
    "pdf": "PDF", "jpg": "JPG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "ico": tr("ICO (icône)"),
    "bmp": "BMP", "tiff": "TIFF", "docx": "Word (DOCX)", "odt": "LibreOffice (ODT)",
    "pptx": "PowerPoint (PPTX)", "odp": "LibreOffice Impress (ODP)", "xlsx": "Excel (XLSX)",
    "ods": "LibreOffice Calc (ODS)", "csv": "CSV", "txt": tr("Texte brut (TXT)"), "md": "Markdown (MD)",
    "html": tr("Page web (HTML)"), "mp4": "MP4", "webm": "WEBM", "mov": "MOV", "mkv": "MKV",
    "mp3": "MP3", "wav": "WAV", "flac": "FLAC", "ogg": "OGG", "m4a": "M4A", "opus": "OPUS",
}
VIDEO_LABELS = {"gif": tr("GIF animé"), "mp3": tr("MP3 (audio seul)"), "wav": tr("WAV (audio seul)")}
TOOLS_TITLE = {"image": tr("Modifier l'image"), "pdf": tr("Modifier le PDF"), "video": tr("Modifier la vidéo"),
               "audio": tr("Modifier l'audio")}
TOOL_ORDER = ["crop", "compress", "addbg", "rembg", "rotate", "exif", "edit", "merge",
              "720p", "mute", "thumb", "normalize", "transcribe"]
TOOL_MENU = {k: tr(v) for k, v in {
    "compress": "Compresser", "crop": "Rogner…", "addbg": "Ajouter un fond…",
    "rembg": "Détourer (enlever le fond)", "exif": "Supprimer les métadonnées", "rotate": "Pivoter de 90°",
    "edit": "Éditer dans Paint", "merge": "Fusionner les PDF sélectionnés", "mute": "Couper le son",
    "720p": "Réduire en 720p", "thumb": "Extraire une miniature", "transcribe": "Transcrire en sous-titres (.srt)",
    "normalize": "Normaliser le volume",
}.items()}


def _pythonw():
    return str(Path(sys.executable).with_name("pythonw.exe"))


def _command(action):
    return f'"{_pythonw()}" "{HERE / "send.pyw"}" {action} "%1"'


def _extensions():
    return sorted(set().union(*actions.CATEGORIES.values()))


def _set(path, values):
    with winreg.CreateKeyEx(HKCU, path, 0, winreg.KEY_SET_VALUE) as k:
        for name, val in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD if isinstance(val, int) else winreg.REG_SZ, val)


def _add_item(verb_path, key, label, action, flags=0, icon=False):
    values = {"MUIVerb": label, "MultiSelectModel": "Player"}  # Player : pas de limite à 15 fichiers
    if flags:
        values["CommandFlags"] = flags
    if icon:
        values["Icon"] = str(ICON)
    _set(f"{verb_path}\\shell\\{key}", values)
    _set(f"{verb_path}\\shell\\{key}\\command", {"": _command(action)})


def _delete_tree(path):
    try:
        with winreg.OpenKey(HKCU, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(path + "\\" + sub)
        winreg.DeleteKey(HKCU, path)
    except FileNotFoundError:
        pass


def _refresh_explorer():
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED


def _menus(ext):
    """Entrées pour une extension : ([(clé, libellé, action)] formats, titre outils, [(clé, libellé, action)] outils)."""
    sample = f"x.{ext}"
    cat = actions.category(sample)
    conv = []
    for f in sorted(actions.formats_for(sample), key=FORMAT_ORDER.index):
        label = (VIDEO_LABELS.get(f) if cat == "video" else None) or FORMAT_LABELS.get(f, f.upper())
        if f == "gif" and cat == "image":
            label = "GIF"
        conv.append((f"f{FORMAT_ORDER.index(f):02d}_{f}", label, f"convert:{f}"))
    tools = actions.tools_for([sample]) + (["merge"] if cat == "pdf" else [])
    tl = [(f"t{TOOL_ORDER.index(t):02d}_{t}", TOOL_MENU[t], f"tool:{t}") for t in sorted(tools, key=TOOL_ORDER.index)]
    return conv, TOOLS_TITLE.get(cat), tl


def install():
    """Installe le menu classique, et le menu moderne sous Windows 11. Renvoie un message d'état."""
    uninstall(refresh=False)  # repart de zéro (formats/outils ont pu changer)
    for ext in _extensions():
        shell = SHELL.format(ext=ext)
        conv_items, tools_title, tool_items = _menus(ext)
        conv = f"{shell}\\{VERB_CONVERT}"
        _set(conv, {"MUIVerb": tr("Convertir en"), "Icon": str(ICON), "SubCommands": "",
                    "MultiSelectModel": "Player"})
        for key, label, action in conv_items:
            _add_item(conv, key, label, action)
        # le menu radial reste accessible, mais tout en bas et discret
        _add_item(conv, "z_menu", tr("Menu rapide (DragShift)…"), "menu", SEPARATOR_BEFORE, icon=True)
        if tool_items:
            tl = f"{shell}\\{VERB_TOOLS}"
            _set(tl, {"MUIVerb": tools_title, "Icon": str(ICON), "SubCommands": "",
                      "MultiSelectModel": "Player"})
            for key, label, action in tool_items:
                _add_item(tl, key, label, action)
    _refresh_explorer()
    return install_modern() if modern_supported() else tr("menu classique installé (Windows 10)")


# ------------------------------------------------------------------ menu moderne de Windows 11

WINMENU = HERE / "winmenu"
PACKAGE_VERSION = "1.3.0.0"
# Un paquet par entrée racine, chacun avec son éditeur et son nom : sinon Windows 11 regroupe les entrées
# sous un sous-menu « DragShift › » (constaté en test avec un seul paquet, puis avec deux paquets du même éditeur).
PACKAGES = [  # (nom, verbe, CLSID, éditeur, nom affiché)
    ("DragShift.Convert", "DragShiftConvert", "22E1DDD1-42FE-4A5F-B9B7-0A94AA9566F5",
     "CN=DragShift Convert", tr("DragShift (Convertir en)")),
    ("DragShift.Tools", "DragShiftTools", "8714DB24-1431-4FFF-82F5-E496EBF6393F",
     "CN=DragShift Tools", tr("DragShift (Modifier)")),
]
OLD_PACKAGES = ["DragShift.ContextMenu"]  # première version à un seul paquet


def modern_supported():
    return sys.getwindowsversion().build >= 22000


def _powershell(command):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=0x08000000)


def _ps_quote(path):
    return "'" + str(path).replace("'", "''") + "'"


def write_modern_config():
    """menu.tsv : ce que DragShiftMenu.dll affiche, et comment lancer DragShift."""
    lines = [f"#python\t{_pythonw()}", f"#send\t{HERE / 'send.pyw'}", f"#app\t{HERE / 'dragshift.pyw'}",
             f"#icon\t{ICON}"]
    for ext in _extensions():
        conv_items, tools_title, tool_items = _menus(ext)
        title = tr("Convertir en")
        for _, label, action in conv_items:
            lines.append(f"{ext}\tconvert\t{title}\t{action}\t{label}")
        lines.append(f"{ext}\tconvert\t{title}\t-\t-")  # séparateur
        lines.append(f"{ext}\tconvert\t{title}\tmenu\t{tr('Menu rapide (DragShift)…')}")
        for _, label, action in tool_items:
            lines.append(f"{ext}\ttools\t{tools_title}\t{action}\t{label}")
    (WINMENU / "menu.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def developer_mode():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock") as k:
            return winreg.QueryValueEx(k, "AllowDevelopmentWithoutDevLicense")[0] == 1
    except OSError:
        return False


def install_modern():
    write_modern_config()
    if not developer_mode():
        return tr("menu classique installé ; pour le menu moderne de Windows 11, active le mode développeur "
                  "(Paramètres > Système > Espace développeurs) puis relance l'installation")
    template = (WINMENU / "AppxManifest.template.xml").read_text(encoding="utf-8")
    for old in OLD_PACKAGES:
        _powershell(f"Get-AppxPackage {old} | Remove-AppxPackage")
    for name, verb, clsid, publisher, display in PACKAGES:
        # chaque paquet a son dossier (avec ses logos) ; DLL et exe sont partagés via -ExternalLocation
        pkg = WINMENU / "pkg" / name
        (pkg / "Assets").mkdir(parents=True, exist_ok=True)
        for logo in (WINMENU / "Assets").iterdir():
            (pkg / "Assets" / logo.name).write_bytes(logo.read_bytes())
        manifest = (template.replace("{NAME}", name).replace("{VERB}", verb).replace("{CLSID}", clsid)
                    .replace("{PUBLISHER}", publisher).replace("{DISPLAYNAME}", display)
                    .replace("{DESCRIPTION}", display).replace("{VERSION}", PACKAGE_VERSION)
                    .replace("{LANGUAGE}", "fr-FR" if LANG == "fr" else "en-US"))
        (pkg / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
        # en mode développeur, Windows refuse de réinscrire la même version si le manifeste a changé
        # (langue, nom affiché…) : on retire d'abord l'ancienne inscription
        r = _powershell(f"Get-AppxPackage {name} | Remove-AppxPackage; "
                        f"Add-AppxPackage -Register {_ps_quote(pkg / 'AppxManifest.xml')} "
                        f"-ExternalLocation {_ps_quote(WINMENU)} -ForceUpdateFromAnyVersion")
        if r.returncode != 0:
            return tr("menu moderne : échec de l'enregistrement de {name} : ", name=name) + \
                (r.stderr or r.stdout).strip()[-400:]
    return tr("menus classique et moderne installés")


def uninstall_modern():
    for name in [p[0] for p in PACKAGES] + OLD_PACKAGES:
        _powershell(f"Get-AppxPackage {name} | Remove-AppxPackage")


def is_modern_installed():
    r = _powershell(f"if (Get-AppxPackage {PACKAGES[0][0]}) {{ 'oui' }}")
    return r.stdout.strip() == "oui"


def uninstall(refresh=True, modern=False):
    for ext in _extensions():
        for verb in (VERB_CONVERT, VERB_TOOLS, *LEGACY_VERBS):
            _delete_tree(f"{SHELL.format(ext=ext)}\\{verb}")
    if modern:
        uninstall_modern()
    if refresh:
        _refresh_explorer()


def is_installed():
    try:
        winreg.OpenKey(HKCU, f"{SHELL.format(ext='png')}\\{VERB_CONVERT}").Close()
        return True
    except OSError:
        return False


def needs_refresh():
    """Menu installé dans une autre langue que l'interface (langue de Windows changée, DRAGSHIFT_LANG) ?"""
    try:
        with winreg.OpenKey(HKCU, f"{SHELL.format(ext='png')}\\{VERB_CONVERT}") as k:
            return winreg.QueryValueEx(k, "MUIVerb")[0] != tr("Convertir en")
    except OSError:
        return False
