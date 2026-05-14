"""Donnees par defaut pour les regles metier (categories + regles seedees au premier lancement)."""

# Catégories de règles (ordre d'affichage dans l'UI)
_RULE_CATEGORIES_ORDER = [
    "Calculs",
    "EN16931 (Schematron)",
    "Exonérations TVA",
    "B2G / Chorus",
    "Notes & mentions",
    "Paiement",
    "Cohérence",
    "Autre",
]

# Lookup id → catégorie pour backfill des règles existantes (créées avant l'ajout du champ)
_RULE_CATEGORY_BY_ID = {
    "rule_1": "B2G / Chorus",
    "rule_2": "Cohérence",
    "rule_3": "Autre",
    "rule_4": "Cohérence",
    "rule_5": "Cohérence",
    "rule_6": "Cohérence",
    "rule_7": "Notes & mentions",
    "rule_8": "Notes & mentions",
    "rule_9": "B2G / Chorus",
    "rule_1776265772124": "Cohérence",
    "rule_1776289738966": "Paiement",
    "rule_1776289820718": "Paiement",
    "rule_1776291450090": "Exonérations TVA",
    "rule_1776298233818": "Calculs",
    "rule_1776441134212": "Cohérence",
    "rule_1777292841400": "Exonérations TVA",
    "rule_1777299260571": "Notes & mentions",
}

