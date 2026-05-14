# Facturix

## Projet

Outil interne de controle de factures electroniques (Factur-X / CII). Il compare les champs BT (Business Terms) entre :
- le **RDI** (fichier texte en sortie de SAP, format colonnes fixes, encodage cp1252)
- le **XML** embarque dans le PDF (norme CrossIndustryInvoice / Factur-X)

Stack : Python 3 / Flask, front-end decoupage en `templates/index.html` + `static/js/app.js` + `static/css/styles.css`, deploiement via Gunicorn derriere nginx.

## Architecture

- **`app.py`** (~2900 lignes) : backend Flask uniquement.
  - `parse_rdi()` : parse le fichier RDI (format colonnes fixes, positions 41-172 pour le tag, 172-175 pour la longueur, 175+ pour la valeur)
  - `extract_xml_from_pdf()` : extrait le XML Factur-X embarque dans le PDF
  - `perform_controls()` : compare RDI vs XML pour un champ donne
  - `normalize_value()` : normalise les valeurs (dates, nombres) pour comparaison
  - `apply_business_rules()` : applique les regles metier configurables (toutes editables via l'UI)
  - `load_mapping()` / `save_mapping()` : lecture/ecriture du mapping actif depuis la BDD (`mapping_champs`)
  - `controle()` : route principale POST `/controle` qui orchestre l'analyse
  - `archive_invoice_files()` : copie les fichiers d'un controle dans `archive_files/<id>/` (conservation manuelle, pas de purge auto)
- **`templates/index.html`** : HTML de l'interface, structure en onglets
- **`static/js/app.js`** : toute la logique JS (navigation, appels API, rendu)
- **`static/css/styles.css`** : feuille de style
- **`facturix.db`** (SQLite) : **source de verite** pour les mappings et les regles metier. Les fichiers `.json` dans `mapping_archive/` sont historiques uniquement, ne plus les editer ; tout passe par la BDD.
  - Table `mappings` : metadonnees (id, name, type, filename, is_default, color)
  - Table `mapping_champs` : un champ BT par ligne (mapping_id, position, balise, libelle, rdi, xpath, type, obligatoire, ignore_field, rdg, categorie_bg, categorie_titre, attribute, is_article, valide, controles_cegedim, type_enregistrement)
  - Table `mapping_content` : contenu JSON brut de secours (PRIMARY KEY = mapping_id)
  - Table `mapping_versions` : historique horodate (snapshot JSON par version)
  - Table `business_rules` : regle unique singleton, contenu JSON, seedee depuis `_DEFAULT_RULES` au premier lancement. Contient aussi les parametres globaux : `schematron_enabled` (bool), `db_alert_threshold_mb` (int, defaut 200)
  - Table `invoice_history` / `invoice_field_ko` : historique des controles
  - Table `mapping_audit` : journal des modifications de mapping
- **`archive_files/<id>/`** : fichiers RDI/PDF/XML archives par controle. **Pas de purge automatique** — a gerer manuellement via l'onglet Parametrage.
- **Mappings par defaut (id en BDD)** :
  - `default_simple` : CART Simple (principal)
  - `default_groupee` : CART Groupee (contient les sous-entrees BT-21/BT-22 detaillees)
  - `default_ventesdiverses` : Ventes Diverses
  - mappings personnalises : id arbitraire (ex. `45962558`), `is_default=0`

## Onglets de l'interface

Ordre : **Controle** · **Batch** · **Statistiques** · **Mapping BT** · **Regles Metiers** · **Parametrage** · **Aide**

- **Mapping BT** (ex-Parametrage) : gestion des mappings BT, ajout/modif/suppression de champs
- **Parametrage** (nouvel onglet) :
  - Toggle schematron EN16931 (stocke dans `business_rules.schematron_enabled`)
  - Seuil d'alerte taille BDD en Mo (stocke dans `business_rules.db_alert_threshold_mb`, defaut 200)
  - Bloc "Etat de la base de donnees" : nb entrees, taille SQLite, taille archives, dates min/max, repartition par type
  - Bloc "Purge de l'historique" : suppression manuelle par criteres (type de facture, taux de conformite < X%, anciennete > X jours, uniquement les erreurs) avec previsualisation (compte + taille liberee) avant confirmation

## Alerte taille BDD

Au chargement de la page et a l'ouverture de l'onglet Parametrage, `loadDbInfo()` appelle `GET /api/stats/db-info`. Si `db_size_bytes + archive_size_bytes >= db_alert_threshold_mb * 1024 * 1024`, une banniere rouge s'affiche sous les onglets avec un bouton "Aller aux Parametres".

## Routes API liees a l'historique

- `GET /api/stats/db-info` : taille BDD + archives, nb entrees, dates, repartition par type, seuil d'alerte
- `GET /api/stats/purge?min_pct=&max_age_days=&only_errors=&type_formulaire=` : previsualisation (count + freed_bytes)
- `POST /api/stats/purge` (JSON memes params) : suppression effective dans `invoice_history`, `invoice_field_ko`, et dossiers `archive_files/<id>/`

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

## Structure d'un champ (table `mapping_champs`)

Une ligne par champ BT. Colonnes principales :

| Colonne | Description |
|---------|-------------|
| `mapping_id` | FK vers `mappings.id` |
| `position` | ordre d'affichage |
| `balise` | code BT (ex. `BT-21-BAR`) |
| `libelle` | description |
| `rdi` | nom du tag dans le RDI (ex. `GS_FECT_EINV-BG1-BT21-BAR`) |
| `xpath` | XPath dans le XML Factur-X |
| `type` | `String`, `Date`, `Number`, ... |
| `obligatoire` | `Oui` / `Non` / `Dependant` |
| `ignore_field` | `Oui` ignore le champ |
| `rdg` | regle de gestion metier (texte libre) |
| `categorie_bg`, `categorie_titre` | regroupement UI |
| `attribute` | si non vide, on lit cet attribut XML au lieu du texte |
| `is_article` | 1 si champ d'article (boucle sur les lignes) |
| `controles_cegedim` | JSON des controles Cegedim |
| `type_enregistrement` | filtre RDI (`DHEADER` / `DMAIN` / vide) |

Pour interroger directement : `sqlite3 facturix.db "SELECT balise, xpath FROM mapping_champs WHERE mapping_id='default_simple' AND balise='BT-21-BAR';"`

## Namespaces XML utilises pour evaluer les XPath

Construits dynamiquement par `build_xml_namespaces(xml_doc)` :

1. Part d'un fallback statique `FACTURX_FALLBACK_NS` (rsm, ram, udt, qdt, xs, xsi)
2. Superpose toutes les declarations `xmlns:prefix=...` trouvees dans le XML (root + descendants), en ignorant le namespace par defaut (cle `None`, non utilisable en XPath 1.0)

Tout prefixe declare dans le XML est donc reconnu automatiquement, meme si un mapping introduit un prefixe inattendu. Les deux orchestrateurs (`/controle` et `/batch_controle`) utilisent ce helper.

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
