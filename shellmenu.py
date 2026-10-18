"""Entrées du clic droit de l'Explorateur : « Convertir en ▸ » et « Modifier l'image ▸ » (ou PDF, vidéo…).

Le nom DragShift n'apparaît pas au premier niveau : quelqu'un à qui on installe l'outil doit juste
voir « Convertir en ». Écrit dans HKCU (pas besoin des droits admin), seulement pour les extensions gérées.
Sous Windows 11 ces entrées sont dans « Afficher d'autres options » (ou directement avec Shift + clic droit).
"""
import ctypes
import os
import sys
import winreg
from pathlib import Path

import actions

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
    "pdf": "PDF", "jpg": "JPG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "ico": "ICO (icône)",
    "bmp": "BMP", "tiff": "TIFF", "docx": "Word (DOCX)", "odt": "LibreOffice (ODT)",
    "pptx": "PowerPoint (PPTX)", "odp": "LibreOffice Impress (ODP)", "xlsx": "Excel (XLSX)",
    "ods": "LibreOffice Calc (ODS)", "csv": "CSV", "txt": "Texte brut (TXT)", "md": "Markdown (MD)",
    "html": "Page web (HTML)", "mp4": "MP4", "webm": "WEBM", "mov": "MOV", "mkv": "MKV",
    "mp3": "MP3", "wav": "WAV", "flac": "FLAC", "ogg": "OGG", "m4a": "M4A", "opus": "OPUS",
}
VIDEO_LABELS = {"gif": "GIF animé", "mp3": "MP3 (audio seul)", "wav": "WAV (audio seul)"}
TOOLS_TITLE = {"image": "Modifier l'image", "pdf": "Modifier le PDF", "video": "Modifier la vidéo",
               "audio": "Modifier l'audio"}
TOOL_ORDER = ["crop", "compress", "addbg", "rembg", "rotate", "exif", "edit", "merge",
              "720p", "mute", "thumb", "normalize", "transcribe"]
TOOL_MENU = {
    "compress": "Compresser", "crop": "Rogner…", "addbg": "Ajouter un fond…",
    "rembg": "Détourer (enlever le fond)", "exif": "Supprimer les métadonnées", "rotate": "Pivoter de 90°",
    "edit": "Éditer dans Paint", "merge": "Fusionner les PDF sélectionnés", "mute": "Couper le son",
    "720p": "Réduire en 720p", "thumb": "Extraire une miniature", "transcribe": "Transcrire en sous-titres (.srt)",
    "normalize": "Normaliser le volume",
}


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


def install():
    uninstall(refresh=False)  # repart de zéro (formats/outils ont pu changer)
    for ext in _extensions():
        sample = f"x.{ext}"
        cat = actions.category(sample)
        shell = SHELL.format(ext=ext)

        conv = f"{shell}\\{VERB_CONVERT}"
        _set(conv, {"MUIVerb": "Convertir en", "Icon": str(ICON), "SubCommands": "",
                    "MultiSelectModel": "Player"})
        fmts = sorted(actions.formats_for(sample), key=FORMAT_ORDER.index)
        for f in fmts:
            label = (VIDEO_LABELS.get(f) if cat == "video" else None) or FORMAT_LABELS.get(f, f.upper())
            if f == "gif" and cat == "image":
                label = "GIF"
            _add_item(conv, f"f{FORMAT_ORDER.index(f):02d}_{f}", label, f"convert:{f}")
        # le menu radial reste accessible, mais tout en bas et discret
        _add_item(conv, "z_menu", "Menu rapide (DragShift)…", "menu", SEPARATOR_BEFORE, icon=True)

        tools = actions.tools_for([sample]) + (["merge"] if cat == "pdf" else [])
        if tools:
            tl = f"{shell}\\{VERB_TOOLS}"
            _set(tl, {"MUIVerb": TOOLS_TITLE[cat], "Icon": str(ICON), "SubCommands": "",
                      "MultiSelectModel": "Player"})
            for t in sorted(tools, key=TOOL_ORDER.index):
                _add_item(tl, f"t{TOOL_ORDER.index(t):02d}_{t}", TOOL_MENU[t], f"tool:{t}")
    _refresh_explorer()


def uninstall(refresh=True):
    for ext in _extensions():
        for verb in (VERB_CONVERT, VERB_TOOLS, *LEGACY_VERBS):
            _delete_tree(f"{SHELL.format(ext=ext)}\\{verb}")
    if refresh:
        _refresh_explorer()


def is_installed():
    try:
        winreg.OpenKey(HKCU, f"{SHELL.format(ext='png')}\\{VERB_CONVERT}").Close()
        return True
    except OSError:
        return False
