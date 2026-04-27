# Facturix

## Projet

Outil interne de controle de factures electroniques (Factur-X / CII). Il compare les champs BT (Business Terms) entre :
- le **RDI** (fichier texte en sortie de SAP, format colonnes fixes, encodage cp1252)
- le **XML** embarque dans le PDF (norme CrossIndustryInvoice / Factur-X)

Stack : Python 3 / Flask, front-end en HTML inline dans `app.py`, deploiement via Gunicorn derriere nginx.

## Architecture

- **`app.py`** (~3000 lignes) : fichier unique contenant tout le backend Flask + le HTML/JS/CSS inline.
  - `parse_rdi()` : parse le fichier RDI (format colonnes fixes, positions 41-172 pour le tag, 172-175 pour la longueur, 175+ pour la valeur)
  - `extract_xml_from_pdf()` : extrait le XML Factur-X embarque dans le PDF
  - `perform_controls()` : compare RDI vs XML pour un champ donne
  - `normalize_value()` : normalise les valeurs (dates, nombres) pour comparaison
  - `apply_business_rules()` : applique les regles metier configurables (toutes editables via l'UI)
  - `controle()` : route principale POST `/controle` qui orchestre l'analyse
- **`mapping_v5_*.json`** : fichiers de mapping definissant les champs BT a controler, par type de formulaire :
  - `mapping_v5_simple.json` : CART Simple (principal)
  - `mapping_v5_groupee.json` : CART Groupee (contient les sous-entrees BT-21/BT-22 detaillees)
  - `mapping_v5_ventesdiverses.json` : Ventes Diverses
  - `mapping_v5_custom_simple_*.json` : mappings personnalises crees par l'utilisateur
- **`business_rules.json`** : regles metier configurables via l'UI (genere automatiquement au premier lancement)

## Format RDI

Fichier texte a colonnes fixes (encodage cp1252) :
- Lignes commencant par `DHEADER` ou `DMAIN`
- Positions 41-172 : nom du tag (ex: `GS_FECT_EINV-BG1-BT21`)
- Positions 172-175 : longueur de la valeur (3 chiffres)
- Position 175+ : valeur du champ

## Champs BT-21 / BT-22 (notes de facture)

Les champs BT-21 (code de note) et BT-22 (mention de note) apparaissent en **paires multiples** dans le RDI et le XML. Chaque paire a un suffixe base sur la valeur de BT-21 :

| Suffixe | BT-21 (code) | BT-22 (mention) | Obligatoire |
|---------|--------------|------------------|-------------|
| BAR | Traitement attendu | B2B / B2BINT / B2G | Oui |
| SUR | Remarques fournisseur | ISU (toujours) | Oui |
| ADN | B2G France (Chorus) | B2G | Non |
| AAB | Escompte | Texte escompte | Non |
| PMT | Indemnite 40 euros | Texte indemnite | Non |
| PMD | Penalites | Texte penalites | Non |

Le parser RDI cree des cles suffixees : `GS_FECT_EINV-BG1-BT21-BAR`, `GS_FECT_EINV-BG1-BT22-BAR`, etc.
Les XPaths utilisent des predicats : `ram:IncludedNote[ram:SubjectCode='BAR']/ram:Content`.

### Regles metier par defaut (editables via l'UI)

Toutes seedees via `_DEFAULT_RULES` au premier lancement, et migrees automatiquement par id sur les installations existantes :

- **rule_1** BT-22 = B2G (Chorus) : rend obligatoires BT-10, BT-13, BT-29, BT-29-1
- **rule_2** BT-3 = 381 (avoir) : rend obligatoires BT-25, BT-26
- **rule_3** BT-8 doit valoir "5"
- **rule_4** BT-48 ne commence pas par FR : rend obligatoire BT-58
- **rule_5** BT-131 negatif : BT-129 doit etre negatif
- **rule_6** BT-22-BAR = B2BINT : BT-47/BT-48 deviennent optionnels
- **rule_7** BT-21-SUR : presence obligatoire
- **rule_8** BT-22-SUR doit valoir "ISU" (comparaison insensible a la casse)
- **rule_9** BT-22-BAR = B2G : rend obligatoires BT-10, BT-13, BT-29, BT-29-1

## Structure d'un champ dans le mapping JSON

```json
{
  "balise": "BT-21-BAR",
  "categorie_bg": "BG-INFOS-GENERALES",
  "categorie_titre": "INFORMATIONS GENERALES DE LA FACTURE",
  "controles_cegedim": [],
  "ignore": "Non",
  "libelle": "Description du champ",
  "obligatoire": "Oui",
  "rdg": "Regle de gestion metier",
  "rdi": "GS_FECT_EINV-BG1-BT21-BAR",
  "type": "String",
  "valide": true,
  "xpath": "/rsm:CrossIndustryInvoice/rsm:ExchangedDocument/ram:IncludedNote[ram:SubjectCode='BAR']/ram:SubjectCode"
}
```

## Commandes

```bash
# Dev local
python3 app.py

# Production (Linux)
gunicorn -c gunicorn_config.py app:app
```

## Tests

Le skill `/run-tests` lance la campagne de tests Projectix (VM Docker + 29 assertions).

## Fichiers de reference

- `mapping BT21 BT22.xlsx` : regles de gestion pour les paires BT-21/BT-22 (colonnes F et G)
- Les fichiers RDI et PDF de test ne sont pas commites (voir `.gitignore`)
