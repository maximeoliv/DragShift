"""Catalogue des formats/outils par type de fichier + exécution des conversions."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
HERE = Path(__file__).resolve().parent


def _find(name, *fallbacks):
    p = shutil.which(name)
    if p:
        return p
    for f in fallbacks:
        if Path(f).exists():
            return f
    return name


MAGICK = _find("magick", r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe")
FFMPEG = _find("ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe")
PANDOC = _find("pandoc")
SOFFICE = _find("soffice", r"C:\Program Files\LibreOffice\program\soffice.exe")
# pythonw.exe -> python.exe pour les helpers (rembg, whisper)
PYTHON = str(Path(sys.executable).with_name("python.exe"))

CATEGORIES = {
    "image": {"png", "jpg", "jpeg", "webp", "avif", "heic", "heif", "bmp", "tif", "tiff", "gif", "ico", "jxl"},
    "pdf": {"pdf"},
    "word": {"docx", "doc", "odt", "rtf"},
    "slides": {"pptx", "ppt", "odp"},
    "sheet": {"xlsx", "xls", "ods"},
    "text": {"md", "markdown", "txt", "html", "htm"},
    "video": {"mp4", "mov", "mkv", "webm", "avi", "m4v", "wmv", "flv"},
    "audio": {"mp3", "wav", "flac", "ogg", "m4a", "opus", "aac", "wma"},
}

# icônes Segoe Fluent / MDL2
ICONS = {
    "compress": "\uE73F", "crop": "\uE7A8", "addbg": "\uE790", "rembg": "\uE8B3",
    "exif": "\uE946", "rotate": "\uE7AD", "edit": "\uE70F", "merge": "\uE8A5",
    "mute": "\uE74F", "720p": "\uE740", "thumb": "\uE722", "transcribe": "\uE8C1",
    "normalize": "\uE767",
}

TOOL_LABELS = {
    "compress": "COMPRESSER", "crop": "ROGNER", "addbg": "FOND", "rembg": "DÉTOURER",
    "exif": "EXIF", "rotate": "ROTATION", "edit": "ÉDITER", "merge": "FUSIONNER",
    "mute": "MUET", "720p": "720P", "thumb": "MINIATURE", "transcribe": "TRANSCRIRE",
    "normalize": "NORMALISER",
}

INTERACTIVE = {"crop", "addbg"}


def ext_of(p):
    return Path(p).suffix.lower().lstrip(".")


def category(p):
    e = ext_of(p)
    for cat, exts in CATEGORIES.items():
        if e in exts:
            return cat
    return None


def _norm(e):
    return {"jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md", "heif": "heic"}.get(e, e)


def formats_for(p):
    cat, e = category(p), _norm(ext_of(p))
    lists = {
        "image": ["png", "jpg", "webp", "avif", "pdf", "ico", "bmp", "tiff", "gif"],
        "pdf": ["docx", "txt", "jpg", "png"],
        "word": ["pdf", "docx", "odt", "txt", "md", "html"] if e in ("docx", "odt") else ["pdf", "docx", "odt", "txt"],
        "slides": ["pdf", "pptx", "odp"],
        "sheet": ["pdf", "xlsx", "ods", "csv"],
        "text": ["docx", "pdf", "html", "md", "txt"],
        "video": ["mp4", "webm", "mov", "mkv", "gif", "mp3", "wav"],
        "audio": ["mp3", "wav", "flac", "ogg", "m4a", "opus"],
    }
    out = [f for f in lists.get(cat, []) if f != e]
    return out[:8]


def tools_for(paths):
    cat = category(paths[0])
    t = {
        "image": ["compress", "crop", "addbg", "rembg", "exif", "rotate", "edit"],
        "pdf": ["compress", "rotate"] + (["merge"] if sum(category(p) == "pdf" for p in paths) > 1 else []),
        "video": ["compress", "mute", "720p", "thumb", "transcribe"],
        "audio": ["normalize", "compress", "transcribe"],
    }
    return t.get(cat, [])


# ---------------------------------------------------------------- utilitaires

def unique(path):
    path = Path(path)
    if not path.exists():
        return path
    i = 2
    while True:
        c = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not c.exists():
            return c
        i += 1


def human(n):
    for u in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or u == "Go":
            return f"{n:.0f} {u}" if u == "o" else f"{n:.1f} {u}".replace(".0 ", " ")
        n /= 1024


def run(cmd, timeout=3600):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=NO_WINDOW, timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(f"{Path(str(cmd[0])).stem} a échoué : " + " | ".join(tail))
    return r


def out_path(src, suffix="", ext=None):
    src = Path(src)
    return unique(src.with_name(f"{src.stem}{suffix}.{ext or ext_of(src)}"))


# ---------------------------------------------------------------- conversions

def convert_many(paths, fmt):
    """Convertit chaque fichier pour lequel ce format a un sens (sélection mixte possible)."""
    outs = []
    for p in paths:
        if fmt in formats_for(p):
            outs += convert(p, fmt)
    return outs


def convert(src, fmt):
    src = Path(src)
    cat = category(src)
    if cat == "image":
        return [_image_to(src, fmt)]
    if cat == "pdf":
        return _pdf_to(src, fmt)
    if cat in ("word", "slides", "sheet"):
        return [_office_to(src, fmt)]
    if cat == "text":
        return [_text_to(src, fmt)]
    if cat in ("video", "audio"):
        return [_av_to(src, fmt)]
    raise RuntimeError("Type de fichier non géré")


def natural_key(p):
    """Tri « humain » : photo 2 avant photo 10."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).name)]


