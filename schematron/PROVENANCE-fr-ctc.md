# Schematrons France CTC (réforme facturation électronique)

Ce dossier contient les schematrons utilisés par `validators/schematron_validator.py`
pour valider un XML CII (Factur-X) **en fonction du profil déclaré dans BT-24**
(`ExchangedDocumentContext/GuidelineSpecifiedDocumentContextParameter/ram:ID`).

## Aiguillage par profil

| Profil BT-24 (classifié) | Jeu(x) de règles appliqué(s) |
|--------------------------|------------------------------|
| `minimum`, `basicwl`     | *(aucun)* — profils non conformes EN16931 ; appliquer EN16931 produirait des faux positifs |
| `basic`, `en16931`, `extended`, inconnu | `en16931-cii/` (EN16931-CII v1.3.16) |
| `extended-ctc-fr`        | `extended-ctc-fr/` (étape 2) **+** `br-fr-flux2/` (étape 3, overlay France CTC) |

La classification est faite par `classify_profile()` ; l'aiguillage par
`rulesets_for_profile()`. Cf. la doc officielle ci-dessous (« organisation des contrôles »,
étapes 1 à 4).

## Sources

### `en16931-cii/` — EN16931 (cœur normatif)
- Source : ConnectingEurope / eInvoicing-EN16931, **v1.3.16**
- https://github.com/ConnectingEurope/eInvoicing-EN16931/releases
- Licence : EUPL v1.2

### `extended-ctc-fr/` et `br-fr-flux2/` — règles France CTC
- Source : **FNFE-MPE** — paquet `2026_04_30_FNFE_SCHEMATRONS_FR_CTC_V1.3.1.zip`
- Version **1.3.1** du **30 avril 2026** (en application de la norme **XP Z12-012**)
- Page de téléchargement : https://fnfe-mpe.org/ressources/
- URL directe : https://fnfe-mpe.org/wp-content/uploads/2026/05/2026_04_30_FNFE_SCHEMATRONS_FR_CTC_V1.3.1.zip
- Licence : Apache 2.0 (« TEL QUEL / AS IS »)

Fichiers repris du paquet (uniquement la syntaxe **CII**, seule utilisée par Factur-X) :

| Fichier dans ce repo | Fichier d'origine dans le paquet FNFE-MPE |
|----------------------|-------------------------------------------|
| `extended-ctc-fr/EXTENDED-CTC-FR-CII.sch`  | `1b.EXTENDED-CTC-FR_Schematrons_V1.3.1_CII_ET_UBL/20260430_EXTENDED-CTC-FR-CII-V1.3.1.sch` |
| `extended-ctc-fr/EXTENDED-CTC-FR-CII.xslt` | `1b.EXTENDED-CTC-FR_Schematrons_V1.3.1_CII_ET_UBL/_XSLT/20260430_EXTENDED-CTC-FR-CII-V1.3.1.xsl` |
| `br-fr-flux2/BR-FR-Flux2-CII.sch`          | `2.BR-FR-CTC-Flux2-Schematron_UBL_ET_CII_FX_V1.3.1/20260430_BR-FR-Flux2-Schematron-CII_V1.3.1.sch` |
| `br-fr-flux2/BR-FR-Flux2-CII.xslt`         | `2.BR-FR-CTC-Flux2-Schematron_UBL_ET_CII_FX_V1.3.1/_XSLT/20260430_BR-FR-Flux2-Schematron-CII_V1.3.1.xsl` |

Les `.xslt` sont les versions **pré-compilées** (XSLT 2.0) exécutées par SaxonC-HE ;
les `.sch` servent à extraire les BT cités par chaque règle (`_rule_to_bts`).

## Notes importantes (d'après la doc FNFE-MPE)

- Le schematron **EXTENDED-CTC-FR** est *dérivé* du schematron EN16931 : il **remplace**
  certaines règles (ex. `BR-CO-10/11/12/13/15` désactivées au profit de `BR-FREXT-CO-*`)
  et ajoute des règles `BR-FR*`. Il s'utilise donc **à la place** d'EN16931, pas en plus.
- Toutes les règles **BR-FR Flux 2** sont en flag **`warning`** (non bloquantes) : elles
  signalent la non-conformité aux règles applicables au 1er septembre 2026 sans rejeter la facture.
- Non intégrés ici (hors périmètre pour l'instant) : schematrons UBL, XSD CII D22B (étape 1),
  schematron CDAR (cycle de vie), profils Factur-X 1.08 dédiés (1c), règles B2G `BR-FR-CPRO`
  (non encore publiées par le FNFE-MPE).

## Mise à jour

Pour passer à une version ultérieure : télécharger le nouveau paquet FNFE-MPE, remplacer
les 4 fichiers ci-dessus (en conservant les noms de ce repo), mettre à jour les libellés
`label` / versions dans `RULESETS` (`validators/schematron_validator.py`) et ce fichier.
