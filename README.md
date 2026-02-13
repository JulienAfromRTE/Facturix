# Factur-X V11 — Générer le .exe Windows via GitHub

## Pourquoi GitHub Actions ?

PyInstaller génère uniquement des binaires pour l'OS où il tourne.
- Mac → .app Mac
- Linux → binaire Linux
- Windows → .exe Windows ✅

GitHub met à disposition des machines Windows gratuitement.
On s'en sert pour compiler le .exe automatiquement.

---

## Étapes (15 minutes, une seule fois)

### 1. Créer un compte GitHub gratuit
→ https://github.com/signup

### 2. Créer un nouveau dépôt (repository)
1. Cliquez sur **"New repository"** (bouton vert)
2. Nom : `facturx-build` (ou ce que vous voulez)
3. Visibilité : **Private** (vos fichiers restent privés)
4. Cliquez **"Create repository"**

### 3. Uploader les fichiers
Dans votre nouveau dépôt, cliquez **"uploading an existing file"** et déposez :
```
facturx_webapp_v11.py
launcher.py
.github/workflows/build_windows_exe.yml   ← important, respecter l'arborescence
```

> ⚠️ Pour le fichier `.github/workflows/build_windows_exe.yml`, créez d'abord
> le dossier `.github/workflows/` dans l'interface GitHub avant d'uploader.

**Alternative plus simple avec GitHub Desktop :**
1. Téléchargez GitHub Desktop : https://desktop.github.com
2. Clonez votre dépôt en local
3. Copiez tous les fichiers dedans (en respectant les dossiers)
4. Faites un "Commit" puis "Push"

### 4. Lancer le build
1. Dans votre dépôt GitHub, cliquez sur l'onglet **"Actions"**
2. Vous voyez **"Build Windows EXE"** dans la liste
3. Cliquez dessus → **"Run workflow"** → **"Run workflow"** (bouton vert)
4. Attendez 3-5 minutes ⏳ (une icône orange tourne)
5. Quand c'est vert ✅ : c'est terminé !

### 5. Télécharger le .exe
1. Cliquez sur le build vert
2. En bas de page, section **"Artifacts"**
3. Cliquez sur **"FacturX_V11_Windows"**
4. Un ZIP se télécharge, dedans : **FacturX_V11.exe** 🎉

---

## Utilisation du .exe

1. Déposez `FacturX_V11.exe` dans un dossier avec :
   ```
   FacturX_V11.exe
   mapping_v5_simple.json
   mapping_v5_groupee.json
   ```
2. Double-cliquez sur `FacturX_V11.exe`
3. Une fenêtre noire s'ouvre + le navigateur s'ouvre sur `http://localhost:5000`
4. Utilisez l'application normalement

**Pas besoin d'installer Python sur le PC cible.** Tout est inclus dans le .exe.

---

## Mettre à jour le .exe (futures versions)

1. Remplacez `facturx_webapp_v11.py` dans le dépôt GitHub
2. Allez dans l'onglet **Actions** → **Run workflow**
3. Téléchargez le nouvel artifact
