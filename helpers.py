"""Tâches lourdes lancées en sous-processus (évite de charger torch/onnx dans l'app)."""
import sys


def rembg(src, out):
    from PIL import Image
    from rembg import remove
    remove(Image.open(src)).save(out)


def _ts(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}".replace(",1000", ",999")


def _cuda_dlls():
    """cuBLAS/cuDNN viennent des paquets pip nvidia-* : on expose leurs dossiers bin."""
    import glob
    import os
    import site
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def transcribe(src, out):
    _cuda_dlls()
    from faster_whisper import WhisperModel
    segs = None
    for name, device, ctype in (("large-v3-turbo", "cuda", "float16"), ("small", "cpu", "int8")):
        try:
            model = WhisperModel(name, device=device, compute_type=ctype)
            segs = list(model.transcribe(src, vad_filter=True)[0])  # consomme ici : erreurs CUDA tardives
            break
        except Exception as e:  # noqa
            print(f"{device}: {e}", file=sys.stderr)
    if segs is None:
        raise SystemExit("Transcription impossible")
    with open(out, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segs, 1):
            f.write(f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{seg.text.strip()}\n\n")


if __name__ == "__main__":
    {"rembg": rembg, "transcribe": transcribe}[sys.argv[1]](sys.argv[2], sys.argv[3])
