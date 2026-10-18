"""Relais du clic droit : transmet « action<TAB>fichier » à DragShift, et le lance s'il ne tourne pas.

Volontairement minimal (bibliothèque standard seulement) : l'Explorateur le lance une fois
par fichier sélectionné, il doit démarrer vite. Usage : send.pyw <action> <fichier>...
"""
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPE = r"\\.\pipe\DragShift-" + os.environ.get("USERNAME", "user")
ERROR_PIPE_BUSY = 231


def main():
    if len(sys.argv) < 3:
        return
    action, paths = sys.argv[1], sys.argv[2:]
    # autorise DragShift à passer au premier plan (sinon Windows bloque l'affichage du menu)
    ctypes.windll.user32.AllowSetForegroundWindow(-1)
    data = "".join(f"{action}\t{os.path.abspath(p)}\n" for p in paths).encode("utf-8")
    started = False
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with open(PIPE, "wb", buffering=0) as f:
                f.write(data)
            return
        except OSError as e:
            if getattr(e, "winerror", None) == ERROR_PIPE_BUSY:
                # plusieurs fichiers sélectionnés = plusieurs relais en même temps : on attend
                # qu'une instance du pipe se libère (réveil immédiat) plutôt que de dormir
                ctypes.windll.kernel32.WaitNamedPipeW(PIPE, 2000)
                continue
            if not started:
                started = True  # DragShift ne tourne pas : on le démarre puis on réessaie
                subprocess.Popen([sys.executable, str(HERE / "dragshift.pyw")])
            time.sleep(0.2)


if __name__ == "__main__":
    main()
