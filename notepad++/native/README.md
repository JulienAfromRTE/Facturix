# Facturix — plugin natif Notepad++ (C++)

Version **DLL native** du plugin (pas de PythonScript). À l'ouverture d'un XML
Factur-X / CII : annotations EOL à droite des balises avec le Business Term
EN16931 (appellation officielle), et encadrement de chaque groupe BG par son
numéro (ouverture `┌─ BG-x` / fermeture `└─ BG-x`).

Même logique que la version PythonScript (parseur XML + XPath portés à
l'identique), **table EN16931 générée** depuis `../FacturixBT/facturix_en16931.py`.

## Fichiers

| Fichier | Rôle |
|---------|------|
| `Facturix.cpp` | tout le plugin (parseur XML, XPath, rendu, interface NPP) |
| `en16931_data.h` | table BT/BG **générée** (ne pas éditer à la main) |
| `gen_data.py` | génère `en16931_data.h` depuis le référentiel Python validé |
| `Facturix.def` | symboles exportés par la DLL |
| `build_mingw.sh` | cross-compilation MinGW-w64 (Linux connecté) |

## Comment obtenir `Facturix.dll` (sans compilateur local)

La machine cible étant hors-ligne, le binaire est produit par **GitHub Actions**
(cross-compilation automatique). Sur un **PC connecté** :

1. Pousser ce dépôt sur GitHub (`git push`). Le workflow
   [`.github/workflows/build-facturix-dll.yml`](../../.github/workflows/build-facturix-dll.yml)
   se lance tout seul (ou via l'onglet **Actions ▸ Run workflow**).
2. Ouvrir le run terminé ▸ section **Artifacts** ▸ télécharger **`Facturix-dll-x64`**
   (contient `Facturix.dll`).
3. Copier `Facturix.dll` sur clé USB.

> Pour une **Release** versionnée avec le DLL attaché : pousser un tag
> `git tag facturix-v1 && git push origin facturix-v1`.

## Installation dans Notepad++ (x64)

Créer un dossier au **nom exact du plugin** et y déposer la DLL :

```
<Notepad++>\plugins\Facturix\Facturix.dll
```

- 8.9.1 géré : `C:\APPLIRTE64\NOTEPAD++.080901\plugins\Facturix\Facturix.dll`
- 8.6.2 portable : `…\npp.8.6.2.portable.x64\plugins\Facturix\Facturix.dll`

Redémarrer Notepad++. Menu **Plugins ▸ Facturix ▸ Activer / désactiver Facturix**.
(Aucune dépendance : ni PythonScript, ni Python, ni base de données.)

## Build manuel (si un jour tu as un toolchain)

```bash
cd notepad++/native
bash build_mingw.sh           # Linux + g++-mingw-w64-x86-64
# -> Facturix.dll
```

## Limites connues / à vérifier

- **Binaire non testé** par l'auteur (pas de Notepad++ dans l'environnement de
  build) : vérifier qu'il se charge et que le rendu est correct au 1er lancement.
- EOL annotations : nécessitent une version récente de Scintilla (NPP ≳ 8.3).
- Pas de panneau docké ni de barre de statut dans cette version native (rendu
  EOL uniquement, pour la robustesse).
- Texte UTF-8 : adapté aux XML Factur-X (toujours UTF-8).
