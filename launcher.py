#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Launcher Factur-X V11.0
Point d'entrée pour l'exécutable Windows (.exe)
Ouvre automatiquement le navigateur sur http://localhost:5000
"""
import sys
import os
import threading
import webbrowser
import time

# En mode .exe PyInstaller, les fichiers bundlés sont dans sys._MEIPASS
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Patch SCRIPT_DIR de l'appli pour pointer vers le dossier de l'exe
os.environ['FACTURX_BASE_DIR'] = BASE_DIR

from facturx_webapp_v11 import app
import facturx_webapp_v11 as appmodule
appmodule.SCRIPT_DIR = BASE_DIR
appmodule.UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads_temp')
os.makedirs(appmodule.UPLOAD_FOLDER, exist_ok=True)

PORT = 5000

def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

def main():
    print("=" * 60)
    print("  FACTUR-X V11.0")
    print("=" * 60)
    print(f"  Dossier : {BASE_DIR}")
    print(f"  URL     : http://localhost:{PORT}")
    print()
    print("  Le navigateur va s'ouvrir automatiquement.")
    print("  Fermez cette fenetre pour arreter l'application.")
    print("=" * 60)

    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

if __name__ == '__main__':
    main()
