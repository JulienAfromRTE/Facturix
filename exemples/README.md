# Jeux de données d'exemple

Ce répertoire contient des fichiers **entièrement fictifs** pour tester Facturix.

## Fichiers

### `facture_exemple.xml` — XML CII / Factur-X

Facture au format Cross Industry Invoice (CII), conforme Factur-X profil EXTENDED / EN 16931.

**Données fictives :**
- Vendeur : ACME SAS (SIREN 123456789, TVA FR12123456789)
- Acheteur : EXEMPLE SA (SIREN 987654321, TVA FR98987654321)
- Numéro : FAC-2024-00001 · Date : 15/01/2024 · Échéance : 15/02/2024
- 1 ligne : Prestation informatique — 10 h × 100 € = 1 000 € HT
- TVA 20 % : 200 € · Total TTC : 1 200 €

**Comment l'utiliser dans Facturix :**
1. Onglet **Contrôle** → type de contrôle **CII direct**
2. Déposer `facture_exemple.xml`
3. Cliquer **Lancer le contrôle**

### `facture_exemple.txt` — RDI (format colonnes fixes, cp1252)

Fichier RDI fictif correspondant à la même facture, au format exporté par un ERP (SAP).

**Format :** colonnes fixes, encodage cp1252
- Position 0–40 : type d'enregistrement (DHEADER / DMAIN)
- Position 41–171 : nom du tag SAP
- Position 172–174 : longueur de la valeur (3 chiffres)
- Position 175+ : valeur

**Comment l'utiliser dans Facturix :**

Mode *RDI vs XML* :
1. Onglet **Contrôle** → type de contrôle **RDI vs XML**
2. Déposer `facture_exemple.txt` comme fichier RDI
3. Déposer `facture_exemple.xml` comme fichier XML (ou un PDF Factur-X)
4. Cliquer **Lancer le contrôle**

Mode *RDI seul* :
1. Onglet **Contrôle** → type de contrôle **RDI seul**
2. Déposer `facture_exemple.txt`

## Créer vos propres exemples

Pour générer un fichier RDI fictif respectant le bon format de colonnes :

```python
def rdi_line(record_type, tag, value):
    prefix = record_type.ljust(41)   # 41 chars
    tag_part = tag.ljust(131)        # positions 41-171
    length_str = str(len(value)).zfill(3)  # positions 172-174
    return prefix + tag_part + length_str + value

line = rdi_line("DMAIN", "WNUM_FACT", "MA-FACTURE-001")
# → "DMAIN                                    WNUM_FACT                                          ...014MA-FACTURE-001"
```

Les noms de tags correspondent aux colonnes `rdi` de la table `mapping_champs` en base de données.
