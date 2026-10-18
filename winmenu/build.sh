#!/bin/sh
# Compile l'extension du menu moderne avec llvm-mingw (https://github.com/mstorsjo/llvm-mingw).
#   CXX=/chemin/vers/x86_64-w64-mingw32-clang++ ./build.sh
# Les binaires produits (DragShiftMenu.dll, DragShift.exe) sont versionnés : inutile de compiler pour installer.
set -e
cd "$(dirname "$0")"
CXX="${CXX:-x86_64-w64-mingw32-clang++}"
FLAGS="-O2 -std=c++17 -static -s -DUNICODE -D_UNICODE"
"$CXX" $FLAGS -shared -o DragShiftMenu.dll src/DragShiftMenu.cpp src/DragShiftMenu.def \
    -lole32 -luuid -lshlwapi -lshell32 -luser32
"$CXX" $FLAGS -mwindows -municode -o DragShift.exe src/launcher.cpp
echo "OK : DragShiftMenu.dll + DragShift.exe"