_DEFAULT_RULES = {
    "rules": [
        {
            "id": "rule_1",
            "name": "Facture B2G Chorus",
            "category": "B2G / Chorus",
            "enabled": True,
            "conditions": [{"field": "BT-22", "operator": "equals", "value": "B2G"}],
            "actions": [
                {"type": "make_mandatory", "field": "BT-10"},
                {"type": "make_mandatory", "field": "BT-13"},
                {"type": "make_mandatory", "field": "BT-29"},
                {"type": "make_mandatory", "field": "BT-29-1"}
            ]
        },
        {
            "id": "rule_2",
            "name": "Facture avoir",
            "category": "Cohérence",
            "enabled": True,
            "conditions": [{"field": "BT-3", "operator": "equals", "value": "381"}],
            "actions": [
                {"type": "make_mandatory", "field": "BT-25"},
                {"type": "make_mandatory", "field": "BT-26"}
            ]
        },
        {
            "id": "rule_3",
            "name": "BT-8 doit valoir 5",
            "category": "Autre",
            "enabled": True,
            "conditions": [],
            "actions": [{"type": "must_equal", "field": "BT-8", "value": "5"}]
        },
        {
            "id": "rule_4",
            "name": "Client étranger",
            "category": "Cohérence",
            "enabled": True,
            "conditions": [{"field": "BT-48", "operator": "not_starts_with", "value": "FR"}],
            "actions": [{"type": "make_mandatory", "field": "BT-58"}]
        },
        {
            "id": "rule_5",
            "name": "Facture négative - quantité",
            "category": "Cohérence",
            "enabled": True,
            "conditions": [{"field": "BT-131", "operator": "less_than", "value": "0"}],
            "actions": [{"type": "must_be_negative", "field": "BT-129"}]
        },
        {
            "id": "rule_6",
            "name": "B2BINT - BT-47 et BT-48 non obligatoires",
            "category": "Cohérence",
            "enabled": True,
            "conditions": [{"field": "BT-22-BAR", "operator": "equals", "value": "B2BINT"}],
            "actions": [
                {"type": "make_optional", "field": "BT-47"},
                {"type": "make_optional", "field": "BT-48"}
            ]
        },
        {
            "id": "rule_7",
            "name": "BT-21-SUR présence obligatoire",
            "category": "Notes & mentions",
            "enabled": True,
            "conditions": [],
            "actions": [{"type": "make_mandatory", "field": "BT-21-SUR"}]
        },
        {
            "id": "rule_8",
            "name": "BT-22-SUR doit valoir ISU",
            "category": "Notes & mentions",
            "enabled": True,
            "conditions": [],
            "actions": [{"type": "must_equal", "field": "BT-22-SUR", "value": "ISU"}]
        },
        {
            "id": "rule_9",
            "name": "Facture B2G Chorus (BT-22-BAR)",
            "category": "B2G / Chorus",
            "enabled": True,
            "conditions": [{"field": "BT-22-BAR", "operator": "equals", "value": "B2G"}],
            "actions": [
                {"type": "make_mandatory", "field": "BT-10"},
                {"type": "make_mandatory", "field": "BT-13"},
                {"type": "make_mandatory", "field": "BT-29"},
                {"type": "make_mandatory", "field": "BT-29-1"}
            ]
        },
        # ─── Règles auto-générées (schématron EN 16931 / réforme FR) ───────────
        # Ajoutées au démarrage si absentes. Préfixe "[auto]" pour les distinguer.
        {
            "id": "auto_br_29",
            "name": "[auto] BR-29 — BT-131 = BT-146 × BT-129",
            "category": "Calculs",
            "description": "Montant net de la ligne = prix unitaire net × quantité facturée. Désactivée par défaut (vérifier la tolérance des arrondis avant activation).",
            "enabled": False,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "must_equal_product", "field_type": "bt", "field": "BT-131", "field1": "BT-146", "field2": "BT-129", "tolerance": 0.01}]
        },
        {
            "id": "auto_br_co_11",
            "name": "[auto] BR-CO-11 — BT-107 = Σ BT-92",
            "category": "Calculs",
            "description": "Somme des remises niveau document = Σ des montants de remise BT-92.",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "must_equal_sum_of_all", "field_type": "bt", "field": "BT-107", "sum_field": "BT-92", "tolerance": 0.01}]
        },
        {
            "id": "auto_br_co_12",
            "name": "[auto] BR-CO-12 — BT-108 = Σ BT-99",
            "category": "Calculs",
            "description": "Somme des charges niveau document = Σ des montants de charge BT-99.",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "must_equal_sum_of_all", "field_type": "bt", "field": "BT-108", "sum_field": "BT-99", "tolerance": 0.01}]
        },
        {
            "id": "auto_br_co_13",
            "name": "[auto] BR-CO-13 — BT-109 = Σ BT-131 − BT-107 + BT-108",
            "category": "Calculs",
            "description": "Total HT facture (BT-109) = Σ montants nets lignes (BT-131) − total remises document (BT-107) + total charges document (BT-108).",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "must_equal_sum_of_all_minus_plus", "field_type": "bt", "field": "BT-109", "sum_field": "BT-131", "minus_field": "BT-107", "plus_field": "BT-108", "tolerance": 0.01}]
        },
        {
            "id": "auto_br_co_14",
            "name": "[auto] BR-CO-14 — BT-110 = Σ BT-117",
            "category": "Calculs",
            "description": "Total TVA facture = Σ des montants TVA par catégorie BT-117.",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "must_equal_sum_of_all", "field_type": "bt", "field": "BT-110", "sum_field": "BT-117", "tolerance": 0.01}]
        },
        {
            "id": "auto_br_co_15",
            "name": "[auto] BR-CO-15 — BT-112 = BT-109 + BT-110",
            "category": "Calculs",
            "description": "Montant total TTC = Total HT + Total TVA.",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "must_equal_sum", "field_type": "bt", "field": "BT-112", "field1": "BT-109", "field2": "BT-110"}]
        },
        {
            "id": "auto_br_s_08",
            "name": "[auto] BR-S-08 — Détail ventilation TVA Standard rated",
            "category": "Calculs",
            "description": "Détail du calcul de la cohérence TVA Standard rated : pour chaque ventilation (BT-119), Σ BT-131 (lignes 'S' au même taux) + Σ BT-99 (charges 'S') − Σ BT-92 (remises 'S') doit égaler BT-116 de cette ventilation.",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": [{"type": "vat_breakdown_detail", "field_type": "bt", "field": "BT-118"}]
        },
        {
            "id": "auto_br_ae_1",
            "name": "[auto] BR-AE-1 — Autoliquidation : motif d'exonération",
            "category": "Exonérations TVA",
            "description": "Si BT-118 = AE (autoliquidation), un motif d'exonération doit être présent (BT-120 code ou BT-121 texte).",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [{"field": "BT-118", "operator": "equals", "value": "AE"}],
            "actions": [
                {"type": "make_mandatory", "field": "BT-120"},
                {"type": "make_mandatory", "field": "BT-121"}
            ]
        },
        {
            "id": "auto_br_k_1",
            "name": "[auto] BR-K-1 — Livraison intracommunautaire : motif d'exonération",
            "category": "Exonérations TVA",
            "description": "Si BT-118 = K (livraison intra-UE), un motif d'exonération doit être présent (BT-120 ou BT-121).",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [{"field": "BT-118", "operator": "equals", "value": "K"}],
            "actions": [
                {"type": "make_mandatory", "field": "BT-120"},
                {"type": "make_mandatory", "field": "BT-121"}
            ]
        },
        {
            "id": "auto_br_o_1",
            "name": "[auto] BR-O-1 — Hors champ TVA : motif d'exonération",
            "category": "Exonérations TVA",
            "description": "Si BT-118 = O (hors champ TVA), un motif d'exonération doit être présent (BT-120 ou BT-121).",
            "enabled": True,
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [{"field": "BT-118", "operator": "equals", "value": "O"}],
            "actions": [
                {"type": "make_mandatory", "field": "BT-120"},
                {"type": "make_mandatory", "field": "BT-121"}
            ]
        },
        # ─── Règles liées au schématron officiel EN16931 (CII v1.3.16) ─────────
        # Le calcul est exécuté par le schématron — la règle Facturix ne sert
        # qu'à exposer un toggle on/off et le nom convivial dans le tableau.
        # `schematron_id` doit correspondre exactement à l'id d'une assertion
        # du fichier EN16931-CII-validation-preprocessed.sch.
        {
            "id": "schematron_br_co_10",
            "name": "📜 BR-CO-10 — Σ BT-131 = BT-106 (somme des montants nets)",
            "category": "EN16931 (Schematron)",
            "description": "Somme des montants nets des lignes (BT-106) = Σ Montant net ligne (BT-131). Vérifié par le schématron officiel EN16931.",
            "enabled": True,
            "schematron_id": "BR-CO-10",
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": []
        },
        {
            "id": "schematron_br_co_13",
            "name": "📜 BR-CO-13 — BT-109 = Σ BT-131 − BT-107 + BT-108",
            "category": "EN16931 (Schematron)",
            "description": "Total HT facture (BT-109) = Σ montants nets lignes (BT-131) − total remises document (BT-107) + total charges document (BT-108). Vérifié par le schématron officiel.",
            "enabled": True,
            "schematron_id": "BR-CO-13",
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": []
        },
        {
            "id": "schematron_br_co_16",
            "name": "📜 BR-CO-16 — BT-115 = BT-112 − BT-113 + BT-114",
            "category": "EN16931 (Schematron)",
            "description": "Reste à payer (BT-115) = Total TTC (BT-112) − Acompte (BT-113) + Arrondi (BT-114). Vérifié par le schématron officiel.",
            "enabled": True,
            "schematron_id": "BR-CO-16",
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": []
        },
        {
            "id": "schematron_br_s_05",
            "name": "📜 BR-S-05 — Ligne 'Standard rated' : BT-152 > 0",
            "category": "EN16931 (Schematron)",
            "description": "Pour chaque ligne dont la catégorie TVA (BT-151) vaut 'Standard rated', le taux TVA ligne (BT-152) doit être strictement supérieur à 0.",
            "enabled": True,
            "schematron_id": "BR-S-05",
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": []
        },
        {
            "id": "schematron_br_s_08",
            "name": "📜 BR-S-08 — Cohérence ventilation TVA Standard rated",
            "category": "EN16931 (Schematron)",
            "description": "Pour chaque taux TVA distinct (BT-119) avec catégorie 'Standard rated' (BT-118), la base imposable (BT-116) doit égaler Σ BT-131 + Σ BT-99 − Σ BT-92 sur les lignes/charges/remises de même catégorie. Vérifié par le schématron officiel.",
            "enabled": True,
            "schematron_id": "BR-S-08",
            "applicable_forms": ["simple", "groupee", "ventesdiverses"],
            "conditions": [],
            "actions": []
        }
    ]
}