def images_to_pdf(paths, out=None):
    """Une ou plusieurs images -> un PDF, une page A4 par image (orientée selon l'image, prête à imprimer)."""
    import fitz
    paths = sorted((Path(p) for p in paths), key=natural_key)
    if out is None:
        first = paths[0]
        n = len(paths) - 1
        out = out_path(first, ext="pdf") if not n else \
            unique(first.with_name(f"{first.stem} et {n} autre{'s' if n > 1 else ''}.pdf"))
    doc = fitz.open()
    tmp = Path(tempfile.mkdtemp(prefix="dragshift-"))
    try:
        for i, p in enumerate(paths):
            src = p
            if ext_of(p) not in ("jpg", "jpeg", "png", "bmp", "gif", "tif", "tiff"):  # HEIC, AVIF, WEBP…
                src = tmp / f"{i}.png"
                run([MAGICK, f"{p}[0]", "-auto-orient", src])
            r = fitz.open(src)[0].rect  # taille de l'image (en points)
            w, h = r.width, r.height
            page = doc.new_page(width=842 if w > h else 595, height=595 if w > h else 842)
            page.insert_image(page.rect, filename=str(src), keep_proportion=True)
        doc.save(out, garbage=3, deflate=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _image_to(src, fmt):
    if fmt == "pdf":
        return images_to_pdf([src])
    out = out_path(src, ext=fmt)
    first = str(src) if (fmt == "gif" and ext_of(src) == "gif") else f"{src}[0]"
    cmd = [MAGICK, first, "-auto-orient"]
    if fmt in ("jpg", "bmp", "pdf"):
        cmd += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
    cmd += {
        "jpg": ["-quality", "92"],
        "webp": ["-quality", "90"],
        "avif": ["-quality", "70"],
        "ico": ["-resize", "256x256", "-background", "none", "-gravity", "center", "-extent", "256x256",
                "-define", "icon:auto-resize=256,128,64,48,32,16"],
    }.get(fmt, [])
    run(cmd + [out])
    return out


def _pdf_to(src, fmt):
    import fitz
    if fmt == "docx":
        from pdf2docx import Converter
        out = out_path(src, ext="docx")
        cv = Converter(str(src))
        try:
            cv.convert(str(out))
        finally:
            cv.close()
        return [out]
    doc = fitz.open(src)
    if fmt == "txt":
        out = out_path(src, ext="txt")
        out.write_text("\n\n".join(p.get_text() for p in doc), encoding="utf-8")
        return [out]
    outs = []
    for i, page in enumerate(doc):
        # 200 dpi, plafonné à 5000 px de côté (certains PDF ont des pages gigantesques)
        dpi = max(1, min(200, int(5000 * 72 / max(page.rect.width, page.rect.height))))
        pix = page.get_pixmap(dpi=dpi)
        o = out_path(src, ext=fmt) if len(doc) == 1 else out_path(src, f" - {i + 1:02d}", fmt)
        if fmt == "jpg":
            pix.save(str(o), jpg_quality=92)
        else:
            pix.save(str(o))
        outs.append(o)
    return outs


# profil LibreOffice dédié et persistant : sa création prend du temps au 1er lancement,
# et un profil séparé évite les conflits si LibreOffice est déjà ouvert.
_LO_PROFILE = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "DragShift" / "lo-profile"


def _soffice(src, filt, ext):
    tmp = Path(tempfile.mkdtemp(prefix="dragshift-"))
    try:
        run([SOFFICE, f"-env:UserInstallation={_LO_PROFILE.as_uri()}", "--headless",
             "--convert-to", filt, "--outdir", tmp, src], timeout=600)
        produced = list(tmp.glob(f"*.{ext}"))
        if not produced:
            raise RuntimeError("LibreOffice n'a rien produit")
        out = out_path(src, ext=ext)
        shutil.move(str(produced[0]), out)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _office_to(src, fmt):
    e = ext_of(src)
    if fmt in ("md", "html") and e in ("docx", "odt"):
        out = out_path(src, ext=fmt)
        args = ["-t", "gfm"] if fmt == "md" else ["-s", "--embed-resources"]
        run([PANDOC, src, *args, "-o", out])
        return out
    filt = {"txt": "txt:Text (encoded):UTF8", "csv": "csv:Text - txt - csv (StarCalc):44,34,76"}.get(fmt, fmt)
    return _soffice(src, filt, fmt)


def _text_to(src, fmt):
    e = _norm(ext_of(src))
    src_fmt = {"md": "gfm", "txt": "markdown", "html": "html"}[e]
    if fmt == "pdf":
        tmp = Path(tempfile.mkdtemp(prefix="dragshift-"))
        try:
            mid = tmp / f"{src.stem}.docx"
            run([PANDOC, "-f", src_fmt, src, "-o", mid])
            out = _soffice(mid, "pdf", "pdf")  # écrit à côté de mid -> on déplace
            final = out_path(src, ext="pdf")
            shutil.move(str(out), final)
            return final
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    out = out_path(src, ext=fmt)
    args = {"md": ["-t", "gfm"], "txt": ["-t", "plain"], "html": ["-s"], "docx": []}[fmt]
    run([PANDOC, "-f", src_fmt, src, *args, "-o", out])
    return out


AV_ARGS = {
    "mp4": ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus", "-b:a", "128k"],
    "mkv": ["-map", "0", "-c", "copy"],
    "gif": ["-vf", "fps=15,scale='min(720,iw)':-2:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-an"],
    "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "0"],
    "wav": ["-vn", "-c:a", "pcm_s16le"],
    "flac": ["-vn", "-c:a", "flac"],
    "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "6"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "256k"],
    "opus": ["-vn", "-c:a", "libopus", "-b:a", "160k"],
}


def _ff(src, args, out):
    run([FFMPEG, "-hide_banner", "-y", "-i", src, *args, out], timeout=6 * 3600)
    return out


def _av_to(src, fmt):
    return _ff(src, AV_ARGS[fmt], out_path(src, ext=fmt))


# ---------------------------------------------------------------- outils

def tool(paths, key):
    """Outils non interactifs. Renvoie la liste des fichiers produits."""
    if key == "merge":
        return [_pdf_merge([p for p in paths if category(p) == "pdf"])]
    outs = []
    for p in paths:
        p = Path(p)
        if category(p) != category(paths[0]):
            continue
        outs += _tool_one(p, key)
    return outs


def _tool_one(src, key):
    cat, e = category(src), ext_of(src)
    if cat == "image":
        if key == "compress":
            out = out_path(src, " Compressed")
            q = {"jpg": "75", "jpeg": "75", "webp": "75", "avif": "55"}.get(e)
            cmd = [MAGICK, src, "-auto-orient", "-strip", "-resize", "2560x2560>"]
            cmd += ["-quality", q] if q else ["-define", "png:compression-level=9"]
            run(cmd + [out])
            return [out]
        if key == "exif":
            out = out_path(src, " Clean")
            run([MAGICK, src, "-auto-orient", "-strip", out])
            return [out]
        if key == "rotate":
            out = out_path(src, " Rotated")
            run([MAGICK, src, "-auto-orient", "-rotate", "90", out])
            return [out]
        if key == "rembg":
            out = out_path(src, " No BG", "png")
            run([PYTHON, "-I", HERE / "helpers.py", "rembg", src, out])
            return [out]
        if key == "edit":
            subprocess.Popen(["mspaint", str(src)])
            return []
    if cat == "pdf":
        import fitz
        doc = fitz.open(src)
        if key == "compress":
            out = out_path(src, " Compressed")
            try:
                doc.rewrite_images(dpi_threshold=160, dpi_target=130, quality=70)
            except Exception:
                pass
            doc.save(out, garbage=4, deflate=True, clean=True)
            return [out]
        if key == "rotate":
            out = out_path(src, " Rotated")
            for page in doc:
                page.set_rotation((page.rotation + 90) % 360)
            doc.save(out)
            return [out]
    if cat in ("video", "audio"):
        if key == "transcribe":
            out = out_path(src, ext="srt")
            run([PYTHON, "-I", HERE / "helpers.py", "transcribe", src, out], timeout=6 * 3600)
            return [out]
    if cat == "video":
        if key == "compress":
            return [_ff(src, ["-c:v", "libx264", "-crf", "28", "-preset", "medium", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
                        out_path(src, " Compressed", "mp4"))]
        if key == "mute":
            return [_ff(src, ["-map", "0:v", "-c", "copy", "-an"], out_path(src, " Muted"))]
        if key == "720p":
            return [_ff(src, ["-vf", "scale=-2:720", "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", "-b:a", "160k"], out_path(src, " 720p", "mp4"))]
        if key == "thumb":
            return [_ff(src, ["-vf", "thumbnail", "-frames:v", "1", "-q:v", "2"], out_path(src, " Thumbnail", "jpg"))]
    if cat == "audio":
        if key == "normalize":
            codec = AV_ARGS.get(_norm(e), AV_ARGS["mp3"])
            codec = [a for a in codec if a != "-vn"]
            return [_ff(src, ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", *codec],
                        out_path(src, " Normalized"))]
        if key == "compress":
            return [_ff(src, ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"], out_path(src, " Compressed", "mp3"))]
    raise RuntimeError(f"Outil « {key} » indisponible pour .{e}")


def _pdf_merge(paths):
    import fitz
    out = unique(Path(paths[0]).with_name("Fusion.pdf"))
    merged = fitz.open()
    for p in paths:
        merged.insert_pdf(fitz.open(p))
    merged.save(out, garbage=3, deflate=True)
    return out
