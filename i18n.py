"""Langue de l'interface : français si Windows est en français, anglais sinon.

Les textes sont écrits en français dans le code ; `tr()` renvoie leur traduction anglaise.
DRAGSHIFT_LANG=fr ou en force la langue (le clic droit se met à jour au lancement suivant).
"""
import ctypes
import os


def _detect():
    forced = os.environ.get("DRAGSHIFT_LANG", "").strip().lower()[:2]
    if forced in ("fr", "en"):
        return forced
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return "en"
    return "fr" if langid & 0x3FF == 0x0C else "en"  # LANG_FRENCH, quel que soit le pays


LANG = _detect()

EN = {
    # menu radial
    "Bouge le fichier…": "Move the file…",
    "Aucun fichier local": "No local file",
    ".{ext} non géré": ".{ext} not supported",
    "{n} fichiers": "{n} files",
    "Outils": "Tools",
    "Formats": "Formats",
    "clic : {other}": "click: {other}",
    # outils (anneau du menu radial)
    "Compresser": "Compress",
    "Rogner": "Crop",
    "Fond": "Background",
    "Détourer": "Remove BG",
    "Rotation": "Rotate",
    "Éditer": "Edit",
    "Fusionner": "Merge",
    "Muet": "Mute",
    "Miniature": "Thumbnail",
    "Transcrire": "Transcribe",
    "Normaliser": "Normalize",
    # icône de la barre des tâches, notifications
    "glisse un fichier + Shift": "drag a file + Shift",
    "{n} tâche(s) en cours…": "{n} task(s) running…",
    "Activé": "Enabled",
    "Lancer au démarrage de Windows": "Start with Windows",
    "« Convertir en » au clic droit": "\"Convert to\" in the right-click menu",
    "Comment ça marche ?": "How does it work?",
    "Quitter": "Quit",
    "DragShift — mode d'emploi": "DragShift — how to use it",
    "1. Glisse un fichier + Shift, lâche-le sur un format.\n"
    "2. Réappuie sur Shift : outils (Rogner, Fond, Compresser…).\n"
    "3. Ou clic droit sur un fichier > Convertir en.":
        "1. Drag a file + Shift, drop it on a format.\n"
        "2. Press Shift again: tools (Crop, Background, Compress…).\n"
        "3. Or right-click a file > Convert to.",
    "{label} en cours : {name}": "{label}: working on {name}…",
    "{app} — échec": "{app} — failed",
    "{size} — clique pour afficher dans l'Explorateur": "{size} — click to show it in File Explorer",
    "Prêt. Glisse un fichier + Shift, ou clic droit sur un fichier > Convertir en.":
        "Ready. Drag a file + Shift, or right-click a file > Convert to.",
    "DragShift installé ({status}) + lancement au démarrage.":
        "DragShift installed ({status}) + starts with Windows.",
    "DragShift désinstallé (clic droit + démarrage auto). Le dossier peut être supprimé.":
        "DragShift uninstalled (right-click menu + autostart). You can now delete the folder.",
    # conversions
    "{tool} a échoué : ": "{tool} failed: ",
    "Type de fichier non géré": "Unsupported file type",
    "{stem} et {n} autre.pdf": "{stem} and {n} other.pdf",
    "{stem} et {n} autres.pdf": "{stem} and {n} others.pdf",
    "LibreOffice n'a rien produit": "LibreOffice produced no file",
    "Outil « {key} » indisponible pour .{ext}": "The \"{key}\" tool isn't available for .{ext}",
    "Fusion.pdf": "Merged.pdf",
    # clic droit
    "Convertir en": "Convert to",
    "Menu rapide (DragShift)…": "Quick menu (DragShift)…",
    "ICO (icône)": "ICO (icon)",
    "Texte brut (TXT)": "Plain text (TXT)",
    "Page web (HTML)": "Web page (HTML)",
    "GIF animé": "Animated GIF",
    "MP3 (audio seul)": "MP3 (audio only)",
    "WAV (audio seul)": "WAV (audio only)",
    "Modifier l'image": "Edit image",
    "Modifier le PDF": "Edit PDF",
    "Modifier la vidéo": "Edit video",
    "Modifier l'audio": "Edit audio",
    "Rogner…": "Crop…",
    "Ajouter un fond…": "Add a background…",
    "Détourer (enlever le fond)": "Remove background",
    "Supprimer les métadonnées": "Remove metadata",
    "Pivoter de 90°": "Rotate 90°",
    "Éditer dans Paint": "Edit in Paint",
    "Fusionner les PDF sélectionnés": "Merge selected PDFs",
    "Couper le son": "Mute",
    "Réduire en 720p": "Downscale to 720p",
    "Extraire une miniature": "Extract a thumbnail",
    "Transcrire en sous-titres (.srt)": "Transcribe to subtitles (.srt)",
    "Normaliser le volume": "Normalize volume",
    "DragShift (Convertir en)": "DragShift (Convert to)",
    "DragShift (Modifier)": "DragShift (Edit)",
    "menu classique installé (Windows 10)": "classic menu installed (Windows 10)",
    "menu classique installé ; pour le menu moderne de Windows 11, active le mode développeur "
    "(Paramètres > Système > Espace développeurs) puis relance l'installation":
        "classic menu installed; for the modern Windows 11 menu, turn on Developer Mode "
        "(Settings > System > For developers) and run the install again",
    "menu moderne : échec de l'enregistrement de {name} : ": "modern menu: couldn't register {name}: ",
    "menus classique et moderne installés": "classic and modern menus installed",
    # fenêtres
    "Rogner l'image": "Crop image",
    "Impossible d'ouvrir l'image": "Couldn't open the image",
    "Ratio": "Ratio",
    "Libre": "Free",
    "L": "W",
    "H": "H",
    "Réinitialiser": "Reset",
    "Appliquer": "Apply",
    "Échec de l'enregistrement": "Couldn't save the file",
    "Ajouter un fond": "Add a background",
    "Marge": "Padding",
    "Coins": "Corners",
    "Ombre": "Shadow",
    "Enregistrer avec le fond": "Save with background",
    "Convertir {n} images en PDF": "Convert {n} images to PDF",
    "Comment veux-tu les regrouper ?": "How do you want to group them?",
    "Un seul PDF": "One PDF",
    "{n} pages, une image par page": "{n} pages, one image per page",
    "Un PDF par image": "One PDF per image",
    "{n} fichiers PDF séparés": "{n} separate PDF files",
}


def tr(text, **fields):
    """Texte dans la langue de l'interface ; `fields` remplit les {champs}."""
    if LANG == "en":
        text = EN.get(text, text)
    return text.format(**fields) if fields else text
