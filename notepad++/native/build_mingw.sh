#!/usr/bin/env bash
# ==========================================================================
#  Build du plugin natif Facturix.dll (Notepad++ x64) par cross-compilation
#  MinGW-w64, depuis un environnement Linux CONNECTE a Internet.
#
#  Prerequis :
#    - x86_64-w64-mingw32-g++  (paquet g++-mingw-w64-x86-64 sur Debian/Ubuntu)
#    - curl
#
#  Usage :
#    cd notepad++/native && bash build_mingw.sh
#  Produit : notepad++/native/Facturix.dll
# ==========================================================================
set -euo pipefail
cd "$(dirname "$0")"

CXX="${CXX:-x86_64-w64-mingw32-g++}"
DEPS="deps"
NPP_RAW="https://raw.githubusercontent.com/notepad-plus-plus/notepad-plus-plus/master"

echo ">> Recuperation des en-tetes officiels Notepad++ / Scintilla"
mkdir -p "$DEPS"
fetch () {  # fetch <url> <dest>
  if [ ! -f "$DEPS/$2" ]; then
    echo "   - $2"
    curl -fsSL "$1" -o "$DEPS/$2"
  fi
}
fetch "$NPP_RAW/PowerEditor/src/MISC/PluginsManager/PluginInterface.h" PluginInterface.h
fetch "$NPP_RAW/PowerEditor/src/MISC/PluginsManager/Notepad_plus_msgs.h" Notepad_plus_msgs.h
fetch "$NPP_RAW/PowerEditor/src/menuCmdID.h" menuCmdID.h
fetch "$NPP_RAW/scintilla/include/Scintilla.h" Scintilla.h
fetch "$NPP_RAW/scintilla/include/Sci_Position.h" Sci_Position.h

# regenere la table EN16931 depuis le referentiel Python valide (si python dispo)
if command -v python3 >/dev/null 2>&1; then
  echo ">> Regeneration de en16931_data.h depuis le referentiel valide"
  python3 gen_data.py || echo "   (gen_data.py ignore - en16931_data.h existant conserve)"
fi

echo ">> Compilation (x64)"
"$CXX" -std=c++11 -O2 -shared -DUNICODE -D_UNICODE \
  -finput-charset=UTF-8 -fexec-charset=UTF-8 \
  -static -static-libgcc -static-libstdc++ \
  -I "$DEPS" \
  Facturix.cpp Facturix.def \
  -o Facturix.dll \
  -lkernel32 -luser32 -lgdi32

echo ">> OK : $(pwd)/Facturix.dll"
ls -la Facturix.dll
