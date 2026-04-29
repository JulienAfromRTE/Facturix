#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Factur-X V12.0 - Enhanced Mapping Management"""
from flask import Flask, request, jsonify, send_file
import os, json, re, sqlite3, PyPDF2, io
import pikepdf
import logging
from lxml import etree
from collections import defaultdict
from validators.schematron_validator import (
    validate_xml as schematron_validate_xml,
    candidates_for_balise,
    line_index_from_location,
    index_errors_by_bt,
)
from validators.cii_builder import build_cii_xml

app = Flask(__name__)

# ════════════════════════════════════════════
# CONFIGURATION PROJECTIX — NE PAS SUPPRIMER
# ════════════════════════════════════════════
APP_NAME = "facturix"
APP_SLUG = "facturix"
APP_RELEASE = "v2.1"
APP_DESCRIPTION = "La potion magique pour des factures certifiées"
APP_ICON = "💵"
APP_COLOR = "#3b82f6"


# Préfixe URL pour déploiement derrière un reverse proxy (ex: /facturix)
# Détecté automatiquement via le header SCRIPT_NAME de nginx,
# ou configurable via la variable d'environnement URL_PREFIX
URL_PREFIX = os.environ.get('URL_PREFIX', '').rstrip('/')

class ReverseProxied:
    """Middleware pour gérer SCRIPT_NAME envoyé par nginx."""
    def __init__(self, app):
        self.app = app
    def __call__(self, environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', '') or environ.get('HTTP_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
        return self.app(environ, start_response)

app.wsgi_app = ReverseProxied(app.wsgi_app)

# Quand PyInstaller crée un .exe (--onefile), le code s'exécute dans un
# dossier temporaire (sys._MEIPASS). On veut pointer vers le dossier de
# l'exe lui-même pour trouver les fichiers JSON de mapping.
import sys
if getattr(sys, 'frozen', False):
    # Mode .exe PyInstaller : dossier de l'executable
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    # Mode Linux/Gunicorn : on remonte depuis __file__ du .py
    # puis on verifie ; si mal resolu on tente /opt/facturx
    _self = os.path.abspath(__file__)
    SCRIPT_DIR = os.path.dirname(_self)
    if not os.path.exists(os.path.join(SCRIPT_DIR, 'facturx_webapp_v11.py')):
        for _candidate in [
            '/opt/facturx',
            os.path.dirname(os.path.dirname(os.path.dirname(sys.executable))),
        ]:
            if os.path.exists(os.path.join(_candidate, 'facturx_webapp_v11.py')):
                SCRIPT_DIR = _candidate
                break

UPLOAD_FOLDER = os.path.join(SCRIPT_DIR, 'uploads_temp')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_FILE = os.path.join(SCRIPT_DIR, 'facturix.db')

print(f"[FACTURX] Dossier de travail : {SCRIPT_DIR}")

# ── Données par défaut ──────────────────────────────────────────────────────

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

# ── SQLite helpers ──────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _get_mapping_id(type_formulaire):
    """Convertit un type_formulaire (clé URL) en id de mapping DB."""
    defaults = {
        'simple':         'default_simple',
        'groupee':        'default_groupee',
        'flux':           'default_flux',
        'ventesdiverses': 'default_ventesdiverses',
        'CARTsimple':     'default_simple',
    }
    if type_formulaire in defaults:
        return defaults[type_formulaire]
    # Mappings custom : type_formulaire = "custom_<id>" → id = "<id>"
    if type_formulaire.startswith('custom_'):
        return type_formulaire[len('custom_'):]
    return type_formulaire

def init_db():
    """Crée la base SQLite et initialise les données par défaut."""
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS mappings (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            type         TEXT NOT NULL,
            filename     TEXT NOT NULL,
            created_date TEXT,
            is_default   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS mapping_champs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mapping_id          TEXT NOT NULL REFERENCES mappings(id) ON DELETE CASCADE,
            position            INTEGER NOT NULL DEFAULT 0,
            balise              TEXT NOT NULL,
            libelle             TEXT DEFAULT '',
            rdi                 TEXT DEFAULT '',
            xpath               TEXT DEFAULT '',
            type                TEXT DEFAULT 'String',
            obligatoire         TEXT DEFAULT 'Non',
            ignore_field        TEXT DEFAULT 'Non',
            rdg                 TEXT DEFAULT '',
            categorie_bg        TEXT DEFAULT '',
            categorie_titre     TEXT DEFAULT '',
            attribute           TEXT DEFAULT '',
            is_article          INTEGER DEFAULT 0,
            valide              INTEGER DEFAULT 1,
            controles_cegedim   TEXT DEFAULT '[]',
            type_enregistrement TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS mapping_versions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            mapping_id  TEXT NOT NULL
                        REFERENCES mappings(id) ON DELETE CASCADE,
            timestamp   TEXT NOT NULL,
            content     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_rules (
            singleton   INTEGER PRIMARY KEY DEFAULT 1
                        CHECK(singleton = 1),
            content     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mapping_audit (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            mapping_id              TEXT NOT NULL,
            timestamp               TEXT NOT NULL,
            author                  TEXT NOT NULL,
            action                  TEXT NOT NULL,
            bt_balise               TEXT NOT NULL,
            old_libelle             TEXT,
            new_libelle             TEXT,
            old_rdi                 TEXT,
            new_rdi                 TEXT,
            old_xpath               TEXT,
            new_xpath               TEXT,
            old_obligatoire         TEXT,
            new_obligatoire         TEXT,
            old_ignore              TEXT,
            new_ignore              TEXT,
            old_rdg                 TEXT,
            new_rdg                 TEXT,
            old_categorie_bg        TEXT,
            new_categorie_bg        TEXT,
            old_attribute           TEXT,
            new_attribute           TEXT,
            old_type_enregistrement TEXT,
            new_type_enregistrement TEXT,
            snapshot                TEXT
        );
        CREATE TABLE IF NOT EXISTS invoice_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            type_formulaire TEXT NOT NULL,
            type_controle   TEXT,
            mode            TEXT NOT NULL,
            invoice_number  TEXT,
            filename        TEXT,
            total           INTEGER DEFAULT 0,
            ok              INTEGER DEFAULT 0,
            erreur          INTEGER DEFAULT 0,
            ignore_count    INTEGER DEFAULT 0,
            ambigu          INTEGER DEFAULT 0,
            conformity_pct  REAL DEFAULT 0,
            error           TEXT
        );
        CREATE TABLE IF NOT EXISTS invoice_field_ko (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_history_id INTEGER NOT NULL REFERENCES invoice_history(id) ON DELETE CASCADE,
            type_formulaire    TEXT NOT NULL,
            timestamp          TEXT NOT NULL,
            balise             TEXT NOT NULL,
            libelle            TEXT,
            obligatoire        TEXT,
            status             TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_invoice_history_ts   ON invoice_history(timestamp);
        CREATE INDEX IF NOT EXISTS idx_invoice_history_type ON invoice_history(type_formulaire);
        CREATE INDEX IF NOT EXISTS idx_invoice_history_mode ON invoice_history(mode);
        CREATE INDEX IF NOT EXISTS idx_invoice_field_ko_balise ON invoice_field_ko(balise);
        CREATE INDEX IF NOT EXISTS idx_invoice_field_ko_type   ON invoice_field_ko(type_formulaire);
        CREATE INDEX IF NOT EXISTS idx_invoice_field_ko_ts     ON invoice_field_ko(timestamp);
    ''')
    # Migrations pour bases existantes
    try:
        conn.execute("ALTER TABLE mappings ADD COLUMN color TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # colonne déjà présente
    _migrate_to_relational(conn)
    conn.commit()
    _seed_default_data(conn)
    conn.close()
    print("[DB] Base SQLite prête.")

# ── Helpers : champ dict ↔ mapping_champs row ───────────────────────────────

def _champ_to_row(mapping_id, position, champ):
    """Convertit un dict champ en tuple de valeurs pour mapping_champs."""
    return (
        mapping_id,
        position,
        champ.get('balise', ''),
        champ.get('libelle', ''),
        champ.get('rdi', ''),
        champ.get('xpath', ''),
        champ.get('type', 'String'),
        champ.get('obligatoire', 'Non'),
        champ.get('ignore', 'Non'),
        champ.get('rdg', ''),
        champ.get('categorie_bg', ''),
        champ.get('categorie_titre', ''),
        champ.get('attribute', ''),
        1 if champ.get('is_article') else 0,
        1 if champ.get('valide', True) else 0,
        json.dumps(champ.get('controles_cegedim', []), ensure_ascii=False),
        champ.get('type_enregistrement', ''),
    )

def _row_to_champ(row):
    """Convertit une Row mapping_champs en dict champ."""
    c = {
        'balise':              row['balise'],
        'libelle':             row['libelle'] or '',
        'rdi':                 row['rdi'] or '',
        'xpath':               row['xpath'] or '',
        'type':                row['type'] or 'String',
        'obligatoire':         row['obligatoire'] or 'Non',
        'ignore':              row['ignore_field'] or 'Non',
        'rdg':                 row['rdg'] or '',
        'categorie_bg':        row['categorie_bg'] or '',
        'categorie_titre':     row['categorie_titre'] or '',
        'valide':              bool(row['valide']),
        'controles_cegedim':   json.loads(row['controles_cegedim'] or '[]'),
    }
    if row['attribute']:
        c['attribute'] = row['attribute']
    if row['is_article']:
        c['is_article'] = True
    if row['type_enregistrement']:
        c['type_enregistrement'] = row['type_enregistrement']
    return c

_CHAMP_INSERT_SQL = '''
    INSERT INTO mapping_champs
        (mapping_id, position, balise, libelle, rdi, xpath, type, obligatoire,
         ignore_field, rdg, categorie_bg, categorie_titre, attribute,
         is_article, valide, controles_cegedim, type_enregistrement)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
'''

# ── Migration mapping_content → mapping_champs ──────────────────────────────

def _migrate_to_relational(conn):
    """Migration one-shot : mapping_content (blob JSON) → mapping_champs (colonnes)."""
    # ── 1. Migration mapping_champs ──────────────────────────────────────────
    has_content = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mapping_content'"
    ).fetchone()
    if has_content:
        already_done = conn.execute(
            "SELECT COUNT(*) FROM mapping_champs"
        ).fetchone()[0]
        if not already_done:
            print("[MIGRATION] mapping_content → mapping_champs …")
            rows = conn.execute("SELECT mapping_id, content FROM mapping_content").fetchall()
            for row in rows:
                mapping_id = row['mapping_id']
                try:
                    data = json.loads(row['content'])
                    champs = data.get('champs', [])
                    for pos, champ in enumerate(champs):
                        conn.execute(_CHAMP_INSERT_SQL, _champ_to_row(mapping_id, pos, champ))
                except Exception as e:
                    print(f"[MIGRATION] Erreur {mapping_id}: {e}")
            conn.commit()
            print("[MIGRATION] mapping_champs : OK")

    # ── 2. Migration mapping_audit → nouveau schéma (colonnes individuelles) ─
    has_old_col = conn.execute(
        "SELECT COUNT(*) FROM pragma_table_info('mapping_audit') WHERE name='old_value'"
    ).fetchone()[0]
    if has_old_col:
        conn.execute("DROP TABLE IF EXISTS mapping_audit")
        conn.execute('''
            CREATE TABLE mapping_audit (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_id              TEXT NOT NULL,
                timestamp               TEXT NOT NULL,
                author                  TEXT NOT NULL,
                action                  TEXT NOT NULL,
                bt_balise               TEXT NOT NULL,
                old_libelle             TEXT,
                new_libelle             TEXT,
                old_rdi                 TEXT,
                new_rdi                 TEXT,
                old_xpath               TEXT,
                new_xpath               TEXT,
                old_obligatoire         TEXT,
                new_obligatoire         TEXT,
                old_ignore              TEXT,
                new_ignore              TEXT,
                old_rdg                 TEXT,
                new_rdg                 TEXT,
                old_categorie_bg        TEXT,
                new_categorie_bg        TEXT,
                old_attribute           TEXT,
                new_attribute           TEXT,
                old_type_enregistrement TEXT,
                new_type_enregistrement TEXT,
                snapshot                TEXT,
                revert_of               INTEGER
            )
        ''')
        conn.commit()
        print("[MIGRATION] mapping_audit recréé avec le nouveau schéma.")

    # ── 3. Migration mapping_audit → ajout colonne revert_of ──────────────────
    has_revert_of = conn.execute(
        "SELECT COUNT(*) FROM pragma_table_info('mapping_audit') WHERE name='revert_of'"
    ).fetchone()[0]
    if not has_revert_of:
        conn.execute("ALTER TABLE mapping_audit ADD COLUMN revert_of INTEGER")
        conn.commit()
        print("[MIGRATION] mapping_audit : colonne revert_of ajoutée.")

    # ── 3. Archiver les fichiers JSON originaux (une seule fois) ─────────────
    archive_dir = os.path.join(SCRIPT_DIR, 'mapping_archive')
    if has_content and not os.path.exists(archive_dir):
        import shutil
        os.makedirs(archive_dir, exist_ok=True)
        for fname in os.listdir(SCRIPT_DIR):
            if fname.endswith('.json') and (
                fname.startswith('mapping_v5') or fname.startswith('mappings_index')
            ):
                try:
                    shutil.copy2(os.path.join(SCRIPT_DIR, fname),
                                 os.path.join(archive_dir, fname))
                except Exception:
                    pass
        print(f"[MIGRATION] Fichiers JSON archivés dans {archive_dir}")
    print("[MIGRATION] Terminée.")

def _seed_default_data(conn):
    """Insère les données initiales si la DB est vide (exécuté une seule fois)."""
    c = conn.cursor()

    # ── Règles métier ────────────────────────────────────────────────────────
    c.execute("SELECT COUNT(*) FROM business_rules")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO business_rules (singleton, content) VALUES (1, ?)",
            (json.dumps(_DEFAULT_RULES, ensure_ascii=False),)
        )
        print("[DB] Règles métier initialisées.")

    # ── Mappings par défaut ──────────────────────────────────────────────────
    c.execute("SELECT COUNT(*) FROM mappings")
    if c.fetchone()[0] == 0:
        seeds = [
            ("default_simple",  "CART Simple",    "CART Simple",    "mapping_CARTsimple_1504.json"),
            ("default_groupee", "CART Groupée",   "CART Groupée",   "mapping_CARTgroupe_1504.json"),
            ("default_flux",    "Flux Générique", "Flux Générique", "mapping_fluxGénérique_1504.json"),
        ]
        for mid, name, mtype, filename in seeds:
            champs = []
            filepath = os.path.join(SCRIPT_DIR, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        champs = json.load(f).get('champs', [])
                except Exception:
                    pass
            c.execute(
                "INSERT INTO mappings "
                "(id, name, type, filename, created_date, is_default) VALUES (?,?,?,?,?,1)",
                (mid, name, mtype, filename, "2025-04-15")
            )
            for pos, champ in enumerate(champs):
                c.execute(_CHAMP_INSERT_SQL, _champ_to_row(mid, pos, champ))
            print(f"[DB] Mapping initialisé : {name} ({filename})")
        conn.commit()

# ── Statistiques : log d'une facture contrôlée ──────────────────────────────

def _log_invoice_to_history(type_formulaire, type_controle, mode,
                            invoice_number=None, filename=None,
                            stats=None, results=None, error=None):
    """Insère une ligne dans invoice_history (+ ses champs KO dans invoice_field_ko).
    Best-effort : toute exception est silencieuse pour ne jamais bloquer le contrôle."""
    try:
        from datetime import datetime
        ts = datetime.now().isoformat(timespec='seconds')
        stats = stats or {}
        total = int(stats.get('total', 0) or 0)
        ok = int(stats.get('ok', 0) or 0)
        erreur = int(stats.get('erreur', 0) or 0)
        ign = int(stats.get('ignore', 0) or 0)
        amb = int(stats.get('ambigu', 0) or 0)
        # Taux de conformité = OK / total. Aligné sur l'affichage des onglets
        # Contrôle (var pct=Math.round(data.stats.ok/data.stats.total*100))
        # et Batch (var pct=Math.round(nbOkInv/nbTotInv*100)).
        pct = round(100.0 * ok / total, 2) if total > 0 else 0.0

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO invoice_history "
            "(timestamp, type_formulaire, type_controle, mode, invoice_number, "
            " filename, total, ok, erreur, ignore_count, ambigu, conformity_pct, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, type_formulaire or '', type_controle or '', mode or 'unitaire',
             invoice_number or None, filename or None,
             total, ok, erreur, ign, amb, pct, error)
        )
        invoice_id = cur.lastrowid
        if results:
            rows = []
            for r in results:
                if r.get('status') in ('ERREUR', 'AMBIGU'):
                    rows.append((
                        invoice_id, type_formulaire or '', ts,
                        r.get('balise', '') or '',
                        (r.get('libelle', '') or '')[:200],
                        r.get('obligatoire', '') or '',
                        r.get('status', '')
                    ))
            if rows:
                cur.executemany(
                    "INSERT INTO invoice_field_ko "
                    "(invoice_history_id, type_formulaire, timestamp, balise, "
                    " libelle, obligatoire, status) VALUES (?,?,?,?,?,?,?)",
                    rows
                )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[STATS] Erreur log historique : {e}")

# ── Business rules ──────────────────────────────────────────────────────────

def load_business_rules():
    """Charge les règles métiers depuis la base de données.
    Injecte automatiquement les règles par défaut manquantes (par id)
    pour migrer les anciennes installations vers les nouvelles règles."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT content FROM business_rules WHERE singleton = 1"
        ).fetchone()
        conn.close()
        if row:
            data = json.loads(row['content'])
            existing_ids = {r.get('id') for r in data.get('rules', [])}
            missing = [r for r in _DEFAULT_RULES['rules'] if r.get('id') not in existing_ids]
            if missing:
                data.setdefault('rules', []).extend(missing)
            # Backfill catégorie sur les règles existantes (sans écraser une catégorie déjà saisie)
            category_changed = False
            defaults_by_id = {r.get('id'): r for r in _DEFAULT_RULES['rules']}
            for r in data.get('rules', []):
                if not r.get('category'):
                    rid = r.get('id')
                    cat = (defaults_by_id.get(rid, {}).get('category')
                           or _RULE_CATEGORY_BY_ID.get(rid)
                           or 'Autre')
                    r['category'] = cat
                    category_changed = True
            if missing or category_changed:
                save_business_rules(data)
            return data
    except Exception:
        pass
    return _DEFAULT_RULES

def save_business_rules(rules_data):
    """Sauvegarde les règles métiers."""
    try:
        content = json.dumps(rules_data, ensure_ascii=False)
        conn = get_db()
        conn.execute(
            "INSERT INTO business_rules (singleton, content) VALUES (1, ?) "
            "ON CONFLICT(singleton) DO UPDATE SET content = excluded.content",
            (content,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

# ── Mappings index ──────────────────────────────────────────────────────────

def load_mappings_index():
    """Charge l'index des mappings depuis la base de données."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, name, type, filename, created_date, is_default, color "
            "FROM mappings ORDER BY is_default DESC, name"
        ).fetchall()
        conn.close()
        return {
            "mappings": [
                {
                    "id":           r["id"],
                    "name":         r["name"],
                    "type":         r["type"],
                    "filename":     r["filename"],
                    "created_date": r["created_date"],
                    "is_default":   bool(r["is_default"]),
                    "color":        r["color"] or ""
                }
                for r in rows
            ]
        }
    except Exception:
        return {"mappings": []}

def save_mappings_index(index_data):
    """Sync un index en mémoire vers la base (compatibilité code existant)."""
    try:
        conn = get_db()
        for m in index_data.get('mappings', []):
            conn.execute(
                "INSERT INTO mappings "
                "(id, name, type, filename, created_date, is_default) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, type=excluded.type, filename=excluded.filename, "
                "created_date=excluded.created_date, is_default=excluded.is_default",
                (m['id'], m['name'], m['type'], m['filename'],
                 m.get('created_date', ''), 1 if m.get('is_default') else 0)
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

# ── Mapping content ─────────────────────────────────────────────────────────

def load_mapping(type_formulaire='CARTsimple'):
    """Charge le contenu d'un mapping depuis mapping_champs."""
    mapping_id = _get_mapping_id(type_formulaire)
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM mapping_champs WHERE mapping_id=? ORDER BY position",
            (mapping_id,)
        ).fetchall()
        conn.close()
        return {'champs': [_row_to_champ(r) for r in rows]}
    except Exception as e:
        print(f"Erreur chargement mapping {mapping_id}: {e}")
    return None

def save_mapping(data, type_formulaire='simple'):
    """Sauvegarde le contenu d'un mapping dans mapping_champs."""
    mapping_id = _get_mapping_id(type_formulaire)
    try:
        champs = data.get('champs', [])
        conn = get_db()
        conn.execute("DELETE FROM mapping_champs WHERE mapping_id=?", (mapping_id,))
        for pos, champ in enumerate(champs):
            conn.execute(_CHAMP_INSERT_SQL, _champ_to_row(mapping_id, pos, champ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erreur sauvegarde mapping {mapping_id}: {e}")
        return False

# ── Mapping versions ────────────────────────────────────────────────────────

def save_mapping_version(data, type_formulaire='simple'):
    """Sauvegarde une version horodatée du mapping."""
    from datetime import datetime
    mapping_id = _get_mapping_id(type_formulaire)
    timestamp  = datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        content = json.dumps(data, ensure_ascii=False)
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO mapping_versions (mapping_id, timestamp, content) VALUES (?, ?, ?)",
            (mapping_id, timestamp, content)
        )
        version_id = cur.lastrowid
        conn.commit()
        conn.close()
        return {'success': True, 'filename': str(version_id), 'timestamp': timestamp}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def list_mapping_versions(type_formulaire='simple'):
    """Liste toutes les versions horodatées d'un mapping (plus récent en premier)."""
    mapping_id = _get_mapping_id(type_formulaire)
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, timestamp, LENGTH(content) AS size "
            "FROM mapping_versions WHERE mapping_id = ? ORDER BY id DESC",
            (mapping_id,)
        ).fetchall()
        conn.close()
        return [
            {
                'filename':  str(r['id']),
                'timestamp': r['timestamp'],
                'size':      r['size'],
                'mtime':     0
            }
            for r in rows
        ]
    except Exception:
        return []

def restore_mapping_version(filename, type_formulaire='simple'):
    """Restaure une version horodatée comme version active."""
    try:
        version_id = int(filename)
        conn = get_db()
        row = conn.execute(
            "SELECT content FROM mapping_versions WHERE id = ?", (version_id,)
        ).fetchone()
        conn.close()
        if not row:
            return {'success': False, 'error': 'Version introuvable'}
        data = json.loads(row['content'])
        return {'success': save_mapping(data, type_formulaire)}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def parse_rdi(rdi_path):
    """
    Parse le fichier RDI et retourne (data, articles).
    - data : dict des champs d'en-tête (hors articles)
    - articles : liste de dicts, un par bloc article (BG25/BG26/BG29/BG30/BG31)
    """
    data = {}
    articles = []
    current_article = None
    last_bt21_value = None  # Pour suivre les paires BT21/BT22

    # Tags qui appartiennent aux blocs articles (lignes de facture)
    ARTICLE_TAG_PREFIXES = ('GS_FECT_EINV-BG25-', 'GS_FECT_EINV-BG26-',
                            'GS_FECT_EINV-BG29-', 'GS_FECT_EINV-BG30-',
                            'GS_FECT_EINV-BG31-',
                            'MAIN_GS_FECT_EINV-BG25-', 'MAIN_GS_FECT_EINV-BG26-',
                            'MAIN_GS_FECT_EINV-BG29-', 'MAIN_GS_FECT_EINV-BG30-',
                            'MAIN_GS_FECT_EINV-BG31-')

    # Lire toutes les lignes parsées en une seule passe
    parsed_lines = []  # (record_type, tag, value)
    try:
        with open(rdi_path, 'r', encoding='cp1252') as f:
            for line in f:
                if line.startswith('D'):
                    if len(line) >= 176:
                        try:
                            length_str = line[172:175]
                            length = int(length_str)
                            value = line[175:175+length] if len(line) > 175 else ''
                            tag_section = line[41:172].strip()
                            tag_parts = tag_section.split()
                            if tag_parts:
                                tag = tag_parts[-1]
                                record_type = line.split()[0]
                                parsed_lines.append((record_type, tag, value))
                        except:
                            pass
    except:
        pass

    # Construire data_multi : {tag_upper: [(record_type, value), ...]} pour toutes les occurrences
    data_multi = {}
    for record_type, tag, value in parsed_lines:
        tag_upper = tag.upper()
        if tag_upper not in data_multi:
            data_multi[tag_upper] = []
        data_multi[tag_upper].append((record_type, value))

    # Passe 1 : construire data normalement et collecter les valeurs BT-22 qui sont des références
    bt22_refs = set()  # Noms de tags référencés par les BT-22 (ex: "PENALITE-TEXT", "TTAUX-TEXT")
    for record_type, tag, value in parsed_lines:
        # Gestion spéciale des paires BT21/BT22 (multiples occurrences)
        if tag == 'GS_FECT_EINV-BG1-BT21':
            suffix = value.strip().upper()
            last_bt21_value = suffix
            suffixed_tag = f'{tag}-{suffix}'
            data[suffixed_tag] = value
        elif tag == 'GS_FECT_EINV-BG1-BT22' and last_bt21_value:
            suffixed_tag = f'{tag}-{last_bt21_value}'
            data[suffixed_tag] = value
            last_bt21_value = None
            # Détecter si la valeur est une référence vers un bloc de texte
            val_stripped = value.strip()
            if val_stripped and not val_stripped.startswith('GS_FECT_EINV') and val_stripped.replace('-', '').replace('_', '').isalpha():
                bt22_refs.add(val_stripped)
        # Gestion des blocs articles (BG25/BG26/BG29/BG30/BG31)
        elif any(tag.startswith(p) or tag.upper().startswith(p) for p in ARTICLE_TAG_PREFIXES):
            if 'BT126' in tag:
                current_article = {}
                articles.append(current_article)
            if current_article is not None:
                current_article[tag] = value
        elif tag not in data:
            data[tag] = value

    # Passe 2 : accumuler les blocs de texte référencés par les BT-22
    if bt22_refs:
        text_blocks = {}
        for record_type, tag, value in parsed_lines:
            if tag in bt22_refs:
                if tag not in text_blocks:
                    text_blocks[tag] = []
                text_blocks[tag].append(value)

        # Résolution : remplacer les valeurs BT-22 par le texte concaténé
        for key in list(data.keys()):
            if 'BT22' in key:
                val = data[key].strip()
                if val in text_blocks:
                    data[key] = ' '.join(text_blocks[val])

    return data, articles, data_multi

def extract_xml_from_pdf(pdf_path):
    try:
        with open(pdf_path, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            if '/Names' in pdf_reader.trailer['/Root']:
                names = pdf_reader.trailer['/Root']['/Names']
                if '/EmbeddedFiles' in names:
                    embedded = names['/EmbeddedFiles']['/Names']
                    for i in range(0, len(embedded), 2):
                        file_name = embedded[i]
                        if isinstance(file_name, str) and file_name.lower().endswith('.xml'):
                            file_spec = embedded[i + 1].get_object()
                            file_obj = file_spec['/EF']['/F'].get_object()
                            xml_content = file_obj.get_data()
                            return xml_content.decode('utf-8') if isinstance(xml_content, bytes) else xml_content
    except:
        pass
    return None

def remove_pdf_signature(pdf_path):
    """Reconstruit le PDF sans les signatures numériques (champs /Sig, /Perms, SigFlags).
    Les fichiers embarqués (XML Factur-X) sont préservés."""
    with pikepdf.open(pdf_path, allow_overwriting_input=False) as pdf:
        # Supprimer /Perms qui verrouille les modifications
        if '/Perms' in pdf.Root:
            del pdf.Root['/Perms']
        # Supprimer les champs de signature dans AcroForm
        if '/AcroForm' in pdf.Root:
            acroform = pdf.Root['/AcroForm']
            if '/Fields' in acroform:
                acroform['/Fields'] = pikepdf.Array([
                    f for f in acroform['/Fields']
                    if pdf.get_object(f.objgen).get('/FT') != pikepdf.Name('/Sig')
                ])
            if '/SigFlags' in acroform:
                del acroform['/SigFlags']
        output = io.BytesIO()
        pdf.save(output)
        output.seek(0)
        return output


FACTURX_FALLBACK_NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
    'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
    'xs':  'http://www.w3.org/2001/XMLSchema',
}

def build_xml_namespaces(xml_doc):
    """
    Construit le dict de namespaces pour evaluer les XPath du mapping.
    On part du fallback Factur-X puis on superpose les declarations du XML
    (root + descendants), en ignorant le namespace par defaut (cle None,
    non utilisable dans XPath 1.0). Tout prefixe present dans le XML sera
    donc reconnu, meme si un mapping ajoute un namespace inattendu.
    """
    ns = dict(FACTURX_FALLBACK_NS)
    if xml_doc is None:
        return ns
    try:
        for el in xml_doc.iter():
            for prefix, uri in (el.nsmap or {}).items():
                if prefix and uri:
                    ns[prefix] = uri
    except Exception:
        pass
    return ns


def get_xml_tag_name(xpath):
    """Extrait le nom complet du dernier tag dans le XPath (ex: 'ram:TypeCode' depuis '//ram:TypeCode')"""
    if not xpath:
        return ''
    # Nettoyer le XPath et récupérer le dernier élément
    xpath = xpath.strip()
    parts = xpath.split('/')
    for part in reversed(parts):
        part = part.strip()
        # Ignorer les parties vides et les conditions entre crochets
        if part and '[' not in part and part != '..':
            return part
    return parts[-1] if parts else ''

def get_xml_short_name(xpath):
    if not xpath:
        return ''
    parts = xpath.split('/')
    for part in reversed(parts):
        if ':' in part:
            return part.split(':')[1]
    return parts[-1] if parts else ''

def normalize_value(value):
    if not value:
        return ''
    value_str = str(value).strip()
    
    # Gestion spéciale des dates : normaliser tous les formats de date vers AAAAMMJJ
    # Formats supportés : JJ.MM.AAAA, JJ/MM/AAAA, AAAAMMJJ
    date_patterns = [
        (r'^(\d{2})[./](\d{2})[./](\d{4})$', lambda m: m.group(3) + m.group(2) + m.group(1)),  # JJ.MM.AAAA ou JJ/MM/AAAA -> AAAAMMJJ
        (r'^(\d{4})(\d{2})(\d{2})$', lambda m: m.group(1) + m.group(2) + m.group(3))  # AAAAMMJJ -> AAAAMMJJ (déjà bon)
    ]
    
    import re
    for pattern, transform in date_patterns:
        match = re.match(pattern, value_str)
        if match:
            return transform(match)
    
    # Si ce n'est pas une date, traiter comme avant
    if any(char.isdigit() for char in value_str):
        value_str = value_str.replace(' ', '')
        # Gérer le signe négatif en suffixe (format SAP : "1,000-" -> "-1,000")
        if value_str.endswith('-'):
            value_str = '-' + value_str[:-1]
        if '.' in value_str and ',' in value_str:
            value_str = value_str.replace('.', '').replace(',', '.')
        elif ',' in value_str and '.' not in value_str:
            value_str = value_str.replace(',', '.')
        elif value_str.count('.') > 1:
            value_str = value_str.replace('.', '')
        try:
            num_value = float(value_str)
            # Toujours formater de la même façon pour comparaison
            return f"{num_value:.10f}".rstrip('0').rstrip('.')
        except ValueError:
            pass
    return value_str.upper()

def perform_controls(field, rdi_value, xml_value, type_controle):
    # Vérifier si ce champ doit être ignoré
    if field.get('ignore') == 'Oui':
        return 'IGNORE', ['Contrôles ignorés'], ['Ce champ est configuré pour ignorer les erreurs']
    
    regles_testees = []
    details_erreurs = []
    status = 'OK'
    is_xml_only = (type_controle in ['cii', 'xmlonly'])

    if field.get('obligatoire') == 'Oui':
        regles_testees.append('Présence obligatoire')
        if is_xml_only:
            # En mode CII ou XML only : présence vérifiée dans le XML uniquement
            if not xml_value:
                status = 'ERREUR'
                details_erreurs.append('Champ obligatoire absent du XML')
        else:
            if not rdi_value:
                status = 'ERREUR'
                details_erreurs.append('Champ obligatoire absent du RDI')

    # Règles de Gestion (RDG)
    if field.get('rdg'):
        rdg_text = field['rdg']
        regles_testees.append(f"{rdg_text[:100]}..." if len(rdg_text) > 100 else rdg_text)

    # Contrôles CEGEDIM (non applicables en mode CII/XML only)
    if not is_xml_only:
        for controle in field.get('controles_cegedim', []):
            if controle.get('nature') == 'Presence':
                if not rdi_value:
                    status = 'ERREUR'
                    details_erreurs.append(f"{controle.get('ref')}: {controle.get('message', 'Controle CEGEDIM echoue')}")

    if type_controle == 'xml':
        if not xml_value and field.get('obligatoire') == 'Oui':
            status = 'ERREUR'
            details_erreurs.append('Absent du XML (obligatoire)')
        elif not xml_value and rdi_value:
            status = 'ERREUR'
            details_erreurs.append('Present dans RDI mais absent du XML')
        elif rdi_value and xml_value:
            rdi_normalized = normalize_value(rdi_value)
            xml_normalized = normalize_value(xml_value)
            if rdi_normalized != xml_normalized:
                status = 'ERREUR'
                details_erreurs.append(f"Valeurs differentes: RDI='{rdi_value}' vs XML='{xml_value}'")

    if not details_erreurs:
        details_erreurs = ['RAS']

    return status, regles_testees, details_erreurs


def normalize_category(categorie_bg, categorie_titre):
    """
    Normalise les catégories pour éviter les doublons.
    Retourne un tuple (categorie_bg_normalisee, categorie_titre_normalise)
    """
    # Nettoyer les espaces et mettre en majuscules pour la comparaison
    titre_upper = categorie_titre.upper().strip()
    bg_upper = categorie_bg.upper().strip()
    
    # Mapping de normalisation basé sur les mots-clés (avec emojis)
    normalizations = {
        'INFOS': ('BG-INFOS-GENERALES', '📄 INFORMATIONS GÉNÉRALES DE LA FACTURE'),
        'GÉNÉRALES': ('BG-INFOS-GENERALES', '📄 INFORMATIONS GÉNÉRALES DE LA FACTURE'),
        'GENERALES': ('BG-INFOS-GENERALES', '📄 INFORMATIONS GÉNÉRALES DE LA FACTURE'),
        'TOTAUX': ('BG-TOTAUX', '💰 TOTAUX DE LA FACTURE'),
        'TVA': ('BG-TVA', '🧾 DÉTAIL DE LA TVA'),
        'LIGNE': ('BG-LIGNES', '📋 LIGNES DE FACTURE'),
        'VENDEUR': ('BG-VENDEUR', '🏢 INFORMATIONS VENDEUR (RTE)'),
        'ACHETEUR': ('BG-ACHETEUR', '🛒 INFORMATIONS ACHETEUR (CLIENT)'),
    }
    
    # Chercher une correspondance dans le titre
    for keyword, (norm_bg, norm_titre) in normalizations.items():
        if keyword in titre_upper or keyword in bg_upper:
            return norm_bg, norm_titre
    
    # Si aucune correspondance, retourner tel quel
    return categorie_bg, categorie_titre


def get_category_order(categorie_bg):
    """Retourne l'ordre de tri des catégories"""
    order_map = {
        'BG-INFOS-GENERALES': 1,
        'BG-TOTAUX': 2,
        'BG-TVA': 3,
        'BG-LIGNES': 4,
        'BG-VENDEUR': 5,
        'BG-ACHETEUR': 6,
    }
    return order_map.get(categorie_bg, 999)


def _index_business_rules_by_schematron_id():
    """Renvoie un dict {schematron_id: business_rule} pour les règles qui en déclarent un."""
    try:
        rules_data = load_business_rules() or {}
    except Exception:
        return {}
    out = {}
    for rule in rules_data.get('rules', []):
        sid = (rule.get('schematron_id') or '').strip()
        if sid:
            out[sid] = rule
    return out


def apply_schematron(xml_content, results):
    """Applique le schematron officiel EN16931 et fusionne ses erreurs dans les résultats.

    Renvoie un dict de synthèse (compteurs + erreurs orphelines) à exposer à l'UI.
    En cas d'échec de validation, retourne un dict avec 'error'.
    """
    if not xml_content:
        return None

    try:
        errors = schematron_validate_xml(xml_content)
    except Exception as exc:
        print(f"[Schematron] échec validation: {exc}")
        return {'error': str(exc), 'total': 0, 'fatal': 0, 'warning': 0,
                'errors': [], 'orphans': [], 'rules': []}

    # Périmètre du mapping : ensemble des BT couverts par les résultats.
    # Inclut le préfixe court (BT-21) pour matcher les balises suffixées (BT-21-BAR).
    mapped_balises = set()
    for r in results:
        bal = (r.get('balise') or '').strip()
        if not bal:
            continue
        mapped_balises.add(bal)
        parts = bal.split('-')
        if len(parts) >= 3 and parts[0] == 'BT':
            mapped_balises.add('-'.join(parts[:2]))

    # Pont schematron ↔ règles métier + filtrage des erreurs hors mapping.
    rules_by_sid = _index_business_rules_by_schematron_id()
    kept = []
    skipped_out_of_scope = 0
    for err in errors:
        rule = rules_by_sid.get(err.get('rule_id'))
        if rule and not rule.get('enabled', True):
            continue  # règle désactivée par l'utilisateur

        # Filtrer les BT cités pour ne garder que ceux du mapping en cours.
        # Si la règle citait des BT mais aucun n'est mappé → erreur hors scope, on saute.
        original_bts = list(err.get('bts') or [])
        if mapped_balises and original_bts:
            scoped = [bt for bt in original_bts if bt in mapped_balises]
            if not scoped:
                skipped_out_of_scope += 1
                continue
            err['bts'] = scoped
            err['bts_full'] = original_bts  # pour ne pas perdre l'info de la règle officielle

        if rule:
            err['business_rule_name'] = rule.get('name')
            err['business_rule_id'] = rule.get('id')
            err['business_rule_category'] = rule.get('category') or 'EN16931 (Schematron)'
        kept.append(err)
    errors = kept

    by_bt = index_errors_by_bt(errors)
    matched_keys = set()

    def _attach(result, *, article_index=None):
        candidates = candidates_for_balise(result.get('balise', ''))
        if not candidates:
            return
        seen = set()
        for cand in candidates:
            for err in by_bt.get(cand, []):
                err_line = line_index_from_location(err.get('location', ''))
                if article_index is not None:
                    # Ligne article : on ne prend que les erreurs ciblant cette même ligne
                    if err_line is None or err_line != article_index:
                        continue
                else:
                    # Champ d'en-tête : on exclut les erreurs scopées à une ligne précise
                    if err_line is not None:
                        continue
                key = (err['rule_id'], err.get('location', ''), err.get('message', ''))
                if key in seen:
                    continue
                seen.add(key)
                matched_keys.add(key)
                result.setdefault('schematron_errors', []).append(err)

                # Si une règle métier porte ce schematron_id, on utilise son nom
                # convivial — sinon label générique préfixé pour l'identifier.
                rule_label = err.get('business_rule_name') or f"📜 Schematron {err['rule_id']}"
                if rule_label not in result['regles_testees']:
                    result['regles_testees'].append(rule_label)
                if 'RAS' in result['details_erreurs']:
                    result['details_erreurs'].remove('RAS')
                # Format compact en colonne ; le tooltip fournit le détail complet
                short_msg = err['message']
                # Retire le préfixe "[BR-XX]-" déjà présent dans le message officiel
                short_msg = re.sub(r'^\[' + re.escape(err['rule_id']) + r'\]-?', '', short_msg).strip()
                if len(short_msg) > 140:
                    short_msg = short_msg[:137].rstrip() + '…'
                msg = f"[{err['rule_id']}] {short_msg}"
                if msg not in result['details_erreurs']:
                    result['details_erreurs'].append(msg)
                if err['flag'] == 'fatal' and result['status'] == 'OK':
                    result['status'] = 'ERREUR'

    for r in results:
        _attach(r, article_index=r.get('article_index'))

    orphans = [
        e for e in errors
        if (e['rule_id'], e.get('location', ''), e.get('message', '')) not in matched_keys
    ]

    return {
        'total': len(errors),
        'fatal': sum(1 for e in errors if e['flag'] == 'fatal'),
        'warning': sum(1 for e in errors if e['flag'] != 'fatal'),
        'matched': len(errors) - len(orphans),
        'skipped_out_of_scope': skipped_out_of_scope,
        'rules': sorted({e['rule_id'] for e in errors}),
        'errors': errors,
        'orphans': orphans,
    }


def apply_business_rules(results, type_formulaire='simple'):
    """
    Applique les règles métiers configurables.
    Remplace l'ancienne fonction apply_contextual_controls hardcodée.
    """
    rules_data = load_business_rules()
    by_rdi_field = {r['rdi_field']: r for r in results if r.get('rdi_field')}

    # Index per-balise (toutes les occurrences) pour la résolution per-ligne
    rows_by_balise = {}
    for r in results:
        rows_by_balise.setdefault(r.get('balise'), []).append(r)

    def _resolve_obj(field, ftype='bt', line_id=None):
        """Résout l'objet résultat pour un champ, optionnellement sur une ligne précise.
        - RDI : doc-level uniquement (par rdi_field)
        - BT  : si line_id fourni et que le champ est multi-ligne, retourne la ligne ;
                sinon, fallback doc-level (1 seule ligne) ou la dernière (compat)."""
        if not field:
            return None
        if ftype == 'rdi':
            return by_rdi_field.get(field)
        rows = rows_by_balise.get(field, [])
        if not rows:
            return None
        if line_id is None:
            return rows[-1]
        for r in rows:
            if (r.get('article_line_id') or '') == line_id:
                return r
        # Fallback : champ doc-level (1 seule occurrence sans line_id)
        if len(rows) == 1 and not (rows[0].get('article_line_id') or ''):
            return rows[0]
        return None

    def evaluate_condition(cond, line_id=None):
        """Évalue une condition. Si line_id est fourni, les champs multi-lignes
        sont résolus sur cette ligne (les champs doc-level restent doc-level)."""
        field = cond.get('field')
        operator = cond.get('operator')
        value = cond.get('value', '')
        field_type = cond.get('field_type', 'bt')

        result_obj = _resolve_obj(field, field_type, line_id)
        if not result_obj:
            return False

        field_value = result_obj.get('rdi', '').strip() or result_obj.get('xml', '').strip()
        
        if operator == 'equals':
            return field_value.upper() == value.upper()
        elif operator == 'not_equals':
            return field_value.upper() != value.upper()
        elif operator == 'contains':
            return value.upper() in field_value.upper()
        elif operator == 'not_contains':
            return value.upper() not in field_value.upper()
        elif operator == 'starts_with':
            return field_value.upper().startswith(value.upper())
        elif operator == 'not_starts_with':
            return not field_value.upper().startswith(value.upper())
        elif operator == 'less_than':
            try:
                return _parse_amount(field_value) < float(value)
            except:
                return False
        elif operator == 'greater_than':
            try:
                return _parse_amount(field_value) > float(value)
            except:
                return False
        elif operator == 'is_empty':
            return not field_value
        elif operator == 'is_not_empty':
            return bool(field_value)
        
        return False
    
    def _parse_amount(s):
        """Parse un montant en float, gère le format français (1.234,56), anglais (1234.56),
        et le format SAP avec signe négatif en fin (37.348,140000-)."""
        s = s.strip().replace('\xa0', '').replace(' ', '')
        if not s:
            return 0.0
        negative = False
        if s.endswith('-'):
            negative = True
            s = s[:-1].rstrip()
        elif s.startswith('-'):
            negative = True
            s = s[1:].lstrip()
        if ',' in s and '.' in s:
            # Format français : point = séparateur de milliers, virgule = décimale
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        value = float(s)
        return -value if negative else value

    def apply_action(action, line_id=None):
        """Applique une action. Si line_id est fourni, la cible et les opérandes
        per-ligne sont résolus sur cette ligne."""
        action_type = action.get('type')
        target_field = action.get('field')
        field_type = action.get('field_type', 'bt')

        target = _resolve_obj(target_field, field_type, line_id)
        if not target:
            return
        if target.get('status') in ('AMBIGU', 'IGNORE'):
            return

        rule_name = action.get('reason', 'Règle métier')
        
        if action_type == 'make_mandatory':
            target['obligatoire'] = 'Oui'
            regle_label = f'Règle: {rule_name}'
            if regle_label not in target['regles_testees']:
                target['regles_testees'].insert(0, regle_label)
            
            if not target.get('rdi', '').strip() and not target.get('xml', '').strip():
                target['status'] = 'ERREUR'
                if 'RAS' in target['details_erreurs']:
                    target['details_erreurs'].remove('RAS')
                error_msg = f'Règle métier "{rule_name}" non respectée : champ obligatoire absent'
                target['details_erreurs'].insert(0, error_msg)
        
        elif action_type == 'must_equal':
            expected = action.get('value', '')
            actual = target.get('rdi', '').strip() or target.get('xml', '').strip()
            regle_label = f'Valeur imposée = "{expected}"'
            if regle_label not in target['regles_testees']:
                target['regles_testees'].append(regle_label)

            if actual.upper() != expected.upper():
                target['status'] = 'ERREUR'
                if 'RAS' in target['details_erreurs']:
                    target['details_erreurs'].remove('RAS')
                msg = f'Règle métier "{rule_name}" non respectée : attendu "{expected}", trouvé "{actual}"'
                if msg not in target['details_erreurs']:
                    target['details_erreurs'].append(msg)
        
        elif action_type == 'make_optional':
            target['obligatoire'] = 'Non'
            regle_label = f'Règle: {rule_name}'
            if regle_label not in target['regles_testees']:
                target['regles_testees'].insert(0, regle_label)
            # Si le champ est vide (RDI et XML), toutes les erreurs sont
            # des erreurs de présence — on les efface intégralement.
            # Si le champ est renseigné, on conserve les éventuelles erreurs
            # de valeur (divergence RDI/XML, etc.).
            if not target.get('rdi', '').strip() and not target.get('xml', '').strip():
                target['details_erreurs'] = ['RAS']
                target['status'] = 'OK'

        elif action_type == 'must_be_negative':
            try:
                value_str = target.get('rdi', '').strip() or target.get('xml', '').strip() or '0'
                value = _parse_amount(value_str)
                regle_label = 'Doit être négatif'
                if regle_label not in target['regles_testees']:
                    target['regles_testees'].append(regle_label)

                if value >= 0:
                    target['status'] = 'ERREUR'
                    if 'RAS' in target['details_erreurs']:
                        target['details_erreurs'].remove('RAS')
                    msg = f'Règle métier "{rule_name}" non respectée : valeur doit être négative (trouvée: {value})'
                    if msg not in target['details_erreurs']:
                        target['details_erreurs'].append(msg)
            except:
                pass

        elif action_type == 'must_equal_sum':
            # Doc-level : opérandes résolus globalement (pas per-ligne)
            field1 = action.get('field1', '')
            field2 = action.get('field2', '')
            src1 = _resolve_obj(field1, 'bt', None)
            src2 = _resolve_obj(field2, 'bt', None)
            try:
                def _to_float(obj):
                    if not obj:
                        return 0.0
                    s = obj.get('rdi', '').strip() or obj.get('xml', '').strip() or '0'
                    return _parse_amount(s)
                val1 = _to_float(src1)
                val2 = _to_float(src2)
                expected = round(val1 + val2, 10)
                val_target_str = target.get('rdi', '').strip() or target.get('xml', '').strip() or '0'
                val_target = _parse_amount(val_target_str)
                tolerance = 0.005
                ecart = abs(val_target - expected)
                status = '✓' if ecart <= tolerance else '✗'
                regle_label = f'Doit égaler {field1} + {field2}'
                if regle_label not in target['regles_testees']:
                    target['regles_testees'].append(regle_label)
                detail_lines = []
                detail_lines.append(f'🔎 {rule_name}')
                detail_lines.append(f'{target_field} doit égaler {field1} + {field2}.')
                detail_lines.append('═══════════════════════════════════════')
                detail_lines.append('📋 Opérandes :')
                detail_lines.append(f'  {field1} = {val1}')
                detail_lines.append(f'  {field2} = {val2}')
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('🧮 Vérification :')
                detail_lines.append(f'  {field1} + {field2} = {expected}')
                detail_lines.append(f'  {target_field} = {val_target}')
                detail_lines.append(f'  Écart = {ecart:.4f} (tolérance {tolerance}) {status}')
                if 'rule_details' not in target:
                    target['rule_details'] = {}
                target['rule_details'][rule_name] = detail_lines
                if ecart > tolerance:
                    target['status'] = 'ERREUR'
                    if 'RAS' in target['details_erreurs']:
                        target['details_erreurs'].remove('RAS')
                    msg = (f'Règle métier "{rule_name}" non respectée : '
                           f'attendu {expected} ({field1}={val1} + {field2}={val2}), '
                           f'trouvé {val_target}')
                    if msg not in target['details_erreurs']:
                        target['details_erreurs'].append(msg)
            except:
                pass

        elif action_type == 'must_equal_sum_of_all':
            # Additionne toutes les occurrences de sum_field (ex: tous les BT-129 de chaque article)
            sum_field = action.get('sum_field', '')
            try:
                tolerance = float(str(action.get('tolerance', '0.01')).replace(',', '.') or '0.01')
            except:
                tolerance = 0.01
            try:
                all_items = [r for r in results if r.get('balise') == sum_field]
                total = 0.0
                operands_lines = []
                n = 0
                for item in all_items:
                    item_line_id = item.get('article_line_id', '')
                    item_name = item.get('article_name', '')
                    xml_all = item.get('xml_all') or []
                    # Champ d'en-tête multi-valué (ex: BT-117 par catégorie de TVA)
                    if not item_line_id and len(xml_all) > 1:
                        for i, v in enumerate(xml_all):
                            label = f'{sum_field} #{i + 1}'
                            try:
                                total += _parse_amount(v)
                                operands_lines.append(f'  {label} : {v}')
                                n += 1
                            except:
                                operands_lines.append(f'  {label} : {v} (non numérique, ignoré)')
                        continue
                    s = item.get('rdi', '').strip() or item.get('xml', '').strip() or '0'
                    if item_line_id:
                        label = f'Ligne {item_line_id}'
                        if item_name:
                            label += f' ({item_name})'
                    else:
                        label = sum_field
                    try:
                        v = _parse_amount(s)
                        total += v
                        operands_lines.append(f'  {label} : {sum_field} = {s}')
                        n += 1
                    except:
                        operands_lines.append(f'  {label} : {sum_field} = {s} (non numérique, ignoré)')
                total = round(total, 10)
                val_target_str = target.get('rdi', '').strip() or target.get('xml', '').strip() or '0'
                val_target = _parse_amount(val_target_str)
                ecart = abs(val_target - total)
                status = '✓' if ecart <= tolerance else '✗'
                regle_label = f'Doit égaler la somme des {n} {sum_field}'
                if regle_label not in target['regles_testees']:
                    target['regles_testees'].append(regle_label)

                detail_lines = []
                detail_lines.append(f'🔎 {rule_name}')
                detail_lines.append(f'{target_field} doit égaler la somme des {sum_field}.')
                detail_lines.append('═══════════════════════════════════════')
                detail_lines.append(f'📋 Opérandes ({n} {sum_field}) :')
                detail_lines.extend(operands_lines or ['  (aucun)'])
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('🧮 Vérification :')
                detail_lines.append(f'  Σ {sum_field} = {total}')
                detail_lines.append(f'  {target_field} = {val_target}')
                detail_lines.append(f'  Écart = {ecart:.4f} (tolérance {tolerance}) {status}')
                if 'rule_details' not in target:
                    target['rule_details'] = {}
                target['rule_details'][rule_name] = detail_lines
                if ecart > tolerance:
                    target['status'] = 'ERREUR'
                    if 'RAS' in target['details_erreurs']:
                        target['details_erreurs'].remove('RAS')
                    msg = (f'Règle métier "{rule_name}" non respectée : '
                           f'Σ {n} {sum_field} = {total}, '
                           f'trouvé {val_target} '
                           f'(écart {ecart:.4f}, tolérance {tolerance})')
                    if msg not in target['details_erreurs']:
                        target['details_erreurs'].append(msg)
            except:
                pass

        elif action_type == 'must_equal_sum_of_all_minus_plus':
            # target = Σ sum_field − minus_field + plus_field (ex: BT-109 = Σ BT-131 − BT-107 + BT-108)
            sum_field = action.get('sum_field', '')
            minus_field = action.get('minus_field', '')
            plus_field = action.get('plus_field', '')
            try:
                tolerance = float(str(action.get('tolerance', '0.01')).replace(',', '.') or '0.01')
            except:
                tolerance = 0.01
            try:
                def _val_of(field_name):
                    obj = _resolve_obj(field_name, 'bt', None)
                    if not obj:
                        return 0.0
                    s = obj.get('rdi', '').strip() or obj.get('xml', '').strip() or '0'
                    try:
                        return _parse_amount(s)
                    except:
                        return 0.0

                sum_items = [r for r in results if r.get('balise') == sum_field]
                sum_total = 0.0
                operands_lines = []
                n = 0
                for item in sum_items:
                    item_line_id = item.get('article_line_id', '')
                    item_name = item.get('article_name', '')
                    xml_all = item.get('xml_all') or []
                    if not item_line_id and len(xml_all) > 1:
                        for i, v in enumerate(xml_all):
                            label = f'{sum_field} #{i + 1}'
                            try:
                                sum_total += _parse_amount(v)
                                operands_lines.append(f'  {label} : {v}')
                                n += 1
                            except:
                                operands_lines.append(f'  {label} : {v} (non numérique, ignoré)')
                        continue
                    s = item.get('rdi', '').strip() or item.get('xml', '').strip() or '0'
                    if item_line_id:
                        label = f'Ligne {item_line_id}'
                        if item_name:
                            label += f' ({item_name})'
                    else:
                        label = sum_field
                    try:
                        sum_total += _parse_amount(s)
                        operands_lines.append(f'  {label} : {sum_field} = {s}')
                        n += 1
                    except:
                        operands_lines.append(f'  {label} : {sum_field} = {s} (non numérique, ignoré)')
                sum_total = round(sum_total, 10)

                val_minus = _val_of(minus_field)
                val_plus = _val_of(plus_field)
                expected = round(sum_total - val_minus + val_plus, 10)
                val_target_str = target.get('rdi', '').strip() or target.get('xml', '').strip() or '0'
                val_target = _parse_amount(val_target_str)
                ecart = abs(val_target - expected)
                status = '✓' if ecart <= tolerance else '✗'

                regle_label = f'Doit égaler Σ {n} {sum_field} − {minus_field} + {plus_field}'
                if regle_label not in target['regles_testees']:
                    target['regles_testees'].append(regle_label)

                detail_lines = []
                detail_lines.append(f'🔎 {rule_name}')
                detail_lines.append(f'{target_field} doit égaler Σ {sum_field} − {minus_field} + {plus_field}.')
                detail_lines.append('═══════════════════════════════════════')
                detail_lines.append(f'📋 Σ {sum_field} ({n} occurrence(s)) :')
                detail_lines.extend(operands_lines or ['  (aucune)'])
                detail_lines.append(f'  ───────────────')
                detail_lines.append(f'  Σ {sum_field} = {sum_total}')
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('📋 Ajustements document :')
                detail_lines.append(f'  − {minus_field} (remises) = {val_minus}')
                detail_lines.append(f'  + {plus_field} (charges) = {val_plus}')
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('🧮 Vérification :')
                detail_lines.append(f'  Σ {sum_field} − {minus_field} + {plus_field} = {expected}')
                detail_lines.append(f'  {target_field} = {val_target}')
                detail_lines.append(f'  Écart = {ecart:.4f} (tolérance {tolerance}) {status}')
                if 'rule_details' not in target:
                    target['rule_details'] = {}
                target['rule_details'][rule_name] = detail_lines
                if ecart > tolerance:
                    target['status'] = 'ERREUR'
                    if 'RAS' in target['details_erreurs']:
                        target['details_erreurs'].remove('RAS')
                    msg = (f'Règle métier "{rule_name}" non respectée : '
                           f'attendu {expected} (Σ {n} {sum_field} = {sum_total} − {minus_field} = {val_minus} + {plus_field} = {val_plus}), '
                           f'trouvé {val_target} (écart {ecart:.4f}, tolérance {tolerance})')
                    if msg not in target['details_erreurs']:
                        target['details_erreurs'].append(msg)
            except:
                pass

        elif action_type == 'vat_breakdown_detail':
            # BR-S-08 : explicite le calcul de la cohérence TVA par ventilation.
            # Affiche, par taux : la base déclarée (BT-116) vs la somme des
            # BT-131 des lignes 'Standard rated' au même taux.
            try:
                def _xml_all_of(balise):
                    obj = next((r for r in results if r.get('balise') == balise), None)
                    if not obj:
                        return []
                    xa = obj.get('xml_all') or []
                    if xa:
                        return xa
                    v = (obj.get('xml') or obj.get('rdi') or '').strip()
                    return [v] if v else []

                bt118_all = _xml_all_of('BT-118')
                bt119_all = _xml_all_of('BT-119')
                bt116_all = _xml_all_of('BT-116')
                bt117_all = _xml_all_of('BT-117')

                # Regroupe les champs par article_index : line_id, name,
                # BT-131 (montant), BT-151 (catégorie TVA), BT-152 (taux).
                articles_data = {}
                for r in results:
                    ai = r.get('article_index')
                    if ai is None:
                        continue
                    entry = articles_data.setdefault(ai, {
                        'line_id': r.get('article_line_id') or '',
                        'name': r.get('article_name') or '',
                        'bt131': '', 'bt151': '', 'bt152': '',
                    })
                    val = (r.get('rdi') or r.get('xml') or '').strip()
                    bal = r.get('balise')
                    if bal == 'BT-131':
                        entry['bt131'] = val
                    elif bal == 'BT-151':
                        entry['bt151'] = val
                    elif bal == 'BT-152':
                        entry['bt152'] = val

                def _norm_rate(s):
                    """Normalise un taux pour comparaison (ex: '20', '20.00', ' 20 ' → '20.0')."""
                    s = (s or '').strip().replace(',', '.')
                    try:
                        return f'{float(s):g}'
                    except:
                        return s

                detail_lines = []
                detail_lines.append('🔎 BR-S-08 — Cohérence ventilation TVA "Standard rated"')
                detail_lines.append('Pour chaque taux TVA, BT-116 (base imposable) doit égaler')
                detail_lines.append('Σ BT-131 (lignes "S" au même taux) + Σ BT-99 − Σ BT-92.')
                detail_lines.append('═══════════════════════════════════════')

                # 1. Ventilations TVA déclarées en en-tête
                detail_lines.append('📋 Ventilations TVA déclarées (en-tête) :')
                n_breakdowns = max(len(bt118_all), len(bt119_all), len(bt116_all), len(bt117_all))
                breakdowns_s = []  # liste de (rate_norm, base_float, vat_float)
                if n_breakdowns == 0:
                    detail_lines.append('  (aucune)')
                for i in range(n_breakdowns):
                    cat = (bt118_all[i] if i < len(bt118_all) else '').strip()
                    rate = (bt119_all[i] if i < len(bt119_all) else '').strip()
                    base = (bt116_all[i] if i < len(bt116_all) else '').strip()
                    vat = (bt117_all[i] if i < len(bt117_all) else '').strip()
                    is_s = cat.upper() == 'S'
                    marker = ' ◀ Standard rated' if is_s else ''
                    detail_lines.append(
                        f'  #{i + 1} : Cat={cat or "?"} | Taux={rate or "?"}% | '
                        f'Base BT-116={base or "?"} | TVA BT-117={vat or "?"}{marker}'
                    )
                    if is_s:
                        try:
                            base_f = _parse_amount(base)
                        except:
                            base_f = 0.0
                        try:
                            vat_f = _parse_amount(vat)
                        except:
                            vat_f = 0.0
                        breakdowns_s.append((_norm_rate(rate), base_f, vat_f))

                # 2. Lignes "Standard rated"
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('📦 Lignes de facture "Standard rated" (BT-151 = "S") :')
                s_lines = []
                for ai in sorted(articles_data.keys()):
                    a = articles_data[ai]
                    if a['bt151'].upper() == 'S' and a['bt131']:
                        s_lines.append((a['line_id'], a['name'], a['bt131'], a['bt152']))
                if not s_lines:
                    detail_lines.append('  (aucune)')
                bt131_total = 0.0
                has_bt152 = False
                for lid, name, amt, rate in s_lines:
                    label = f'Ligne {lid}' if lid else 'Ligne ?'
                    if name:
                        label += f' ({name})'
                    rate_part = f' | Taux BT-152={rate}%' if rate else ''
                    if rate:
                        has_bt152 = True
                    detail_lines.append(f'  {label}{rate_part} : BT-131 = {amt}')
                    try:
                        bt131_total += _parse_amount(amt)
                    except:
                        pass
                detail_lines.append('  ───────────────')
                detail_lines.append(f'  Σ BT-131 (lignes "S") = {round(bt131_total, 2)}')

                # 3. Comparaison
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('🧮 Vérification :')
                detail_lines.append('  (BT-99 charges et BT-92 remises au niveau document')
                detail_lines.append('   ne sont pas dans le mapping — supposés = 0)')
                if has_bt152:
                    sum_by_rate = {}
                    lines_by_rate = {}
                    for _, _, amt, rate in s_lines:
                        key = _norm_rate(rate)
                        try:
                            sum_by_rate[key] = sum_by_rate.get(key, 0.0) + _parse_amount(amt)
                        except:
                            pass
                        lines_by_rate.setdefault(key, []).append(amt)
                    for rate_key, base_val, vat_val in breakdowns_s:
                        lines_total = round(sum_by_rate.get(rate_key, 0.0), 2)
                        ecart = abs(lines_total - base_val)
                        status = '✓' if ecart <= 0.01 else '✗'
                        detail_lines.append(
                            f'  Taux {rate_key}% : '
                            f'Σ BT-131(S) = {lines_total} '
                            f'vs BT-116 = {round(base_val, 2)} '
                            f'→ écart {ecart:.2f} {status}'
                        )
                    rates_in_lines = set(sum_by_rate.keys())
                    rates_in_header = {r for r, _, _ in breakdowns_s}
                    orphan = rates_in_lines - rates_in_header
                    for r in sorted(orphan):
                        detail_lines.append(
                            f'  ⚠️ Taux {r}% présent en ligne mais absent de la ventilation : '
                            f'Σ BT-131 = {round(sum_by_rate[r], 2)}'
                        )
                else:
                    base_s_total = round(sum(b for _, b, _ in breakdowns_s), 2)
                    ecart = abs(bt131_total - base_s_total)
                    status = '✓' if ecart <= 0.01 else '✗'
                    detail_lines.append('  (BT-152 absent du mapping — vérification globale)')
                    detail_lines.append(
                        f'  Σ BT-131 (S) = {round(bt131_total, 2)} '
                        f'vs Σ BT-116 (Cat=S) = {base_s_total} '
                        f'→ écart {ecart:.2f} {status}'
                    )

                regle_label = 'Détail ventilation TVA (BR-S-08)'
                if regle_label not in target['regles_testees']:
                    target['regles_testees'].append(regle_label)
                if 'rule_details' not in target:
                    target['rule_details'] = {}
                target['rule_details'][rule_name] = detail_lines
            except Exception:
                pass

        elif action_type == 'must_equal_product':
            # Per-ligne : opérandes résolus sur la même ligne que la cible
            field1 = action.get('field1', '')
            field2 = action.get('field2', '')
            try:
                tolerance = float(str(action.get('tolerance', '0.01')).replace(',', '.') or '0.01')
            except:
                tolerance = 0.01
            src1 = _resolve_obj(field1, 'bt', line_id)
            src2 = _resolve_obj(field2, 'bt', line_id)
            try:
                def _to_float(obj):
                    if not obj:
                        return 0.0
                    s = obj.get('rdi', '').strip() or obj.get('xml', '').strip() or '0'
                    return _parse_amount(s)
                val1 = _to_float(src1)
                val2 = _to_float(src2)
                expected = round(val1 * val2, 10)
                val_target_str = target.get('rdi', '').strip() or target.get('xml', '').strip() or '0'
                val_target = _parse_amount(val_target_str)
                ecart = abs(val_target - expected)
                status = '✓' if ecart <= tolerance else '✗'
                line_suffix = f' (Ligne {line_id})' if line_id else ''
                regle_label = f'Doit égaler {field1} × {field2}'
                if regle_label not in target['regles_testees']:
                    target['regles_testees'].append(regle_label)
                detail_lines = []
                detail_lines.append(f'🔎 {rule_name}{line_suffix}')
                detail_lines.append(f'{target_field} doit égaler {field1} × {field2}.')
                detail_lines.append('═══════════════════════════════════════')
                detail_lines.append('📋 Opérandes :')
                detail_lines.append(f'  {field1} = {val1}')
                detail_lines.append(f'  {field2} = {val2}')
                detail_lines.append('───────────────────────────────────────')
                detail_lines.append('🧮 Vérification :')
                detail_lines.append(f'  {field1} × {field2} = {expected}')
                detail_lines.append(f'  {target_field} = {val_target}')
                detail_lines.append(f'  Écart = {ecart:.4f} (tolérance {tolerance}) {status}')
                if 'rule_details' not in target:
                    target['rule_details'] = {}
                target['rule_details'][rule_name] = detail_lines
                if ecart > tolerance:
                    target['status'] = 'ERREUR'
                    if 'RAS' in target['details_erreurs']:
                        target['details_erreurs'].remove('RAS')
                    msg = (f'Règle métier "{rule_name}" non respectée : '
                           f'attendu {expected} ({field1}={val1} × {field2}={val2}), '
                           f'trouvé {val_target} '
                           f'(écart {ecart:.4f}, tolérance {tolerance})')
                    if msg not in target['details_erreurs']:
                        target['details_erreurs'].append(msg)
            except:
                pass

    # Actions dont la cible peut être per-ligne (champ d'article)
    PER_LINE_ELIGIBLE_TYPES = {
        'make_mandatory', 'make_optional', 'must_equal',
        'must_be_negative', 'must_equal_product'
    }

    def _per_line_target_lines(action):
        """Si la cible de l'action est multi-occurrences (champ d'article), retourne
        la liste triée des line_ids ; sinon []."""
        if action.get('type') not in PER_LINE_ELIGIBLE_TYPES:
            return []
        if action.get('field_type', 'bt') != 'bt':
            return []
        field = action.get('field')
        if not field:
            return []
        rows = rows_by_balise.get(field, [])
        if len(rows) <= 1:
            return []
        line_ids = sorted({(r.get('article_line_id') or '') for r in rows if r.get('article_line_id')})
        return line_ids

    # Parcourir toutes les règles actives
    for rule in rules_data.get('rules', []):
        if not rule.get('enabled', True):
            continue

        # Vérifier si la règle s'applique à ce type de formulaire
        applicable_forms = rule.get('applicable_forms', [])
        if applicable_forms and type_formulaire not in applicable_forms:
            continue  # Règle non applicable à ce formulaire

        rule_name = rule.get('name', 'Règle métier')
        actions = rule.get('actions', [])
        conditions = rule.get('conditions', [])

        # Collecter tous les line_ids touchés par les actions per-ligne de la règle.
        # Si vide → règle doc-level (une seule passe avec line_id=None).
        all_line_ids = set()
        for action in actions:
            for lid in _per_line_target_lines(action):
                all_line_ids.add(lid)
        contexts = sorted(all_line_ids) if all_line_ids else [None]

        for ctx_line_id in contexts:
            # Évaluer les conditions dans le contexte de cette ligne (AND logique)
            if not all(evaluate_condition(c, ctx_line_id) for c in conditions):
                continue

            # Annoter les champs déclencheurs (conditions) avec le nom de la règle
            for cond in conditions:
                trigger = _resolve_obj(cond.get('field'), cond.get('field_type', 'bt'), ctx_line_id)
                if trigger is not None:
                    regle_label = f'Règle déclenchée : {rule_name}'
                    if regle_label not in trigger['regles_testees']:
                        trigger['regles_testees'].append(regle_label)

            # Appliquer les actions
            for action in actions:
                action['reason'] = rule_name
                apply_action(action, ctx_line_id)

    return results


HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link rel="icon" type="image/x-icon" href="__URL_PREFIX__/img/IcoSite.ico">
<link rel="icon" type="image/png" href="__URL_PREFIX__/img/AppLogo_V2.png">
<title>Facturix - La potion magique pour des factures certifiées !</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Bangers&display=swap');
/* === RESET & BASE === */
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Outfit',Arial,sans-serif;background:#3a5282;min-height:100vh;display:flex;align-items:stretch;gap:0}
.sidebar{position:sticky;top:0;height:100vh;flex-shrink:0}
.main-wrap{flex:1;padding:20px;min-width:0;overflow-y:auto}
.container{max-width:1400px;margin:0 auto;background:#f8fafc;border-radius:20px;overflow:hidden;box-shadow:0 25px 60px rgba(0,0,0,0.25)}
@media(max-width:900px){.sidebar{display:none}.main-wrap{padding:10px}}
/* === HEADER === */
.header{background:#506aab;color:#fff;padding:3px 30px 0px 30px;display:flex;align-items:center;gap:18px;justify-content:space-between}
.header-left{display:flex;align-items:center;gap:18px}
.header-logo{height:80px;width:auto;object-fit:contain;flex-shrink:0;display:block}
.header-banner{flex-shrink:0;cursor:pointer;margin-bottom:0;margin-top:0;transition:transform 0.2s;align-self:flex-end;display:flex;align-items:flex-end}
.header-banner:hover{transform:scale(1.05)}
.header-banner img{height:100px;width:auto;display:block}
.header-text h1{font-size:1.35em;margin:0;font-weight:400;letter-spacing:0.01em;display:flex;align-items:flex-end;gap:0.15em}
.header-text h1 .title-facturix{font-family:'Bangers',cursive;font-size:2em;letter-spacing:0.08em;text-shadow:1px 1px 0 rgba(0,0,0,0.25);line-height:1}
.header-text h1 .title-subtitle{font-size:0.75em;padding-bottom:0.07em}
.version{font-size:0.78em;opacity:0.65;margin-top:4px;font-weight:400}
/* === TABS === */
.tabs{display:flex;background:#fff;border-bottom:1px solid #e2e8f0;padding:0 20px;gap:2px}
.tab{padding:13px 22px;cursor:pointer;border:none;background:transparent;font-weight:600;font-size:0.9em;color:#64748b;font-family:'Outfit',Arial,sans-serif;border-bottom:3px solid transparent;transition:all 0.2s;margin-bottom:-1px}
.tab:hover{color:#4f46e5;background:#f1f5ff}
.tab.active{color:#4f46e5;border-bottom-color:#4f46e5}
/* === TAB CONTENT === */
.tab-content{display:none;padding:26px 30px;background:#f8fafc}
.tab-content.active{display:block}
/* === SECTIONS / CARDS === */
.section{background:#fff;border-radius:12px;padding:18px 22px;margin-bottom:12px;border:1px solid #e2e8f0;box-shadow:0 1px 4px rgba(0,0,0,0.04)}
.section h2{font-size:1.05rem;font-weight:700;color:#1e293b;margin-bottom:14px}
.section h3{font-size:0.95rem;font-weight:600;color:#1e293b;margin-bottom:10px}
/* === FORMS === */
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.form-group{display:flex;flex-direction:column}
.form-group label{font-weight:600;font-size:0.78rem;color:#475569;margin-bottom:5px;letter-spacing:0.05em;text-transform:uppercase}
.form-group select,.form-group input[type=text],.form-group input[type=file],.form-group textarea{padding:8px 11px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.9em;font-family:'Outfit',Arial,sans-serif;color:#1e293b;transition:border-color 0.2s,box-shadow 0.2s;background:#fff}
.form-group select:focus,.form-group input:focus,.form-group textarea:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.12)}
.form-group textarea{min-height:80px;font-family:'JetBrains Mono',monospace;font-size:0.87em;resize:vertical}
/* === HELP BOX === */
.help-box{background:#eef2ff;border-left:3px solid #667eea;padding:11px 15px;margin:10px 0;border-radius:0 8px 8px 0;font-size:0.87em;color:#3730a3;line-height:1.55}
/* === MAIN BUTTON === */
.btn{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:12px 28px;border:none;border-radius:10px;font-size:1em;font-weight:700;cursor:pointer;width:100%;font-family:'Outfit',Arial,sans-serif;transition:all 0.2s;box-shadow:0 3px 10px rgba(102,126,234,0.28);letter-spacing:0.03em}
.btn:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(102,126,234,0.38)}
/* === ACTION BUTTONS === */
.btn-secondary{background:#fff;color:#667eea;padding:8px 15px;border:1.5px solid #c7d2fe;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s}
.btn-secondary:hover{background:#eef2ff;border-color:#818cf8;color:#4f46e5}
.btn-add{background:linear-gradient(135deg,#10b981 0%,#059669 100%);color:#fff;padding:8px 15px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s;box-shadow:0 2px 6px rgba(16,185,129,0.18)}
.btn-add:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(16,185,129,0.3)}
.btn-download{background:linear-gradient(135deg,#f59e0b 0%,#d97706 100%);color:#fff;padding:8px 15px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s}
.btn-save-version{background:linear-gradient(135deg,#8b5cf6 0%,#7c3aed 100%);color:#fff;padding:8px 15px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s}
.btn-restore{background:linear-gradient(135deg,#3b82f6 0%,#2563eb 100%);color:#fff;padding:8px 15px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s}
.btn-clear{background:#64748b;color:#fff;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:0.82em;white-space:nowrap;font-family:'Outfit',Arial,sans-serif;transition:background 0.15s}
.btn-clear:hover{background:#475569}
/* === LOADING === */
.loading{display:none;text-align:center;padding:30px}
.spinner{border:3px solid #e2e8f0;border-top:3px solid #667eea;border-radius:50%;width:42px;height:42px;animation:spin 0.75s linear infinite;margin:0 auto 10px}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.results{display:none}
/* === PROGRESS BAR === */
.progress-section{background:#fff;border-radius:12px;padding:13px 22px;margin-bottom:12px;border:1px solid #e2e8f0;box-shadow:0 1px 4px rgba(0,0,0,0.04)}
.progress-label-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.progress-label-row h3{margin:0;color:#1e293b;font-size:0.82em;font-weight:700;text-transform:uppercase;letter-spacing:0.06em}
.progress-pct{font-size:1.4em;font-weight:700;color:#667eea}
.progress-track{background:#e2e8f0;border-radius:999px;height:14px;position:relative;cursor:pointer;overflow:hidden}
.gaulois-overlay{display:none;position:fixed;z-index:9999;pointer-events:none}
.gaulois-overlay.visible{display:flex;flex-direction:column;align-items:center}
.gaulois-card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:12px 18px;box-shadow:0 12px 36px rgba(0,0,0,0.2);display:flex;flex-direction:column;align-items:center;gap:10px;max-width:420px}
.gaulois-card img{width:338px;height:338px;object-fit:contain;border-radius:12px}
.progress-fill{height:100%;border-radius:999px;transition:width 0.9s ease;min-width:2px}
.pct-0{background:linear-gradient(90deg,#ef4444,#f87171)}
.pct-25{background:linear-gradient(90deg,#f97316,#fb923c)}
.pct-50{background:linear-gradient(90deg,#f59e0b,#fbbf24)}
.pct-75{background:linear-gradient(90deg,#10b981,#34d399)}
/* === STAT CARDS === */
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:12px}
.stat-card{background:#fff;padding:13px 10px;border-radius:10px;text-align:center;border:1px solid #e2e8f0;font-size:0.8em;color:#64748b;font-weight:500;transition:transform 0.18s,box-shadow 0.18s;box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.stat-card:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,0.08)}
.stat-value{font-size:1.7em;font-weight:700;margin-top:4px;display:block}
.ok .stat-value{color:#10b981}
.erreur .stat-value{color:#ef4444}
.ambigu .stat-value{color:#d97706}
.ignore .stat-value{color:#94a3b8}
/* === SEARCH BOX === */
.search-box{display:flex;flex-direction:column;gap:8px;padding:12px 16px;background:#fff;border-radius:10px;border:1px solid #e2e8f0}
.search-box-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.search-box label{font-weight:600;font-size:0.84em;color:#475569;white-space:nowrap}
.search-box input{flex:1;max-width:200px;padding:7px 11px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:0.88em;font-family:'Outfit',Arial,sans-serif;transition:border-color 0.2s,box-shadow 0.2s}
.search-box input:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.12)}
/* === RESULTS CATEGORIES === */
.category{background:#fff;border-radius:10px;margin-bottom:9px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.03)}
.category.hidden{display:none}
.category-header{background:linear-gradient(135deg,#1e1b4b 0%,#4338ca 100%);color:#fff;padding:12px 18px;cursor:pointer;display:flex;justify-content:space-between;font-weight:600;font-size:0.88em;letter-spacing:0.02em}
.category-content{max-height:0;overflow:hidden;transition:max-height 0.3s}
.category-content.open{max-height:50000px}
/* === RESULTS TABLE === */
table.main-table{width:100%;border-collapse:collapse;font-size:0.89em}
table.main-table th{background:#f1f5f9;color:#475569;padding:7px 10px;text-align:left;font-weight:700;font-size:0.78em;border-bottom:2px solid #e2e8f0;text-transform:uppercase;letter-spacing:0.05em}
table.main-table td{padding:5px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle;line-height:1.35;color:#1e293b}
table.main-table tr.data-row:hover{background:#f8fafc}
table.main-table ul{margin:0;padding-left:14px}
table.main-table li{margin:1px 0}
.col-status{width:28px;text-align:center;font-size:1.1em;padding:4px!important}
.col-bt{width:70px}
.col-bt .bt-oblig{border:1.5px solid #ef4444;border-radius:5px;padding:2px 5px;color:#ef4444;display:inline-block;text-align:center;font-size:0.82em;line-height:1.3;font-weight:700}
.col-libelle{width:300px}
.col-regles{width:300px}
.col-valeurs{width:180px}
.col-valeurs .val-line{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:175px;line-height:1.4}
.col-valeurs .val-line .val-label{color:#94a3b8;font-weight:600;font-size:0.87em}
.col-erreurs{max-width:200px}
.col-erreurs-hidden{display:none}
/* === CEGEDIM SUB-TABLE === */
table.ceg-table{width:100%;border-collapse:collapse;margin:6px 0 0 0;font-size:0.83em}
table.ceg-table th{background:#6d28d9;color:#fff;padding:6px 10px;text-align:left;font-weight:600}
table.ceg-table td{padding:6px 10px;border-bottom:1px solid #ede9fe;background:#faf5ff}
.ceg-row-header td{background:#f3e8ff;font-style:italic;font-size:0.78em;color:#7c3aed;padding:4px 10px;border-bottom:1px dashed #ddd6fe}
/* === TOOLTIP === */
.tooltip{position:absolute;background:#1e293b;color:#e2e8f0;padding:11px 14px;border-radius:8px;font-size:0.85em;z-index:1000;display:none;max-width:560px;box-shadow:0 8px 24px rgba(0,0,0,0.28);pointer-events:none;line-height:1.5}
.tooltip strong{color:#fbbf24}
.tooltip>strong,.tooltip br+strong{display:block;margin-bottom:4px}
.tooltip ul{margin:2px 0 4px 0;padding-left:16px}
.tooltip li{margin:1px 0}
.tooltip-separator{border-top:1px solid rgba(255,255,255,0.12);margin:7px 0;padding-top:6px}
.tooltip-controls{font-size:0.81em;color:#94a3b8}
/* === PARAMÉTRAGE — LISTE CHAMPS === */
.mapping-list{list-style:none;margin:0;padding:0}
.mapping-item{padding:9px 12px;margin:3px 0;border-radius:7px;border:1px solid #e2e8f0;border-left:3px solid #667eea;display:flex;justify-content:space-between;align-items:center;background:#fff;cursor:move;transition:all 0.15s}
.mapping-item.valide{border-left-color:#10b981}
.mapping-item.article{border-left-color:#f59e0b}
.mapping-item.article.valide{border-left-color:#10b981}
.mapping-item.ignored{border-left-color:#94a3b8;background:#f1f5f9;opacity:0.6}
/* Tooltip ignoré */
#ignored-tooltip{position:fixed;z-index:9999;background:#1e293b;color:#e2e8f0;padding:12px 16px;border-radius:10px;font-size:0.82em;line-height:1.6;max-width:320px;box-shadow:0 8px 24px rgba(0,0,0,0.22);pointer-events:none;display:none;border-left:3px solid #94a3b8}
#ignored-tooltip strong{color:#fff;display:block;margin-bottom:4px;font-size:0.95em}
#ignored-tooltip em{color:#f59e0b;font-style:normal;display:block;margin-top:6px;font-size:0.9em}
.mapping-item.dragging{opacity:0.45;transform:scale(0.98)}
.mapping-item.drag-over{border-top:2px solid #667eea;margin-top:6px}
.mapping-item-info{flex:1;min-width:0}
.mapping-item-info .item-main{font-weight:600;font-size:0.88em;color:#1e293b}
.mapping-item-info .item-sub{font-size:0.77em;color:#64748b;margin-top:2px}
.mapping-item-info .item-xpath{font-size:0.73em;color:#94a3b8;font-family:'JetBrains Mono',monospace;margin-top:2px;word-break:break-all}
/* === GROUPES CATÉGORIES === */
.cat-filter-bar{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px;align-items:center}
.cat-pill{padding:3px 11px;border-radius:20px;font-size:0.74em;font-weight:600;cursor:pointer;border:1px solid #e2e8f0;background:#f8fafc;color:#64748b;transition:all 0.15s;white-space:nowrap}
.cat-pill:hover{border-color:#667eea;color:#4f46e5}
.cat-pill.active{background:#667eea;color:#fff;border-color:#4f46e5}
.cat-pill.art{border-color:#fdba74;color:#92400e;background:#fff7ed}
.cat-pill.art.active{background:#f59e0b;border-color:#d97706;color:#fff}
.cat-group{margin-bottom:8px;border-radius:10px;overflow:hidden;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.cat-group-hdr{display:flex;align-items:center;gap:8px;padding:8px 14px;cursor:pointer;user-select:none;background:#f1f5f9;transition:background 0.15s}
.cat-group-hdr:hover{background:#e8edf5}
.cat-group-hdr.art{background:#fff7ed}
.cat-group-hdr.art:hover{background:#fef3c7}
.cat-group-arrow{font-size:0.62em;display:inline-block;transition:transform 0.2s;color:#94a3b8;width:10px;text-align:center}
.cat-group-hdr.open .cat-group-arrow{transform:rotate(90deg)}
.cat-group-name{font-weight:700;font-size:0.75em;flex:1;text-transform:uppercase;letter-spacing:0.07em;color:#334155}
.cat-group-hdr.art .cat-group-name{color:#92400e}
.cat-group-count{font-size:0.71em;font-weight:700;padding:2px 9px;border-radius:10px;background:#e2e8f0;color:#475569}
.cat-group-hdr.art .cat-group-count{background:#fde68a;color:#92400e}
.cat-group-ok-ratio{font-size:0.7em;color:#94a3b8;margin-left:4px}
.cat-group-body{padding:6px 6px;background:#fafbfc}
.mapping-actions{display:flex;align-items:center;gap:5px;flex-shrink:0}
.mapping-actions button{padding:5px 10px;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:0.79em;font-family:'Outfit',Arial,sans-serif;transition:all 0.15s}
.btn-edit{background:#667eea;color:#fff}
.btn-edit:hover{background:#4f46e5}
.btn-delete{background:#ef4444;color:#fff}
.btn-delete:hover{background:#dc2626}
.valide-toggle{display:flex;align-items:center;gap:4px;font-size:0.79em;color:#10b981;font-weight:600;cursor:pointer}
.valide-toggle input{width:13px;height:13px;cursor:pointer;accent-color:#10b981}
/* === BTN GROUP === */
.btn-group{display:flex;gap:7px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
/* === MODAL BASE === */
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(15,23,42,0.58);z-index:1000;backdrop-filter:blur(3px)}
.modal-content{background:#fff;margin:4% auto;padding:22px;border-radius:14px;max-width:900px;max-height:92vh;overflow-y:auto;box-shadow:0 24px 60px rgba(0,0,0,0.22);animation:slideUp 0.22s ease;width:90%}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.modal-header h2{font-size:1.1rem;font-weight:700;color:#1e293b;flex:1}
.modal-close{font-size:1.45em;cursor:pointer;color:#94a3b8;line-height:1;transition:color 0.15s}
.modal-close:hover{color:#1e293b}
.modal .form-group{margin-bottom:13px}
.modal .form-group label{font-weight:600;margin-bottom:5px;font-size:0.78em;color:#475569;display:block;text-transform:uppercase;letter-spacing:0.05em}
.modal .form-group input,.modal .form-group select{padding:8px 11px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:0.9em;width:100%;font-family:'Outfit',Arial,sans-serif;transition:border-color 0.2s,box-shadow 0.2s;color:#1e293b}
.modal .form-group input:focus,.modal .form-group select:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.12)}
.modal .form-group textarea{padding:8px 11px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:0.87em;min-height:75px;font-family:'JetBrains Mono',monospace;width:100%;resize:vertical;color:#1e293b}
.modal .form-group small{display:block;margin-top:4px;color:#94a3b8;font-size:0.79em;line-height:1.4}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes slideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.modal-body{padding:1.25rem}
.modal-footer{padding:1rem 1.25rem;border-top:1px solid #e2e8f0;display:flex;gap:0.6rem;justify-content:flex-end}
/* === VERSION HISTORY === */
.version-item{padding:10px 13px;margin:6px 0;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;transition:background 0.15s}
.version-item:hover{background:#f0fdf4;border-color:#d1fae5}
.version-info{flex:1}
.version-timestamp{font-weight:600;color:#667eea;font-size:0.87em}
.version-details{font-size:0.79em;color:#64748b;margin-top:3px}
/* === RÈGLES MÉTIERS === */
.rule-card{background:#fff;border-radius:10px;margin-bottom:9px;overflow:hidden;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.rule-header{padding:12px 16px;display:flex;justify-content:space-between;align-items:center;background:#f8fafc}
.rule-header.enabled{background:#f0fdf4;border-left:3px solid #10b981}
.rule-header.disabled{background:#fef2f2;border-left:3px solid #ef4444;opacity:0.8}
.rule-title{flex:1}
.rule-title strong{font-size:0.92em;color:#1e293b;font-weight:600}
.rule-status{margin-left:10px;padding:2px 9px;border-radius:999px;font-size:0.71em;font-weight:700;text-transform:uppercase;letter-spacing:0.07em}
.rule-header.enabled .rule-status{background:#dcfce7;color:#15803d}
.rule-header.disabled .rule-status{background:#fee2e2;color:#b91c1c}
.rule-actions-btn{display:flex;gap:5px}
.rule-actions-btn button{padding:5px 10px;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:0.79em;font-family:'Outfit',Arial,sans-serif;background:#667eea;color:#fff;transition:all 0.15s}
.rule-actions-btn button:hover{background:#4f46e5}
.rule-actions-btn .btn-edit{background:#f59e0b}
.rule-actions-btn .btn-edit:hover{background:#d97706}
.rule-actions-btn .btn-delete{background:#ef4444}
.rule-actions-btn .btn-delete:hover{background:#dc2626}
.rule-actions-btn .btn-clone{background:#10b981}
.rule-actions-btn .btn-clone:hover{background:#059669}
.rule-body{padding:12px 16px;border-top:1px solid #f1f5f9}
.rule-description{color:#64748b;font-size:0.85em;margin-bottom:9px;font-style:italic}
.rule-logic{background:#f8fafc;padding:10px 13px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:0.83em;color:#475569}
.rule-logic div{margin:4px 0}
.condition-item,.action-item{background:#f0f4ff;padding:10px 12px;border-radius:8px;margin-bottom:7px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;border:1px solid #e0e7ff}
.condition-item select,.action-item select,.condition-item input,.action-item input{padding:6px 9px;border:1.5px solid #e2e8f0;border-radius:6px;font-size:0.85em;max-width:280px;font-family:'Outfit',Arial,sans-serif}
.condition-item .cond-field,.action-item .action-field{min-width:200px;flex:1}
.condition-item .cond-op,.action-item .action-type{min-width:150px}
.condition-item .cond-value,.action-item .action-value{min-width:120px;flex:0.5}
.condition-item .btn-remove,.action-item .btn-remove{background:#ef4444;color:#fff;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-weight:600;white-space:nowrap;font-size:0.79em;font-family:'Outfit',Arial,sans-serif}
.condition-item .btn-remove:hover,.action-item .btn-remove:hover{background:#dc2626}
/* === MAPPING MANAGEMENT === */
.mapping-header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:1.3rem 1.6rem;border-radius:12px;color:white;margin-bottom:1.2rem;box-shadow:0 8px 20px rgba(102,126,234,0.18)}
.mapping-header h2{font-size:1.35rem;font-weight:700;margin-bottom:3px}
.mapping-header p{opacity:0.82;font-size:0.88rem}
.mapping-type-select{width:100%;padding:8px 11px;border:1.5px solid #e2e8f0;border-radius:8px;font-family:'Outfit',Arial,sans-serif;font-size:0.9rem;background:#fff;transition:all 0.2s;color:#1e293b}
.mapping-type-select:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.12)}
.btn-create{background:linear-gradient(135deg,#10b981 0%,#059669 100%);color:#fff;padding:8px 15px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s;display:flex;align-items:center;gap:6px;box-shadow:0 2px 6px rgba(16,185,129,0.18)}
.btn-create:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(16,185,129,0.3)}
.mappings-list{margin-top:1.2rem}
.mapping-card{background:#fff;padding:0.85rem 1.1rem;border-radius:8px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;transition:all 0.18s;border:1px solid #e2e8f0;border-left:3px solid #667eea}
.mapping-card:hover{transform:translateX(4px);box-shadow:0 4px 12px rgba(0,0,0,0.07)}
.mapping-info{flex:1}
.mapping-name{font-weight:600;font-size:0.92rem;color:#1e293b;margin-bottom:3px}
.mapping-type{font-family:'JetBrains Mono',monospace;font-size:0.74rem;color:#64748b;background:#f1f5f9;padding:2px 6px;border-radius:4px;display:inline-block}
.modal-header.create{background:linear-gradient(135deg,#10b981 0%,#059669 100%);color:white;border-radius:10px 10px 0 0;padding:14px 18px;margin:-22px -22px 16px -22px}
.modal-header.delete{background:linear-gradient(135deg,#ef4444 0%,#dc2626 100%);color:white;border-radius:10px 10px 0 0;padding:14px 18px;margin:-22px -22px 16px -22px}
.modal-header.create h2,.modal-header.delete h2{color:#fff}
.modal-header.create .modal-close,.modal-header.delete .modal-close{color:rgba(255,255,255,0.78)}
.modal-header.create .modal-close:hover,.modal-header.delete .modal-close:hover{color:#fff}
.warning-icon{font-size:1.7rem}
/* ── Color picker mapping ── */
.mapping-color-btn{background:none;border:2px solid #e2e8f0;border-radius:6px;width:28px;height:28px;cursor:pointer;padding:0;display:inline-flex;align-items:center;justify-content:center;transition:border-color 0.2s;flex-shrink:0}
.mapping-color-btn:hover{border-color:#667eea}
.mapping-color-btn input[type=color]{opacity:0;position:absolute;width:0;height:0;pointer-events:none}
.color-swatch{width:16px;height:16px;border-radius:3px;border:1px solid rgba(0,0,0,0.15);display:inline-block;background:#667eea}
/* ── Modal auteur ── */
#authorModal .modal-content{max-width:420px;text-align:center}
#authorModal .author-icon{font-size:2.2rem;margin-bottom:8px}
#authorModal h2{margin-bottom:6px}
#authorModal p{color:#64748b;font-size:0.9em;margin-bottom:16px}
#authorInput{width:100%;padding:10px 14px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:1em;font-family:'Outfit',Arial,sans-serif;text-align:center;transition:border-color 0.2s,box-shadow 0.2s}
#authorInput:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.12)}
/* ── Modal historique ── */
#historyModal .modal-content{max-width:760px}
.audit-list{display:flex;flex-direction:column;gap:10px;max-height:520px;overflow-y:auto;margin-bottom:12px}
.audit-item{display:flex;flex-direction:column;background:#f8fafc;border-radius:10px;border:1px solid #e2e8f0}
.audit-item-header{display:flex;align-items:center;gap:10px;padding:10px 14px;font-size:0.87em}
.audit-item .audit-ts{color:#94a3b8;font-size:0.81em;white-space:nowrap;min-width:130px}
.audit-item .audit-author{font-weight:700;color:#4f46e5;min-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.audit-item .audit-action{padding:2px 7px;border-radius:4px;font-size:0.78em;font-weight:700;text-transform:uppercase;flex-shrink:0}
.audit-action.edit{background:#dbeafe;color:#1d4ed8}
.audit-action.add{background:#d1fae5;color:#065f46}
.audit-action.delete{background:#fee2e2;color:#991b1b}
.audit-item .audit-bt{font-family:'JetBrains Mono',monospace;font-weight:700;color:#1e293b;flex:1}
.audit-diff{padding:8px 14px 10px;border-top:1px solid #e2e8f0;display:flex;flex-direction:column;gap:5px}
.audit-diff-row{display:grid;grid-template-columns:110px 1fr 18px 1fr;align-items:start;gap:6px;font-size:0.82em}
.audit-diff-key{color:#64748b;font-weight:600;font-size:0.78em;text-transform:uppercase;letter-spacing:0.04em;padding-top:2px}
.audit-diff-old{color:#b91c1c;background:#fef2f2;border-radius:5px;padding:2px 7px;font-family:'JetBrains Mono',monospace;word-break:break-all;line-height:1.4}
.audit-diff-arrow{color:#94a3b8;text-align:center;font-size:0.9em;padding-top:3px}
.audit-diff-new{color:#065f46;background:#f0fdf4;border-radius:5px;padding:2px 7px;font-family:'JetBrains Mono',monospace;word-break:break-all;line-height:1.4}
.audit-diff-single{color:#475569;font-family:'JetBrains Mono',monospace;font-size:0.82em;padding:2px 7px;background:#f1f5f9;border-radius:5px;word-break:break-all}
.audit-revert-btn{background:#f1f5f9;border:1.5px solid #cbd5e1;border-radius:6px;padding:4px 10px;font-size:0.78em;cursor:pointer;font-family:'Outfit',Arial,sans-serif;font-weight:600;color:#475569;transition:all 0.15s;white-space:nowrap;margin-left:auto}
.audit-revert-btn:hover{background:#e0e7ff;border-color:#818cf8;color:#4338ca}
.audit-action.revert{background:#f3e8ff;color:#7c3aed}
.audit-item-revert{border-color:#e9d5ff!important;background:#faf5ff}
.audit-num{color:#94a3b8;font-family:'JetBrains Mono',monospace;font-size:0.75em;font-weight:700;min-width:36px;flex-shrink:0}
.audit-rollback-label{font-size:0.78em;color:#7c3aed;font-style:italic;white-space:nowrap;margin-left:auto;padding-right:6px}
.btn-history{background:linear-gradient(135deg,#0ea5e9 0%,#0284c7 100%);color:#fff;padding:8px 15px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.84em;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s}
.warning-text{background:#fef3c7;border-left:3px solid #f59e0b;padding:10px 14px;border-radius:0 7px 7px 0;margin:10px 0;color:#92400e;font-size:0.85em;line-height:1.5}
.warning-text strong{display:block;margin-bottom:3px}
.empty-state{text-align:center;padding:2.5rem 1rem;color:#64748b}
.empty-state-icon{font-size:2.8rem;margin-bottom:0.7rem;opacity:0.22}
/* === EDIT FIELD MODAL === */
.edit-field-modal{padding:0!important;max-width:680px!important;border-radius:16px!important;overflow:hidden}
.edit-field-header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:16px 22px;display:flex;justify-content:space-between;align-items:center;margin-bottom:0!important}
.edit-field-header h2{color:#fff;margin:0;font-size:1.1rem;font-weight:700}
.edit-field-header p{color:rgba(255,255,255,0.75);margin:2px 0 0;font-size:0.81rem}
.edit-field-header .modal-close{color:rgba(255,255,255,0.68);font-size:1.35rem;transition:color 0.2s;line-height:1}
.edit-field-header .modal-close:hover{color:#fff}
.edit-field-header-actions{display:flex;align-items:center;gap:10px}
.btn-clone-field{display:inline-flex;align-items:center;gap:5px;padding:5px 11px;border:1.5px solid rgba(255,255,255,0.45);border-radius:7px;background:rgba(255,255,255,0.13);color:#fff;font-size:0.78rem;font-weight:600;cursor:pointer;transition:all 0.18s;font-family:'Outfit',Arial,sans-serif;white-space:nowrap}
.btn-clone-field:hover{background:rgba(255,255,255,0.25);border-color:rgba(255,255,255,0.7)}
.edit-field-hicon{width:35px;height:35px;min-width:35px;background:rgba(255,255,255,0.18);border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:1.05rem;margin-right:11px}
.edit-field-body{padding:16px;display:flex;flex-direction:column;gap:11px;background:#f8fafc;max-height:70vh;overflow-y:auto}
.edit-section{background:#fff;border-radius:10px;padding:12px 15px;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.edit-section-title{font-size:0.66rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#94a3b8;margin-bottom:10px;padding-bottom:7px;border-bottom:1px solid #f1f5f9}
.edit-row-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.edit-row-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.edit-fg{display:flex;flex-direction:column;gap:4px;margin-bottom:9px}
.edit-fg:last-child{margin-bottom:0}
.edit-row-2 .edit-fg,.edit-row-3 .edit-fg{margin-bottom:0}
.edit-lbl{font-size:0.75rem;font-weight:600;color:#475569;letter-spacing:0.03em;display:flex;align-items:center;gap:5px}
.edit-opt{font-weight:400;color:#94a3b8;font-size:0.71rem}
.edit-inp{padding:7px 10px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:0.86rem;color:#1e293b;background:#fff;transition:border-color 0.2s,box-shadow 0.2s;width:100%;box-sizing:border-box;font-family:inherit}
.edit-inp:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.12)}
.edit-inp.mono{font-family:'JetBrains Mono',monospace;font-size:0.78rem}
.edit-inp.textarea-rdg{min-height:62px;resize:vertical}
.edit-hint{font-size:0.74rem;color:#94a3b8;line-height:1.4;margin-top:1px}
.edit-field-footer{padding:12px 16px;background:#fff;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:8px}
.edit-btn-cancel{padding:7px 15px;border:1.5px solid #e2e8f0;border-radius:7px;background:#fff;color:#64748b;font-weight:600;cursor:pointer;font-size:0.85rem;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s}
.edit-btn-cancel:hover{border-color:#cbd5e1;background:#f8fafc}
.edit-btn-save{padding:7px 19px;border:none;border-radius:7px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;font-weight:600;cursor:pointer;font-size:0.85rem;font-family:'Outfit',Arial,sans-serif;transition:all 0.18s;box-shadow:0 2px 7px rgba(102,126,234,0.25)}
.edit-btn-save:hover{transform:translateY(-1px);box-shadow:0 4px 13px rgba(102,126,234,0.35)}
/* === EASTER EGG KONAMI === */
.konami-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:99999;align-items:center;justify-content:center;flex-direction:column}
.konami-overlay.visible{display:flex}
.konami-box{position:relative;animation:konami-pop 0.4s cubic-bezier(.34,1.56,.64,1)}
.konami-box img{max-width:70vw;max-height:70vh;border-radius:20px;box-shadow:0 0 80px rgba(255,215,0,0.6),0 0 20px rgba(0,0,0,0.8)}
.konami-stars{position:absolute;inset:0;pointer-events:none}
.konami-close{margin-top:20px;color:#fff;font-size:0.9em;opacity:0.65;cursor:pointer}
@keyframes konami-pop{0%{transform:scale(0) rotate(-10deg);opacity:0}100%{transform:scale(1) rotate(0deg);opacity:1}}
/* === BATCH MODE === */
.batch-pair-row{display:grid;grid-template-columns:1fr 1fr 110px 36px;gap:10px;align-items:end;margin-bottom:10px}
.batch-file-label{font-weight:600;font-size:0.78rem;color:#475569;text-transform:uppercase;letter-spacing:0.05em;display:block;margin-bottom:5px}
.batch-file-drop{border:1.5px dashed #c7d2fe;border-radius:8px;padding:8px 12px;color:#94a3b8;font-size:0.85em;display:flex;align-items:center;gap:8px;background:#f8fafc;cursor:pointer;transition:all 0.15s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.batch-file-drop:hover{border-color:#818cf8;background:#eef2ff}
.batch-file-drop.filled{border-color:#667eea;color:#4f46e5;background:#eef2ff}
.batch-file-drop.missing{border-color:#fca5a5;color:#ef4444;background:#fff5f5}
.batch-pair-status{font-size:0.8em;font-weight:700;padding-bottom:10px;white-space:nowrap;text-align:center}
.batch-pair-status.ok{color:#10b981}
.batch-pair-status.err{color:#ef4444}
.btn-batch-remove{background:#fff;color:#94a3b8;border:1.5px solid #e2e8f0;border-radius:8px;padding:8px 10px;cursor:pointer;font-size:0.85em;transition:all 0.15s}
.btn-batch-remove:hover{background:#fff5f5;border-color:#fca5a5;color:#ef4444}
.batch-wrap{background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.04);margin-bottom:14px}
.batch-thead{display:grid;grid-template-columns:220px 70px 70px 70px 140px 1fr 110px;background:#f1f5f9;border-bottom:2px solid #e2e8f0;padding:8px 14px;gap:0}
.batch-thead span{font-size:0.73em;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:0.06em}
.batch-thead .bcenter{text-align:center}
.batch-inv-row{border-bottom:1px solid #f1f5f9}
.batch-inv-row:last-child{border-bottom:none}
.batch-inv-main{display:grid;grid-template-columns:220px 70px 70px 70px 140px 1fr 110px;align-items:center;padding:11px 14px;gap:0;transition:background 0.15s}
.batch-inv-main:hover{background:#f8fafc}
.batch-inv-main.has-err{border-left:3px solid #ef4444}
.batch-inv-main.all-ok{border-left:3px solid #10b981}
.batch-inv-main.inv-error{border-left:3px solid #f59e0b}
.batch-inv-name{font-weight:600;font-size:0.88em;color:#1e293b}
.batch-inv-name .bsub{display:block;font-weight:400;font-size:0.76em;color:#94a3b8;margin-top:2px}
.batch-sc{text-align:center;font-size:0.88em;font-weight:700}
.batch-sc.ok{color:#10b981}
.batch-sc.err{color:#ef4444}
.batch-sc.amb{color:#d97706}
.batch-pct-wrap{display:flex;align-items:center;gap:8px;padding-right:10px}
.batch-pct-track{flex:1;height:7px;background:#e2e8f0;border-radius:999px;overflow:hidden}
.batch-pct-fill{height:100%;border-radius:999px}
.batch-pct-fill.good{background:linear-gradient(90deg,#10b981,#34d399)}
.batch-pct-fill.mid{background:linear-gradient(90deg,#f59e0b,#fbbf24)}
.batch-pct-fill.bad{background:linear-gradient(90deg,#ef4444,#f87171)}
.batch-pct-lbl{font-size:0.8em;font-weight:700;min-width:38px;text-align:right}
.batch-pct-lbl.good{color:#10b981}
.batch-pct-lbl.mid{color:#d97706}
.batch-pct-lbl.bad{color:#ef4444}
.batch-etag{display:inline-block;background:#fff5f5;color:#ef4444;border:1px solid #fca5a5;font-size:0.71em;padding:2px 7px;border-radius:4px;font-weight:700;margin:1px 2px 1px 0}
.batch-etag.amb{background:#fffbeb;color:#d97706;border-color:#fde68a}
.batch-etag-more{font-size:0.75em;color:#94a3b8;cursor:pointer;padding:2px 4px;border-radius:4px}
.batch-etag-more:hover{color:#667eea}
.btn-batch-detail{background:#fff;color:#667eea;border:1.5px solid #c7d2fe;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:0.82em;font-weight:700;font-family:'Outfit',Arial,sans-serif;display:inline-flex;align-items:center;gap:5px;transition:all 0.15s;white-space:nowrap}
.btn-batch-detail:hover{background:#eef2ff;border-color:#818cf8}
.btn-batch-detail.open{background:#eef2ff;border-color:#667eea;color:#4f46e5}
.btn-batch-detail .b-arrow{display:inline-block;transition:transform 0.2s;font-size:0.8em}
.btn-batch-detail.open .b-arrow{transform:rotate(90deg)}
.batch-detail-zone{display:none;background:#f8fafc;border-top:1px solid #e2e8f0;padding:18px 22px}
.batch-detail-zone.open{display:block}
.batch-loading{display:none;text-align:center;padding:30px}
.batch-empty{color:#94a3b8;font-size:0.85em;text-align:center;padding:20px;border:1.5px dashed #e2e8f0;border-radius:8px}
/* Dropzone */
.batch-dropzone{border:2px dashed #c7d2fe;border-radius:12px;padding:36px 24px;text-align:center;cursor:pointer;transition:all 0.2s;background:#fafbff;position:relative}
.batch-dropzone:hover,.batch-dropzone.drag-over{border-color:#667eea;background:#eef2ff}
.batch-dropzone .dz-icon{font-size:2.4em;margin-bottom:10px;display:block}
.batch-dropzone .dz-title{font-weight:700;font-size:1em;color:#1e293b;margin-bottom:4px}
.batch-dropzone .dz-hint{font-size:0.82em;color:#94a3b8;margin-bottom:14px}
/* Table fichiers */
.batch-files-table{width:100%;border-collapse:collapse;font-size:0.85em;margin-top:14px}
.batch-files-table th{background:#f1f5f9;color:#475569;padding:7px 12px;text-align:left;font-weight:700;font-size:0.75em;text-transform:uppercase;letter-spacing:0.05em;border-bottom:2px solid #e2e8f0}
.batch-files-table td{padding:8px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
.batch-files-table tr:last-child td{border-bottom:none}
.batch-files-table tr:hover td{background:#f8fafc}
.batch-file-num{font-weight:700;font-size:0.95em;color:#1e293b}
.batch-file-pending{color:#94a3b8;font-style:italic;font-size:0.82em}
.batch-file-chip{display:inline-flex;align-items:center;gap:5px;background:#f1f5f9;border-radius:6px;padding:3px 9px;font-size:0.8em;color:#475569;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.batch-file-chip.pdf{background:#eef2ff;color:#4f46e5}
.batch-file-chip.rdi{background:#f0fdf4;color:#15803d}
.batch-file-chip.missing{background:#fff5f5;color:#ef4444;border:1px dashed #fca5a5}
.batch-status-chip{font-size:0.75em;font-weight:700;padding:3px 9px;border-radius:20px;white-space:nowrap}
.batch-status-chip.ok{background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0}
.batch-status-chip.warn{background:#fffbeb;color:#b45309;border:1px solid #fde68a}
.batch-status-chip.pending{background:#f8fafc;color:#94a3b8;border:1px solid #e2e8f0}
/* Résultats — colonne facture */
.batch-inv-num{font-weight:800;font-size:1em;color:#1e293b;letter-spacing:0.01em}
.batch-inv-filename{font-size:0.74em;color:#94a3b8;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;position:relative}
.batch-inv-filename[data-fullname]:hover::after{content:attr(data-fullname);position:absolute;left:0;top:100%;margin-top:4px;background:#1e293b;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:1em;white-space:nowrap;z-index:1000;box-shadow:0 4px 12px rgba(0,0,0,0.18);pointer-events:none}
/* Schematron EN16931 panel */
.schematron-panel{margin-bottom:14px;border-radius:10px;overflow:hidden;border:1px solid #e2e8f0}
.schematron-header{padding:12px 18px;display:flex;justify-content:space-between;align-items:center;font-weight:700;color:#fff;cursor:pointer;font-size:0.92em}
.schematron-header.ok{background:linear-gradient(135deg,#065f46 0%,#10b981 100%)}
.schematron-header.err{background:linear-gradient(135deg,#7f1d1d 0%,#dc2626 100%)}
.schematron-header.warn{background:linear-gradient(135deg,#78350f 0%,#f59e0b 100%)}
.schematron-header .badges{display:flex;gap:8px;font-size:0.82em;font-weight:600}
.schematron-header .badge{background:rgba(255,255,255,0.2);padding:3px 10px;border-radius:14px}
.schematron-body{background:#fff;padding:0;max-height:0;overflow:hidden;transition:max-height 0.3s}
.schematron-body.open{max-height:30000px;padding:12px 18px}
.schematron-body table{width:100%;border-collapse:collapse;font-size:0.85em;margin-top:6px}
.schematron-body th{background:#f1f5f9;color:#475569;padding:6px 10px;text-align:left;font-size:0.78em;text-transform:uppercase;letter-spacing:0.04em;border-bottom:2px solid #e2e8f0}
.schematron-body td{padding:6px 10px;border-bottom:1px solid #f1f5f9;vertical-align:top;color:#1e293b}
.schematron-body td.rule{font-family:monospace;font-weight:700;color:#dc2626;white-space:nowrap}
.schematron-body td.flag.fatal{color:#dc2626;font-weight:700}
.schematron-body td.flag.warning{color:#b45309;font-weight:700}
.schematron-body td.location{text-align:center;white-space:nowrap}
.schematron-body button.copy-xpath{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:6px;padding:4px 10px;font-size:0.78em;font-weight:600;cursor:pointer;transition:all 0.15s}
.schematron-body button.copy-xpath:hover{background:#dbeafe;border-color:#60a5fa}
.schematron-body button.copy-xpath.copied{background:#dcfce7;color:#15803d;border-color:#86efac}
.schematron-body .empty{color:#15803d;padding:8px 0;font-weight:600}
.schematron-body .intro{color:#64748b;font-size:0.82em;margin-bottom:6px}
.schematron-body .bts span{display:inline-block;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:6px;padding:1px 6px;margin:1px 3px 1px 0;font-size:0.78em;font-weight:600}</style>
</head>
<body>

<div class="konami-overlay" id="konamiOverlay">
  <div class="konami-box">
    <img src="__URL_PREFIX__/img/BigPicture.png" alt="Easter egg !">
  </div>
  <div class="konami-close" onclick="document.getElementById('konamiOverlay').classList.remove('visible')">
    ↑↑↓↓←→←→ B A — Cliquez pour fermer
  </div>
</div>
<div class="gaulois-overlay" id="gauloisOverlay">
<div class="gaulois-card">
<img id="gauloisImg" src="__URL_PREFIX__/img/0-25.jpg" alt="Gaulois">
</div>
</div>
<div class="sidebar">
</div>
<div class="main-wrap">
<div class="container">
<div class="header">
<div class="header-left">
<img class="header-logo" src="__URL_PREFIX__/img/AppLogo_V2.png" alt="Logo"><div class="header-text"><h1><span class="title-facturix">Facturix</span><span class="title-subtitle"> &nbsp;&nbsp;&nbsp;&nbsp;   La potion magique pour des factures certifiées !</span></h1>
<div class="version">v15 — Made with love by Julien ❤️</div></div>
</div>
<div class="header-banner" onclick="document.getElementById('konamiOverlay').classList.add('visible')">
<img src="__URL_PREFIX__/img/TopLogo.png" alt="On va vérifier tes factures, par Bélénos !">
</div>
</div>
<div class="tabs">
<button class="tab active" id="tabControle">Contrôle</button>
<button class="tab" id="tabBatch">📦 Batch</button>
<button class="tab" id="tabStats">📊 Statistiques</button>
<button class="tab" id="tabParam">Paramétrage</button>
<button class="tab" id="tabRules">Règles Métiers</button>
<button class="tab" id="tabAide">Aide</button>
</div>

<!-- ONGLET CONTROLE -->
<div id="contentControle" class="tab-content active">
<div class="section">
<h2>Configuration</h2>
<div class="form-row">
<div class="form-group">
<label>Type de Factures :</label>
<select id="typeFormulaire">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupe</option>
<option value="ventesdiverses">Ventes Diverses</option>
</select>
</div>
<div class="form-group">
<label>Type de Contrôle :</label>
<select id="typeControle">
<option value="xml">RDI vs XML - Comparaison sortie SAP / Exstream</option>
<option value="rdi">RDI - Sortie SAP</option>
<option value="xmlonly">XML - Vérif facture uniquement</option>
<option value="cii">CII - GCP (XML direct)</option>
</select>
</div>
</div>
<div class="help-box" id="helpControle"></div>
</div>
<div class="section">
<h3>Fichiers</h3>
<div class="form-row">
<div class="form-group" id="groupePdf">
<label>PDF ou XML :</label>
<input type="file" id="pdfFile" accept=".pdf,.xml">
<button class="btn-secondary" id="btnDownloadXml" style="display:none;margin-top:6px;font-size:12px;padding:4px 10px"><span>📄</span> Télécharger XML</button>
<button class="btn-secondary" id="btnRemoveSignature" style="display:none;margin-top:6px;margin-left:6px;font-size:12px;padding:4px 10px"><span>✂️</span> Supprimer signature</button>
</div>
<div class="form-group" id="groupeCii" style="display:none">
<label>Fichier XML CII :</label>
<input type="file" id="ciiFile" accept=".xml">
</div>
<div class="form-group" id="groupeRdi">
<label>Fichier RDI :</label>
<input type="file" id="rdiFile" accept=".txt,.rdi">
</div>
</div>
<button class="btn" id="btnControle">LANCER LE CONTRÔLE</button>
</div>
<div class="loading" id="loading"><div class="spinner"></div><p>Controle en cours...</p></div>
<div class="results" id="results">
<div class="progress-section">
<div class="progress-label-row">
<h3>Taux de conformité</h3>
<span class="progress-pct" id="progressPct">0%</span>
</div>

<div class="progress-track">
<div class="progress-fill pct-0" id="progressFill" style="width:0%"></div>
</div>
</div>
<div class="section">
<div class="stats">
<div class="stat-card"><div>Total</div><div class="stat-value" id="statTotal">0</div></div>
<div class="stat-card ok"><div>OK</div><div class="stat-value" id="statOk">0</div></div>
<div class="stat-card erreur"><div>Erreurs</div><div class="stat-value" id="statErreur">0</div></div>
<div class="stat-card ignore"><div>Ignorés</div><div class="stat-value" id="statIgnore">0</div></div>
<div class="stat-card ambigu"><div>Ambigus</div><div class="stat-value" id="statAmbigu">0</div></div>
<div class="stat-card" style="background:#1a3a5a;color:#fff"><div>📦 Articles</div><div class="stat-value" id="statArticles" style="color:#fff">—</div></div>
</div>
</div>
<div class="section">
<div class="search-box">
<div class="search-box-row">
<label for="searchBT">🔍 Rechercher un BT :</label>
<input type="text" id="searchBT" placeholder="Tapez un n° de BT (ex: 48)">
<button class="btn-clear" id="btnClearSearch" style="display:none">✕ Effacer</button>
<label for="searchContent" style="margin-left:10px;font-weight:600;font-size:0.84em;color:#475569;white-space:nowrap">🔎 Contenu RDI / XML :</label>
<input type="text" id="searchContent" placeholder="Ex: FR12345...">
<button class="btn-clear" id="btnClearContent" style="display:none">✕ Effacer</button>
</div>
<div class="search-box-row">
<label style="display:flex;align-items:center;gap:6px;font-weight:normal">
<input type="checkbox" id="filterErrors" style="width:18px;height:18px">
<span>Uniquement les erreurs</span>
</label>
<label style="display:flex;align-items:center;gap:6px;font-weight:normal">
<input type="checkbox" id="filterAmbigus" style="width:18px;height:18px">
<span>Uniquement les ambigus</span>
</label>
<label style="display:flex;align-items:center;gap:6px;font-weight:normal">
<input type="checkbox" id="showCegedim" style="width:18px;height:18px">
<span>Afficher contrôles CEGEDIM</span>
</label>
<div style="margin-left:auto;display:flex;gap:8px">
<button class="btn-clear" id="btnExpandAll" style="display:inline-block;font-size:12px;padding:4px 10px">▼ Tout déplier</button>
<button class="btn-clear" id="btnCollapseAll" style="display:inline-block;font-size:12px;padding:4px 10px">▲ Tout replier</button>
</div>
</div>
</div>
</div>
<div class="section"><div id="categoriesContainer"></div></div>
</div>
</div>

<!-- ONGLET PARAMETRAGE - ENHANCED -->
<div id="contentParam" class="tab-content">
<div class="section">
<h2 style="margin:0 0 14px 0">⚙️ Paramètres globaux</h2>
<div style="display:flex;align-items:center;gap:14px;padding:14px 18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
<label style="display:flex;align-items:center;gap:10px;cursor:pointer;margin:0">
<input type="checkbox" id="settingSchematronEnabled" checked style="width:18px;height:18px;cursor:pointer">
<div>
<div style="font-weight:700;color:#1e293b">📜 Validation schematron officielle EN16931 (CII)</div>
<div style="font-size:0.85em;color:#64748b;margin-top:2px">Décoche pour désactiver complètement la validation schematron lors des contrôles. Le panneau de synthèse et les règles « 📜 BR-XX » disparaissent du résultat.</div>
</div>
</label>
<span id="settingSaveIndicator" style="margin-left:auto;font-size:0.85em;color:#15803d;opacity:0;transition:opacity 0.3s">✓ Enregistré</span>
</div>
</div>
<div class="section">
<div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">
<h2 style="margin:0">Gestion des Mappings</h2>
<button class="btn-create" id="btnCreateMapping"><span>➕</span> Créer un mapping</button>
</div>
<div class="form-group" style="margin-bottom:20px">
<label>Mapping actif :</label>
<div style="display:flex;gap:8px;align-items:center">
<select id="typeFormulaireParam" class="mapping-type-select">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupée</option>
<option value="flux">Flux Générique</option>
</select>
<button id="btnDeleteCurrentMapping" class="btn-delete" style="display:none" onclick="deleteCurrentMapping()"><span>🗑️</span> Supprimer</button>
</div>
</div>
<div class="btn-group">
<button class="btn-add" id="btnAdd"><span>➕</span> Ajouter un champ</button>
<button class="btn-history" id="btnHistory"><span>📋</span> Historique</button><label class="mapping-color-btn" id="mappingColorBtn" title="Couleur du mapping">
<span class="color-swatch" id="mappingColorSwatch"></span>
<input type="color" id="mappingColorPicker" value="#667eea">
</label>
</div>
<div class="search-box">
<label for="searchBTParam">🔍 Rechercher un BT :</label>
<input type="text" id="searchBTParam" placeholder="Tapez un num. de BT (ex: 48)">
<button class="btn-clear" id="btnClearSearchParam" style="display:none">✕ Effacer</button>
</div>
</div>

<div class="section">
<div id="mappingList"></div>
</div>
</div>

<!-- ONGLET RÈGLES MÉTIERS -->
<div id="contentRules" class="tab-content">
<div class="section">
<h2>Règles Métiers Configurables</h2>
<p>Gérez les règles de validation conditionnelles qui s'appliquent aux champs de la facture.</p>
<div style="background:#e8f0fb;border-left:4px solid #366092;border-radius:6px;padding:14px 18px;margin:16px 0;font-size:0.93em;line-height:1.7">
  <strong style="color:#366092">ℹ️ Ordre d'application des contrôles</strong><br>
  Les contrôles s'appliquent en deux passes successives :<br>
  <ol style="margin:8px 0 8px 20px;padding:0">
    <li><strong>Mapping</strong> — chaque champ est contrôlé selon sa définition (obligatoire / type / XPath). C'est la base.</li>
    <li><strong>Règles Métiers</strong> — les règles ci-dessous s'appliquent <em>après</em> le mapping et <strong>prennent le dessus</strong> sur les contrôles par défaut.</li>
  </ol>
  Exemples de surcharge possibles : rendre un champ <em>obligatoire</em> ou <em>non obligatoire</em> selon la valeur d'un autre champ, imposer une valeur fixe, exiger un signe négatif.<br>
</div>
<div class="form-row" style="margin-bottom:15px">
<div class="form-group">
<label>Filtrer par type de factures :</label>
<select id="filterFormType">
<option value="all">Toutes les factures</option>
</select>
</div>
</div>
<div class="btn-group">
<button class="btn-secondary" id="btnReloadRules">🔄 Actualiser</button>
<button class="btn-add" id="btnAddRule">+ Nouvelle règle</button>
</div>
</div>
<div class="section">
<div id="rulesList"></div>
</div>
</div>

<!-- ONGLET AIDE -->
<div id="contentAide" class="tab-content">
<div class="section">
<div style="max-width:680px;margin:0 auto">

  <p style="color:#64748b;font-size:0.95em;margin-bottom:24px;line-height:1.6">
    Facturix contrôle les factures électroniques <strong>Factur-X / CII</strong> en comparant champ par champ la sortie SAP (RDI) et le XML embarqué dans le PDF.
  </p>

  <!-- Contrôle -->
  <div style="background:#f0fdf4;border-left:4px solid #10b981;border-radius:0 10px 10px 0;padding:16px 20px;margin-bottom:14px">
    <div style="font-weight:700;font-size:1em;color:#065f46;margin-bottom:8px">🔍 Contrôle</div>
    <ul style="margin:0;padding-left:18px;color:#374151;font-size:0.9em;line-height:1.8">
      <li><strong>RDI seul</strong> — vérifie la présence des champs obligatoires et les règles de gestion</li>
      <li><strong>XML / PDF seul</strong> — contrôle le XML Factur-X embarqué dans le PDF</li>
      <li><strong>CII direct</strong> — analyse un fichier XML CII brut sans PDF</li>
      <li><strong>RDI vs XML</strong> — comparaison complète sortie SAP ↔ sortie Exstream</li>
    </ul>
  </div>

  <!-- Paramétrage -->
  <div style="background:#eff6ff;border-left:4px solid #3b82f6;border-radius:0 10px 10px 0;padding:16px 20px;margin-bottom:14px">
    <div style="font-weight:700;font-size:1em;color:#1e40af;margin-bottom:8px">⚙️ Paramétrage des mappings</div>
    <ul style="margin:0;padding-left:18px;color:#374151;font-size:0.9em;line-height:1.8">
      <li>Ajouter, modifier, supprimer ou cloner des champs BT</li>
      <li>Configurer le tag RDI, le XPath XML, le caractère obligatoire, la règle de gestion</li>
      <li>Plusieurs mappings disponibles : CART Simple, CART Groupée, Ventes Diverses, custom</li>
      <li>Champ "Ignorer les erreurs" pour masquer un BT défaillant connu</li>
    </ul>
  </div>

  <!-- Historique -->
  <div style="background:#faf5ff;border-left:4px solid #8b5cf6;border-radius:0 10px 10px 0;padding:16px 20px;margin-bottom:14px">
    <div style="font-weight:700;font-size:1em;color:#6b21a8;margin-bottom:8px">📋 Historique des modifications</div>
    <ul style="margin:0;padding-left:18px;color:#374151;font-size:0.9em;line-height:1.8">
      <li>Chaque modification est horodatée et attribuée à un auteur</li>
      <li>Affichage des valeurs avant / après pour chaque champ modifié</li>
      <li>Bouton <strong>↩ Revenir</strong> pour annuler une modification individuelle</li>
      <li>Le rollback est lui-même tracé dans l'historique (<em>Rollback de la modification #X</em>)</li>
    </ul>
  </div>

  <!-- Règles métiers -->
  <div style="background:#fff7ed;border-left:4px solid #f59e0b;border-radius:0 10px 10px 0;padding:16px 20px;margin-bottom:14px">
    <div style="font-weight:700;font-size:1em;color:#92400e;margin-bottom:8px">📐 Règles métiers</div>
    <ul style="margin:0;padding-left:18px;color:#374151;font-size:0.9em;line-height:1.8">
      <li>Toutes les règles sont configurables via l'écran Règles métiers (conditions + actions)</li>
      <li>9 règles par défaut : B2G Chorus, avoirs, BT-8=5, client étranger, factures négatives, BT-21-SUR/ISU, etc.</li>
    </ul>
  </div>

</div>
</div>
</div>

<!-- ONGLET BATCH -->
<div id="contentBatch" class="tab-content">
<div class="section">
<h2>Configuration</h2>
<div class="form-row">
<div class="form-group">
<label>Type de Factures :</label>
<select id="batchTypeFormulaire">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupée</option>
<option value="ventesdiverses">Ventes Diverses</option>
</select>
</div>
<div class="form-group">
<label>Type de Contrôle :</label>
<select id="batchTypeControle" onchange="batchUpdatePairLabels()">
<option value="xml">RDI vs XML — comparaison SAP / Exstream</option>
<option value="rdi">RDI seul — sortie SAP</option>
<option value="xmlonly">XML / PDF seul</option>
<option value="cii">CII direct — XML brut</option>
</select>
</div>
</div>
</div>
<div class="section">
<h2>Factures à contrôler</h2>
<input type="file" id="batchFileInput" multiple accept=".pdf,.xml,.txt,.rdi" style="display:none" onchange="batchHandleFileInput(this.files)">
<div id="batchDropZone" class="batch-dropzone" onclick="document.getElementById('batchFileInput').click()">
  <span class="dz-icon">📂</span>
  <div class="dz-title">Glissez vos fichiers ici ou cliquez pour parcourir</div>
  <div class="dz-hint" id="batchDzHint">PDF + RDI — les numéros de facture sont détectés automatiquement</div>
  <button class="btn-secondary" style="pointer-events:none;margin-top:2px">Parcourir les fichiers</button>
</div>
<div id="batchFilesSection" style="display:none;margin-top:12px">
  <table class="batch-files-table" id="batchFilesTable">
    <thead id="batchFilesHead"></thead>
    <tbody id="batchFilesBody"></tbody>
  </table>
  <div style="display:flex;align-items:center;gap:10px;margin-top:12px">
    <button class="btn-secondary" onclick="batchReset()" style="font-size:0.82em;padding:6px 13px">🗑 Tout effacer</button>
    <button class="btn" id="btnLaunchBatch" onclick="batchLaunch()" style="width:auto;padding:10px 26px;margin-left:auto;opacity:0.5;pointer-events:none">▶ Lancer le contrôle (0 facture)</button>
  </div>
</div>
</div>
<div class="batch-loading" id="batchLoading">
<div class="spinner"></div>
<p id="batchLoadingMsg">Contrôle en cours…</p>
</div>
<div id="batchResults" style="display:none">
<div style="font-size:1em;font-weight:700;color:#1e293b;margin-bottom:12px;display:flex;align-items:baseline;gap:10px">
Résultats du lot <span style="font-size:0.78em;color:#94a3b8;font-weight:400" id="batchResultsSub"></span>
</div>
<div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap" id="batchStatsGlobal"></div>
<div id="batchSkippedWarning" style="display:none;background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:0.84em;color:#92400e;line-height:1.5"></div>
<div class="batch-wrap">
<div class="batch-thead">
<span>Facture</span>
<span class="bcenter">✅ OK</span>
<span class="bcenter">❌ ERR</span>
<span class="bcenter">⚠️ AMB</span>
<span>Score</span>
<span>Erreurs détectées</span>
<span></span>
</div>
<div id="batchTableBody"></div>
</div>
<div style="display:flex;gap:10px;justify-content:flex-end;margin-bottom:20px">
<button class="btn-secondary" id="btnBatchCsvAll" style="padding:8px 16px">⬇ Exporter tout en CSV</button>
<button class="btn-secondary" onclick="batchReset()" style="padding:8px 16px">🔁 Nouveau lot</button>
</div>
</div>
</div>

<!-- ONGLET STATISTIQUES -->
<div id="contentStats" class="tab-content">
<div class="section">
<h2>📊 Statistiques de contrôle</h2>
<p style="color:#64748b;font-size:0.92em;margin-top:6px">Vue d'ensemble des factures contrôlées (unitaire et batch). Toutes les analyses lancées depuis l'onglet Contrôle ou Batch sont enregistrées automatiquement.</p>
</div>

<div class="section">
<h3>Filtres</h3>
<div class="form-row" style="grid-template-columns:1fr 1fr 1fr 1fr;gap:12px">
<div class="form-group">
<label>Type de factures</label>
<select id="statsFilterType">
<option value="all">Tous les types</option>
</select>
</div>
<div class="form-group">
<label>Mode</label>
<select id="statsFilterMode">
<option value="">Unitaire + Batch</option>
<option value="unitaire">Unitaire</option>
<option value="batch">Batch</option>
</select>
</div>
<div class="form-group">
<label>Du</label>
<input type="date" id="statsFilterStart">
</div>
<div class="form-group">
<label>Au</label>
<input type="date" id="statsFilterEnd">
</div>
</div>
<div class="btn-group" style="margin:0">
<button class="btn-secondary" id="btnStatsApply">🔍 Appliquer</button>
<button class="btn-secondary" id="btnStatsReset">↻ Réinitialiser</button>
<span id="statsRangeHint" style="margin-left:auto;color:#94a3b8;font-size:0.82em;align-self:center"></span>
</div>
</div>

<!-- Cartes synthétiques -->
<div class="section">
<h3>Vue d'ensemble</h3>
<div class="stats" id="statsCards">
<div class="stat-card"><div>Factures contrôlées</div><div class="stat-value" id="kpiTotal">—</div></div>
<div class="stat-card ok"><div>Taux moyen</div><div class="stat-value" id="kpiAvgPct">—</div></div>
<div class="stat-card erreur"><div>Échecs / erreurs techniques</div><div class="stat-value" id="kpiErrors">—</div></div>
<div class="stat-card" style="background:#1a3a5a;color:#fff"><div>Unitaire</div><div class="stat-value" id="kpiUnitaire" style="color:#fff">—</div></div>
<div class="stat-card" style="background:#365e3b;color:#fff"><div>Batch</div><div class="stat-value" id="kpiBatch" style="color:#fff">—</div></div>
<div class="stat-card" style="background:#5a3a1a;color:#fff"><div>Types analysés</div><div class="stat-value" id="kpiTypes" style="color:#fff">—</div></div>
</div>
</div>

<!-- Ventilation par type -->
<div class="section">
<h3>Ventilation par type de factures</h3>
<div id="statsByType" style="overflow-x:auto"></div>
</div>

<!-- Trend -->
<div class="section">
<h3>Évolution du taux de conformité</h3>
<p style="color:#64748b;font-size:0.85em;margin:-6px 0 10px 0">Une courbe par type de factures. Survolez les points pour voir le détail.</p>
<div id="statsChartWrap" style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px"></div>
<div id="statsChartLegend" style="margin-top:10px;display:flex;flex-wrap:wrap;gap:14px;font-size:0.84em;color:#475569"></div>
</div>

<!-- Top KO -->
<div class="section">
<h3>Champs les plus souvent KO</h3>
<p style="color:#64748b;font-size:0.85em;margin:-6px 0 10px 0">Classement par occurrences (ERREUR + AMBIGU). Filtré selon les filtres ci-dessus.</p>
<div id="statsTopKo" style="overflow-x:auto"></div>
</div>

<!-- Historique -->
<div class="section">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
<h3 style="margin:0">Dernières factures contrôlées</h3>
<button class="btn-secondary" id="btnStatsExportCsv" style="padding:6px 12px">⬇ Export CSV</button>
</div>
<div id="statsHistory" style="overflow-x:auto"></div>
</div>
</div>

<div id="ignored-tooltip"><strong>⚠️ Ignorer les erreurs est activé</strong>Ce champ est configuré pour masquer ses erreurs — utile quand un bug connu sur ce BT polluerait systématiquement les résultats.<em>↩ Pensez à désactiver cette option une fois l'anomalie corrigée.</em></div>

<!-- MODAL EDITION -->
<div id="editModal" class="modal">
<div class="modal-content edit-field-modal">
<div class="modal-header edit-field-header">
<div style="display:flex;align-items:center">
<div class="edit-field-hicon">⚙️</div>
<div><h2 id="modalTitle">Mapping</h2><p id="modalSubtitle">Mise à jour du champ BT</p></div>
</div>
<div class="edit-field-header-actions">
<button class="btn-clone-field" id="btnCloneField" style="display:none" title="Copier ce champ vers un autre mapping">⎘ Cloner vers…</button>
<span class="modal-close" id="modalClose">&times;</span>
</div>
</div>
<div class="edit-field-body">
<!-- Identification -->
<div class="edit-section">
<div class="edit-section-title">Identification</div>
<div class="edit-row-2">
<div class="edit-fg">
<label class="edit-lbl">Balise BT</label>
<input type="text" id="editBalise" class="edit-inp" placeholder="ex : BT-24">
</div>
<div class="edit-fg">
<label class="edit-lbl">Catégorie</label>
<select id="editCategorie" class="edit-inp">
<option value="BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE">Informations générales</option>
<option value="BG-TOTAUX|TOTAUX DE LA FACTURE">Totaux de la facture</option>
<option value="BG-TVA|DÉTAIL DE LA TVA">Détail de la TVA</option>
<option value="BG-LIGNES|LIGNES DE FACTURE">Lignes de facture</option>
<option value="BG-VENDEUR|INFORMATIONS VENDEUR">Informations vendeur (RTE)</option>
<option value="BG-ACHETEUR|INFORMATIONS ACHETEUR">Informations client</option>
</select>
</div>
</div>
<div class="edit-fg">
<label class="edit-lbl">Libellé</label>
<input type="text" id="editLibelle" class="edit-inp" placeholder="Description lisible du champ">
</div>
</div>
<!-- Mapping technique -->
<div class="edit-section">
<div class="edit-section-title">Mapping technique</div>
<div class="edit-row-2">
<div class="edit-fg">
<label class="edit-lbl">Champ RDI</label>
<input type="text" id="editRdi" class="edit-inp mono" placeholder="ex : GS_FECT_EINV-BG1-BT21-BAR">
</div>
<div class="edit-fg">
<label class="edit-lbl">Type d'enregistrement <span class="edit-opt">optionnel</span></label>
<input type="text" id="editTypeEnreg" class="edit-inp mono" placeholder="ex : DMAIN">
<span class="edit-hint">Si le même tag RDI existe dans plusieurs types de lignes, précisez lequel utiliser.</span>
</div>
</div>
<div class="edit-fg">
<label class="edit-lbl">XPath</label>
<input type="text" id="editXpath" class="edit-inp mono" placeholder="ex : /rsm:CrossIndustryInvoice/...">
</div>
<div class="edit-fg">
<label class="edit-lbl">Attribut <span class="edit-opt">optionnel</span></label>
<input type="text" id="editAttribute" class="edit-inp mono" placeholder="ex : schemeID, format">
<span class="edit-hint">Laissez vide pour extraire le texte. Indiquez un nom d'attribut pour extraire sa valeur.</span>
</div>
</div>
<!-- Comportement -->
<div class="edit-section">
<div class="edit-section-title">Comportement</div>
<div class="edit-row-2">
<div class="edit-fg">
<label class="edit-lbl">Obligatoire</label>
<select id="editObligatoire" class="edit-inp">
<option value="Oui">Oui</option>
<option value="Non">Non</option>
<option value="Dependant">Dépendant</option>
</select>
</div>
<div class="edit-fg">
<label class="edit-lbl">Ignorer les erreurs</label>
<select id="editIgnore" class="edit-inp">
<option value="Non">Non</option>
<option value="Oui">Oui</option>
</select>
<span class="edit-hint">Si Oui, marqué "Ignoré" dans la liste</span>
</div>
</div>
<div class="edit-fg">
<label class="edit-lbl">Règle de gestion (RDG)</label>
<textarea id="editRdg" class="edit-inp mono textarea-rdg" placeholder="Décrivez la règle métier applicable…"></textarea>
</div>
</div>
</div>
<div class="edit-field-footer">
<button class="edit-btn-cancel" id="editCancelBtn">Annuler</button>
<button class="edit-btn-save" id="btnSave">Sauvegarder</button>
</div>
</div>
</div>

<!-- MODAL SÉLECTION MAPPINGS (ajout champ multi-mapping) -->
<div id="selectMappingsModal" class="modal">
<div class="modal-content" style="max-width:520px">
<div class="modal-header">
<h2>Ajouter le champ à quel(s) mapping(s) ?</h2>
<span class="modal-close" id="selectMappingsClose">&times;</span>
</div>
<p style="margin-bottom:12px;color:#555;font-size:0.93em">Sélectionnez les mappings dans lesquels ce nouveau champ sera ajouté. Le mapping actuel est présélectionné.</p>
<div id="selectMappingsList" style="display:flex;flex-direction:column;gap:8px;max-height:300px;overflow-y:auto;margin-bottom:18px"></div>
<div style="display:flex;gap:10px;justify-content:flex-end">
<button class="btn-secondary" id="selectMappingsCancel">Annuler</button>
<button class="btn" id="selectMappingsConfirm">Continuer →</button>
</div>
</div>
</div>

<!-- MODAL RESTAURATION -->
<!-- MODAL EDITION RÈGLE -->
<div id="editRuleModal" class="modal">
<div class="modal-content" style="max-width:900px">
<div class="modal-header">
<h2 id="ruleModalTitle">Créer une règle</h2>
<span class="modal-close" id="ruleModalClose">&times;</span>
</div>
<div class="form-group">
<label>Nom de la règle :</label>
<input type="text" id="ruleName" placeholder="Ex: Facture B2G Chorus">
</div>
<div class="form-group">
<label>Description :</label>
<textarea id="ruleDescription" placeholder="Expliquez en quelques mots à quoi sert cette règle"></textarea>
</div>
<div class="form-group">
<label>Catégorie :</label>
<select id="ruleCategory"></select>
</div>
<div class="form-group">
<label style="display:flex;align-items:center;gap:8px">
<input type="checkbox" id="ruleEnabled" checked style="width:20px;height:20px">
<span>Règle activée</span>
</label>
</div>
<div class="form-group">
<label>Applicable aux types de factures :</label>
<div id="ruleFormsContainer" style="display:flex;flex-direction:column;gap:8px;padding:10px;background:#f9f9f9;border-radius:6px"></div>
<small style="display:block;color:#666;font-size:0.85em;margin-top:4px">
Si aucune case n'est cochée, la règle s'appliquera à tous les types de factures.
</small>
</div>
<hr style="margin:20px 0;border:none;border-top:2px solid #eee">
<h3>Conditions (SI...)</h3>
<p style="font-size:0.9em;color:#666;margin-bottom:12px">Si toutes ces conditions sont remplies, les actions seront déclenchées. Laissez vide pour appliquer toujours.</p>
<div id="conditionsList"></div>
<button class="btn-secondary" id="btnAddCondition" style="margin-top:10px">+ Ajouter une condition</button>
<hr style="margin:20px 0;border:none;border-top:2px solid #eee">
<h3>Actions (ALORS...)</h3>
<p style="font-size:0.9em;color:#666;margin-bottom:12px">Ces actions seront appliquées si les conditions sont remplies.</p>
<div id="actionsList"></div>
<button class="btn-secondary" id="btnAddAction" style="margin-top:10px">+ Ajouter une action</button>
<hr style="margin:20px 0;border:none;border-top:2px solid #eee">
<button class="btn" id="btnSaveRule">Enregistrer la règle</button>
</div>
</div>

<!-- Create Mapping Modal -->
<div id="createMappingModal" class="modal">
<div class="modal-content">
<div class="modal-header create">
<span class="warning-icon">➕</span>
<h2>Créer un nouveau mapping</h2>
</div>
<div class="modal-body">
<div class="form-group" style="margin-bottom:15px">
<label>Nom du mapping :</label>
<input type="text" id="newMappingName" placeholder="Ex: Mon nouveau mapping" style="width:100%">
</div>
<div class="form-group">
<label>Cloner depuis un mapping existant (optionnel) :</label>
<select id="copyFromMapping" class="mapping-type-select">
<option value="">Mapping vide</option>
</select>
<small style="display:block;color:#666;font-size:0.85em;margin-top:4px">
Choisissez un mapping existant pour copier sa configuration, ou laissez vide
</small>
</div>
</div>
<div class="modal-footer">
<button class="btn-secondary" onclick="closeCreateMappingModal()">Annuler</button>
<button class="btn-create" onclick="confirmCreateMapping()">
<span>✓</span> Créer
</button>
</div>
</div>
</div>

<!-- Delete Mapping Modal -->
<div id="deleteMappingModal" class="modal">
<div class="modal-content">
<div class="modal-header delete">
<span class="warning-icon">⚠️</span>
<h2>Confirmation de suppression</h2>
</div>
<div class="modal-body">
<div class="warning-text">
<strong>⚠️ ATTENTION - Cette action est irréversible !</strong>
Vous êtes sur le point de supprimer définitivement le mapping suivant :
</div>
<div style="background:#f8fafc;padding:1rem;border-radius:0.5rem;margin:1rem 0">
<p><strong>Nom :</strong> <span id="deleteMappingName"></span></p>
</div>
<p style="color:#64748b;font-size:0.9rem">
Cette suppression supprimera toutes les données associées à ce mapping. 
Assurez-vous d'avoir une sauvegarde si nécessaire.
</p>
</div>
<div class="modal-footer">
<button class="btn-secondary" onclick="closeDeleteMappingModal()">Annuler</button>
<button class="btn-delete" onclick="confirmDeleteMapping()">
<span>🗑️</span> Supprimer définitivement
</button>
</div>
</div>
</div>

<!-- MODAL AUTEUR -->
<div id="authorModal" class="modal">
<div class="modal-content" style="max-width:420px;text-align:center;padding:32px 28px">
<div class="author-icon">👤</div>
<h2 style="margin-bottom:6px;font-size:1.15rem">Qui fait cette modification ?</h2>
<p style="color:#64748b;font-size:0.9em;margin-bottom:18px">Votre nom sera associé à cette modification dans l'historique.</p>
<input type="text" id="authorInput" placeholder="Entrez votre prénom ou nom…" autocomplete="off">
<div style="display:flex;gap:10px;justify-content:center;margin-top:18px">
<button class="btn-secondary" id="authorCancelBtn">Annuler</button>
<button class="btn" id="authorConfirmBtn" style="width:auto;padding:10px 28px">Confirmer</button>
</div>
</div>
</div>

<!-- MODAL HISTORIQUE -->
<div id="historyModal" class="modal">
<div class="modal-content" style="max-width:720px">
<div class="modal-header" style="border-bottom:1px solid #e2e8f0;margin-bottom:16px;padding-bottom:12px">
<h2 style="font-size:1.05rem;font-weight:700;color:#1e293b">📋 Historique des modifications</h2>
<span class="modal-close" id="historyModalClose">&times;</span>
</div>
<div id="auditList" class="audit-list"></div>
<div style="display:flex;justify-content:flex-end">
<button class="btn-secondary" id="historyCloseBtn">Fermer</button>
</div>
</div>
</div>

<div id="tooltip" class="tooltip"></div>
<script>
var BASE='__URL_PREFIX__';
var currentMapping=null;
var currentIndex=null;
var tooltip=document.getElementById('tooltip');
var mappingsIndex = { mappings: [] };
var mappingToDelete = null;
var pendingAuditCallback = null; // callback à exécuter après saisie de l'auteur

function positionTooltip(e){
var margin=12;
var vpW=window.innerWidth;
var vpH=window.innerHeight;
var scrollY=window.scrollY||document.documentElement.scrollTop;
var scrollX=window.scrollX||document.documentElement.scrollLeft;
// Mesure la taille réelle du tooltip (il est visible à ce stade)
var tw=tooltip.offsetWidth;
var th=tooltip.offsetHeight;
var x=e.pageX+margin;
var y=e.pageY+margin;
// Dépasse à droite → coller à gauche du curseur
if(x+tw>scrollX+vpW-margin){x=e.pageX-tw-margin;}
// Dépasse en bas → afficher au-dessus du curseur
if(y+th>scrollY+vpH-margin){y=e.pageY-th-margin;}
// Garde dans les limites hautes/gauches
if(y<scrollY+margin){y=scrollY+margin;}
if(x<scrollX+margin){x=scrollX+margin;}
tooltip.style.left=x+'px';
tooltip.style.top=y+'px';
}

/* ---- ONGLETS ---- */
document.getElementById('tabControle').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentControle').classList.add('active');
});
document.getElementById('tabParam').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentParam').classList.add('active');
loadMappings();
loadGlobalSettings();
});

/* ── Global settings toggle (schematron on/off) ────────────────────────── */
async function loadGlobalSettings(){
  try{
    var resp=await fetch(BASE+'/api/rules');
    if(!resp.ok)return;
    var data=await resp.json();
    var cb=document.getElementById('settingSchematronEnabled');
    if(cb){cb.checked=data.schematron_enabled!==false;}
  }catch(e){console.warn('loadGlobalSettings',e);}
}

document.getElementById('settingSchematronEnabled').addEventListener('change',async function(){
  var enabled=this.checked;
  try{
    var resp=await fetch(BASE+'/api/rules');
    var data=resp.ok?await resp.json():{};
    delete data.categories;  // injecté par GET, pas à renvoyer
    data.schematron_enabled=enabled;
    var save=await fetch(BASE+'/api/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(save.ok){
      var ind=document.getElementById('settingSaveIndicator');
      if(ind){ind.style.opacity='1';setTimeout(function(){ind.style.opacity='0';},1500);}
    }
  }catch(e){alert('Erreur sauvegarde paramètre: '+e.message);}
});
document.getElementById('tabRules').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentRules').classList.add('active');
loadRules();
});
document.getElementById('tabAide').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentAide').classList.add('active');
});
document.getElementById('tabBatch').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentBatch').classList.add('active');
});
document.getElementById('tabStats').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentStats').classList.add('active');
statsLoadAll();
});

/* ============================================================
   STATISTIQUES
   ============================================================ */
var STATS_TYPE_LABELS = {
  'simple':'CART Simple',
  'groupee':'CART Groupée',
  'ventesdiverses':'Ventes Diverses',
  'flux':'Flux Générique',
  'CARTsimple':'CART Simple'
};
var STATS_PALETTE = ['#4f46e5','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#ec4899','#84cc16','#f97316'];
var statsState = { lastSummary:null, lastTrend:null, lastTopKo:null, lastHistory:null };

function statsLabel(t){ return STATS_TYPE_LABELS[t] || (t || 'inconnu'); }
function statsPctClass(p){
  if(p>=75) return 'pct-75';
  if(p>=50) return 'pct-50';
  if(p>=25) return 'pct-25';
  return 'pct-0';
}
function statsBuildQuery(){
  var p = new URLSearchParams();
  var t = document.getElementById('statsFilterType').value;
  var m = document.getElementById('statsFilterMode').value;
  var s = document.getElementById('statsFilterStart').value;
  var e = document.getElementById('statsFilterEnd').value;
  if(t && t!=='all') p.set('type',t);
  if(m) p.set('mode',m);
  if(s) p.set('start',s);
  if(e) p.set('end',e);
  return p.toString() ? ('?'+p.toString()) : '';
}

async function statsLoadAll(){
  await statsLoadTypeOptions();
  await Promise.all([
    statsLoadSummary(),
    statsLoadTrend(),
    statsLoadTopKo(),
    statsLoadHistory()
  ]);
}

async function statsLoadTypeOptions(){
  try{
    var r = await fetch(BASE+'/api/stats/types');
    var d = await r.json();
    // Enrichit la table de libellés avec ceux résolus côté serveur
    if(d.labels){
      Object.keys(d.labels).forEach(function(k){ STATS_TYPE_LABELS[k] = d.labels[k]; });
    }
    var sel = document.getElementById('statsFilterType');
    var current = sel.value || 'all';
    sel.innerHTML = '<option value="all">Tous les types</option>';
    (d.types||[]).forEach(function(t){
      var id = (typeof t === 'string') ? t : t.id;
      var label = (typeof t === 'string') ? statsLabel(t) : (t.label || statsLabel(t.id));
      var o = document.createElement('option');
      o.value = id; o.textContent = label;
      sel.appendChild(o);
    });
    sel.value = current;
  }catch(e){}
}

async function statsLoadSummary(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/summary'+qs);
  var d = await r.json();
  statsState.lastSummary = d;
  document.getElementById('kpiTotal').textContent = d.total_invoices||0;
  document.getElementById('kpiAvgPct').textContent = ((d.avg_conformity_pct||0).toFixed(1))+'%';
  document.getElementById('kpiErrors').textContent = d.nb_errors||0;
  var unit = (d.by_mode||[]).find(function(x){return x.mode==='unitaire';});
  var batch= (d.by_mode||[]).find(function(x){return x.mode==='batch';});
  document.getElementById('kpiUnitaire').textContent = unit?unit.count:0;
  document.getElementById('kpiBatch').textContent = batch?batch.count:0;
  document.getElementById('kpiTypes').textContent = (d.by_type||[]).length;

  // Range hint (bornes globales)
  var hint = document.getElementById('statsRangeHint');
  if(d.date_min && d.date_max){
    hint.textContent = 'Données du '+d.date_min+' au '+d.date_max;
  } else {
    hint.textContent = 'Aucune donnée enregistrée pour le moment';
  }

  // Tableau par type
  var rows = (d.by_type||[]).map(function(x){
    var byMode = (d.by_type_mode||[]).filter(function(y){return y.type===x.type;});
    var u = (byMode.find(function(y){return y.mode==='unitaire';})||{}).count||0;
    var b = (byMode.find(function(y){return y.mode==='batch';})||{}).count||0;
    var pct = (x.avg_pct||0).toFixed(1);
    return '<tr>'
      +'<td style="font-weight:600">'+statsLabel(x.type)+'</td>'
      +'<td>'+x.count+'</td>'
      +'<td>'+u+'</td>'
      +'<td>'+b+'</td>'
      +'<td>'
        +'<div style="display:flex;align-items:center;gap:8px;min-width:160px">'
          +'<div class="progress-track" style="flex:1;height:10px"><div class="progress-fill '+statsPctClass(x.avg_pct||0)+'" style="width:'+(x.avg_pct||0)+'%"></div></div>'
          +'<span style="font-weight:700;color:#1e293b;width:48px;text-align:right">'+pct+'%</span>'
        +'</div>'
      +'</td>'
    +'</tr>';
  }).join('');
  if(!rows){
    document.getElementById('statsByType').innerHTML = '<p style="color:#94a3b8;font-style:italic">Aucune donnée pour les filtres sélectionnés.</p>';
  } else {
    document.getElementById('statsByType').innerHTML =
      '<table class="main-table" style="margin-top:6px">'
      +'<thead><tr><th>Type</th><th>Total</th><th>Unitaire</th><th>Batch</th><th>Taux moyen</th></tr></thead>'
      +'<tbody>'+rows+'</tbody></table>';
  }
}

async function statsLoadTrend(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/conformity-trend'+qs);
  var d = await r.json();
  statsState.lastTrend = d;
  statsRenderTrend(d);
}

function statsRenderTrend(d){
  var wrap = document.getElementById('statsChartWrap');
  var legend = document.getElementById('statsChartLegend');
  var dates = d.dates || [];
  var series = d.series || [];
  if(!dates.length){
    wrap.innerHTML = '<p style="color:#94a3b8;font-style:italic;padding:30px;text-align:center">Aucune donnée à afficher pour ces filtres.</p>';
    legend.innerHTML = '';
    return;
  }
  // Dimensions
  var W = wrap.clientWidth || 800;
  var H = 280;
  var padL = 44, padR = 16, padT = 18, padB = 36;
  var innerW = W - padL - padR;
  var innerH = H - padT - padB;
  var n = dates.length;
  var xFor = function(i){ return n<=1 ? padL+innerW/2 : padL + i*(innerW/(n-1)); };
  var yFor = function(p){ return padT + innerH - (Math.max(0,Math.min(100,p))/100)*innerH; };

  // Grille horizontale
  var grid = '';
  [0,25,50,75,100].forEach(function(g){
    var y = yFor(g);
    grid += '<line x1="'+padL+'" y1="'+y+'" x2="'+(W-padR)+'" y2="'+y+'" stroke="#e2e8f0" stroke-width="1"/>';
    grid += '<text x="'+(padL-6)+'" y="'+(y+4)+'" text-anchor="end" font-size="10" fill="#94a3b8">'+g+'%</text>';
  });

  // Axe X (étiquettes : début / milieu / fin pour limiter le bruit)
  var labelIdx = [0];
  if(n>=3){ labelIdx.push(Math.floor(n/2)); labelIdx.push(n-1); }
  else if(n===2){ labelIdx.push(1); }
  var xLabels = labelIdx.map(function(i){
    return '<text x="'+xFor(i)+'" y="'+(H-padB+18)+'" text-anchor="middle" font-size="10" fill="#64748b">'+dates[i]+'</text>';
  }).join('');

  // Tracer chaque série
  var paths = '';
  var dots = '';
  var legendItems = '';
  series.forEach(function(s, idx){
    var color = STATS_PALETTE[idx % STATS_PALETTE.length];
    var pts = [];
    s.points.forEach(function(p, i){
      if(p.pct === null || p.pct === undefined) return;
      pts.push({x: xFor(i), y: yFor(p.pct), pct: p.pct, count: p.count, date: dates[i]});
    });
    if(pts.length){
      var dPath = pts.map(function(pt,i){ return (i===0?'M':'L')+pt.x+' '+pt.y; }).join(' ');
      paths += '<path d="'+dPath+'" fill="none" stroke="'+color+'" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';
      pts.forEach(function(pt){
        var title = statsLabel(s.type)+' — '+pt.date+' : '+pt.pct.toFixed(1)+'% ('+pt.count+' factures)';
        dots += '<circle cx="'+pt.x+'" cy="'+pt.y+'" r="3.5" fill="#fff" stroke="'+color+'" stroke-width="2"><title>'+title+'</title></circle>';
      });
    }
    legendItems += '<span style="display:inline-flex;align-items:center;gap:6px"><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:'+color+'"></span>'+statsLabel(s.type)+'</span>';
  });

  wrap.innerHTML = '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="width:100%;height:'+H+'px;display:block">'
    + grid + xLabels + paths + dots + '</svg>';
  legend.innerHTML = legendItems;
}

async function statsLoadTopKo(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/top-ko'+qs+(qs?'&':'?')+'limit=15');
  var d = await r.json();
  statsState.lastTopKo = d;
  var items = d.items || [];
  if(!items.length){
    document.getElementById('statsTopKo').innerHTML = '<p style="color:#94a3b8;font-style:italic">Aucun champ KO sur ce périmètre — soit aucune facture, soit toutes conformes 🎉</p>';
    return;
  }
  var maxTotal = items.reduce(function(m,x){return Math.max(m,x.total||0);},1);
  var rows = items.map(function(x){
    var pct = Math.round(100*(x.total||0)/maxTotal);
    var oblig = (x.obligatoire==='Oui') ? '<span style="color:#ef4444;font-weight:700;font-size:0.78em">obligatoire</span>' : '<span style="color:#94a3b8;font-size:0.78em">optionnel</span>';
    return '<tr>'
      +'<td style="font-weight:700;color:#1e293b">'+x.balise+'</td>'
      +'<td style="color:#475569">'+(x.libelle||'')+'</td>'
      +'<td>'+statsLabel(x.type_formulaire)+'</td>'
      +'<td>'+oblig+'</td>'
      +'<td style="text-align:right;font-weight:600">'+(x.nb_erreur||0)+'</td>'
      +'<td style="text-align:right;color:#d97706;font-weight:600">'+(x.nb_ambigu||0)+'</td>'
      +'<td>'
        +'<div style="display:flex;align-items:center;gap:8px;min-width:140px">'
          +'<div class="progress-track" style="flex:1;height:8px"><div class="progress-fill pct-0" style="width:'+pct+'%;background:linear-gradient(90deg,#ef4444,#f87171)"></div></div>'
          +'<span style="font-weight:700;color:#ef4444;width:32px;text-align:right">'+x.total+'</span>'
        +'</div>'
      +'</td>'
    +'</tr>';
  }).join('');
  document.getElementById('statsTopKo').innerHTML =
    '<table class="main-table">'
    +'<thead><tr><th>BT</th><th>Libellé</th><th>Type</th><th></th><th style="text-align:right">Erreurs</th><th style="text-align:right">Ambigus</th><th>Occurrences</th></tr></thead>'
    +'<tbody>'+rows+'</tbody></table>';
}

async function statsLoadHistory(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/history'+qs+(qs?'&':'?')+'limit=50');
  var d = await r.json();
  statsState.lastHistory = d;
  var items = d.items || [];
  if(!items.length){
    document.getElementById('statsHistory').innerHTML = '<p style="color:#94a3b8;font-style:italic">Aucun contrôle enregistré pour ces filtres.</p>';
    return;
  }
  var rows = items.map(function(x){
    var pct = (x.conformity_pct||0).toFixed(1);
    var ts = (x.timestamp||'').replace('T',' ').slice(0,16);
    var status = x.error
      ? '<span style="color:#ef4444;font-weight:600">⚠ '+(x.error||'').slice(0,40)+'</span>'
      : (x.erreur>0 ? '<span style="color:#ef4444">'+x.erreur+' KO</span>' : '<span style="color:#10b981">OK</span>');
    return '<tr>'
      +'<td style="white-space:nowrap;font-family:monospace;font-size:0.85em">'+ts+'</td>'
      +'<td>'+statsLabel(x.type_formulaire)+'</td>'
      +'<td>'+(x.mode||'')+'</td>'
      +'<td style="font-family:monospace">'+(x.invoice_number||'—')+'</td>'
      +'<td style="color:#64748b;font-size:0.85em">'+(x.filename||'')+'</td>'
      +'<td style="text-align:right">'+(x.total||0)+'</td>'
      +'<td>'+status+'</td>'
      +'<td><div style="display:flex;align-items:center;gap:6px;min-width:120px">'
        +'<div class="progress-track" style="flex:1;height:8px"><div class="progress-fill '+statsPctClass(x.conformity_pct||0)+'" style="width:'+(x.conformity_pct||0)+'%"></div></div>'
        +'<span style="font-weight:700;width:42px;text-align:right">'+pct+'%</span>'
      +'</div></td>'
    +'</tr>';
  }).join('');
  document.getElementById('statsHistory').innerHTML =
    '<table class="main-table">'
    +'<thead><tr><th>Date</th><th>Type</th><th>Mode</th><th>N° facture</th><th>Fichier</th><th style="text-align:right">Total</th><th>Statut</th><th>Conformité</th></tr></thead>'
    +'<tbody>'+rows+'</tbody></table>';
}

function statsExportCsv(){
  var items = (statsState.lastHistory && statsState.lastHistory.items) || [];
  if(!items.length){ alert('Aucune ligne à exporter.'); return; }
  var hdr = ['date','type','mode','invoice_number','filename','total','ok','erreur','ignore','ambigu','conformity_pct','error'];
  var esc = function(v){
    if(v===null||v===undefined) return '';
    var s = String(v).replace(/"/g,'""');
    return /[",;\n]/.test(s) ? '"'+s+'"' : s;
  };
  var lines = [hdr.join(';')].concat(items.map(function(x){
    return [x.timestamp,x.type_formulaire,x.mode,x.invoice_number,x.filename,x.total,x.ok,x.erreur,x.ignore_count,x.ambigu,x.conformity_pct,x.error].map(esc).join(';');
  }));
  var blob = new Blob(["﻿"+lines.join('\n')], {type:'text/csv;charset=utf-8'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'facturix-historique.csv';
  document.body.appendChild(a); a.click(); a.remove();
}

document.getElementById('btnStatsApply').addEventListener('click', statsLoadAll);
document.getElementById('btnStatsReset').addEventListener('click', function(){
  document.getElementById('statsFilterType').value = 'all';
  document.getElementById('statsFilterMode').value = '';
  document.getElementById('statsFilterStart').value = '';
  document.getElementById('statsFilterEnd').value = '';
  statsLoadAll();
});
document.getElementById('btnStatsExportCsv').addEventListener('click', statsExportCsv);
window.addEventListener('resize', function(){
  if(document.getElementById('contentStats').classList.contains('active') && statsState.lastTrend){
    statsRenderTrend(statsState.lastTrend);
  }
});

/* ============================================================
   BATCH MODE
   ============================================================ */
// batchFilesMap : clé = numéro de facture (ou "tmp_N") → {key, invoiceNumber, pdfFile, rdiFile, pdfName, rdiName, pending}
var batchFilesMap={};
var batchTmpCounter=0;

// ── Dropzone setup ──────────────────────────────────────────
(function(){
  var dz=document.getElementById('batchDropZone');
  if(!dz)return;
  dz.addEventListener('dragover',function(e){e.preventDefault();dz.classList.add('drag-over');});
  dz.addEventListener('dragleave',function(){dz.classList.remove('drag-over');});
  dz.addEventListener('drop',function(e){
    e.preventDefault();dz.classList.remove('drag-over');
    batchHandleFileInput(e.dataTransfer.files);
  });
  // Empêcher le click du bouton interne de remonter deux fois
  dz.querySelector('button').addEventListener('click',function(e){e.stopPropagation();document.getElementById('batchFileInput').click();});
})();

document.getElementById('batchTypeControle').onchange=function(){batchUpdateDzHint();batchRenderFileList();batchUpdateLaunchBtn();};

function batchUpdateDzHint(){
  var mode=document.getElementById('batchTypeControle').value;
  var hints={xml:'PDF + RDI — les numéros de facture sont détectés automatiquement',rdi:'Fichiers RDI (.txt/.rdi) uniquement',xmlonly:'Fichiers PDF uniquement',cii:'Fichiers XML CII uniquement'};
  var el=document.getElementById('batchDzHint');
  if(el)el.textContent=hints[mode]||hints.xml;
  var inp=document.getElementById('batchFileInput');
  if(mode==='rdi')inp.accept='.txt,.rdi';
  else if(mode==='xmlonly')inp.accept='.pdf,.xml';
  else if(mode==='cii')inp.accept='.xml';
  else inp.accept='.pdf,.xml,.txt,.rdi';
}

async function batchHandleFileInput(files){
  if(!files||files.length===0)return;
  var arr=Array.from(files);
  // Afficher section fichiers immédiatement
  document.getElementById('batchFilesSection').style.display='block';
  // Ajouter chaque fichier en mode pending, puis résoudre
  var promises=arr.map(function(f){return batchAddFile(f);});
  await Promise.all(promises);
  batchRenderFileList();
  batchUpdateLaunchBtn();
  // Reset l'input pour permettre de re-sélectionner les mêmes fichiers
  document.getElementById('batchFileInput').value='';
}

async function batchAddFile(file){
  var ext=file.name.toLowerCase().split('.').pop();
  var isRdi=(ext==='txt'||ext==='rdi');
  var isPdf=(ext==='pdf'||ext==='xml');
  // Appel preview pour obtenir le N° de facture
  var invoiceNumber=null;
  try{
    var fd=new FormData();fd.append('file',file);
    fd.append('type_formulaire',document.getElementById('batchTypeFormulaire').value);
    var resp=await fetch(BASE+'/api/batch-preview',{method:'POST',body:fd});
    if(resp.ok){var d=await resp.json();invoiceNumber=d.invoice_number||null;}
  }catch(e){}
  // Clé : numéro de facture si dispo, sinon tmp
  var key=invoiceNumber||('tmp_'+(batchTmpCounter++));
  if(!batchFilesMap[key]){
    batchFilesMap[key]={key:key,invoiceNumber:invoiceNumber,pdfFile:null,rdiFile:null,pdfName:null,rdiName:null};
  } else if(invoiceNumber&&batchFilesMap[key].invoiceNumber!==invoiceNumber){
    // Collision de clé tmp → forcer nouveau
    key='tmp_'+(batchTmpCounter++);
    batchFilesMap[key]={key:key,invoiceNumber:invoiceNumber,pdfFile:null,rdiFile:null,pdfName:null,rdiName:null};
  }
  if(isRdi){batchFilesMap[key].rdiFile=file;batchFilesMap[key].rdiName=file.name;}
  else if(isPdf){batchFilesMap[key].pdfFile=file;batchFilesMap[key].pdfName=file.name;}
}

function batchRenderFileList(){
  var mode=document.getElementById('batchTypeControle').value;
  var needPdf=(mode!=='rdi');
  var needRdi=(mode!=='xmlonly'&&mode!=='cii');
  var keys=Object.keys(batchFilesMap);
  var section=document.getElementById('batchFilesSection');
  if(keys.length===0){section.style.display='none';return;}
  section.style.display='block';
  // En-tête
  var head=document.getElementById('batchFilesHead');
  var headCols='<tr><th>N° Facture</th>';
  if(needPdf)headCols+='<th>PDF</th>';
  if(needRdi)headCols+='<th>RDI</th>';
  headCols+='<th>Statut</th><th style="width:36px"></th></tr>';
  head.innerHTML=headCols;
  // Corps
  var body=document.getElementById('batchFilesBody');
  body.innerHTML='';
  keys.forEach(function(key){
    var e=batchFilesMap[key];
    var hasPdf=!!e.pdfFile;
    var hasRdi=!!e.rdiFile;
    var ready=(!needPdf||hasPdf)&&(!needRdi||hasRdi);
    var hasExtra=(needPdf&&hasPdf)||(needRdi&&hasRdi);
    var tr=document.createElement('tr');
    // Colonne N° facture
    var numCell='<td><div class="batch-file-num">'+(e.invoiceNumber?escHtml(e.invoiceNumber):'<span style="color:#94a3b8;font-weight:400;font-style:italic">Inconnu</span>')+'</div></td>';
    // Colonnes fichiers
    var pdfCell='',rdiCell='';
    if(needPdf)pdfCell='<td>'+(hasPdf?'<span class="batch-file-chip pdf">📄 '+escHtml(e.pdfName)+'</span>':'<span class="batch-file-chip missing">— manquant</span>')+'</td>';
    if(needRdi)rdiCell='<td>'+(hasRdi?'<span class="batch-file-chip rdi">📋 '+escHtml(e.rdiName)+'</span>':'<span class="batch-file-chip missing">— manquant</span>')+'</td>';
    // Statut
    var statusLabel=ready?'✓ Prêt':(hasExtra?'⚠ Incomplet':'⚠ Vide');
    var statusClass=ready?'ok':'warn';
    var statusCell='<td><span class="batch-status-chip '+statusClass+'">'+statusLabel+'</span></td>';
    // Supprimer
    var removeCell='<td><button class="btn-batch-remove" data-key="'+escHtml(key)+'" title="Supprimer">✕</button></td>';
    tr.innerHTML=numCell+pdfCell+rdiCell+statusCell+removeCell;
    tr.querySelector('.btn-batch-remove').addEventListener('click',function(){
      delete batchFilesMap[this.dataset.key];
      batchRenderFileList();
      batchUpdateLaunchBtn();
    });
    body.appendChild(tr);
  });
}

function batchUpdateLaunchBtn(){
  var btn=document.getElementById('btnLaunchBatch');
  if(!btn)return;
  var mode=document.getElementById('batchTypeControle').value;
  var needPdf=(mode!=='rdi');
  var needRdi=(mode!=='xmlonly'&&mode!=='cii');
  var all=Object.values(batchFilesMap);
  var ready=all.filter(function(e){return(!needPdf||!!e.pdfFile)&&(!needRdi||!!e.rdiFile);}).length;
  var incomplete=all.filter(function(e){return !((!needPdf||!!e.pdfFile)&&(!needRdi||!!e.rdiFile));}).length;
  var total=ready+incomplete;
  if(total>0){
    btn.style.opacity='1';btn.style.pointerEvents='auto';
    var lbl='▶ Lancer le contrôle ('+ready+' facture'+(ready>1?'s':'');
    if(incomplete>0)lbl+=' · '+incomplete+' incomplète'+(incomplete>1?'s':'')+' ignorée'+(incomplete>1?'s':'');
    lbl+=')';
    btn.textContent=lbl;
  } else {
    btn.style.opacity='0.5';btn.style.pointerEvents='none';btn.textContent='▶ Lancer le contrôle (0 facture)';
  }
}

async function batchLaunch(){
  var mode=document.getElementById('batchTypeControle').value;
  var typeForm=document.getElementById('batchTypeFormulaire').value;
  var needPdf=(mode!=='rdi');
  var needRdi=(mode!=='xmlonly'&&mode!=='cii');
  var fd=new FormData();
  fd.append('type_formulaire',typeForm);fd.append('type_controle',mode);
  var count=0;
  var skipped=[];
  Object.values(batchFilesMap).forEach(function(e){
    if((!needPdf||!!e.pdfFile)&&(!needRdi||!!e.rdiFile)){
      if(e.pdfFile)fd.append('pdf_'+count,e.pdfFile);
      if(e.rdiFile)fd.append('rdi_'+count,e.rdiFile);
      fd.append('name_'+count,e.rdiName||e.pdfName||('Facture '+(count+1)));
      fd.append('invoice_number_'+count,e.invoiceNumber||'');
      count++;
    } else {
      // Fichier sans paire — ignoré, on mémorise pour le rapport
      var fname=e.rdiName||e.pdfName||e.key;
      var missing=[];
      if(needPdf&&!e.pdfFile)missing.push('PDF manquant');
      if(needRdi&&!e.rdiFile)missing.push('RDI manquant');
      skipped.push({name:fname,invoiceNumber:e.invoiceNumber,reason:missing.join(', ')});
    }
  });
  fd.append('pair_count',count);
  fd.append('skipped_json',JSON.stringify(skipped));
  document.getElementById('batchLoading').style.display='block';
  document.getElementById('batchResults').style.display='none';
  document.getElementById('batchLoadingMsg').textContent='Contrôle en cours… ('+count+' facture'+(count>1?'s':'')+')';
  try{
    var resp=await fetch(BASE+'/controle-batch',{method:'POST',body:fd});
    var data=await resp.json();
    if(data.error){alert('Erreur: '+data.error);return;}
    data.skipped=skipped;
    batchRenderResults(data);
  }catch(e){alert('Erreur réseau: '+e);}
  finally{document.getElementById('batchLoading').style.display='none';}
}

function batchRenderResults(data){
  var batch=data.batch||[];
  var skipped=data.skipped||[];
  var now=new Date();
  var dateStr=now.toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'})
    +' '+now.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
  document.getElementById('batchResultsSub').textContent=
    batch.length+' facture'+(batch.length>1?'s':'')+' analysée'+(batch.length>1?'s':'')+' · '+dateStr;

  // Bandeau fichiers ignorés
  var skippedEl=document.getElementById('batchSkippedWarning');
  if(skippedEl){
    if(skipped.length>0){
      var skippedRows=skipped.map(function(s){
        var num=s.invoiceNumber?(' <span style="font-weight:700">N°'+escHtml(s.invoiceNumber)+'</span>'):'';
        return '<li>'+escHtml(s.name)+num+' — <em>'+escHtml(s.reason)+'</em></li>';
      }).join('');
      skippedEl.innerHTML='<span style="font-weight:700">⚠ '+skipped.length+' fichier'+(skipped.length>1?'s':'')+' ignoré'+(skipped.length>1?'s':'')+' (paire incomplète) :</span><ul style="margin:6px 0 0 16px;padding:0">'+skippedRows+'</ul>';
      skippedEl.style.display='block';
    } else {
      skippedEl.style.display='none';
    }
  }

  // Stats globales
  var nbTotal=batch.length;
  var nbOk=batch.filter(function(b){return !b.error&&b.stats&&b.stats.erreur===0;}).length;
  var nbErr=batch.filter(function(b){return b.error||(b.stats&&b.stats.erreur>0);}).length;
  var totalErreurs=batch.reduce(function(acc,b){return acc+(b.stats?b.stats.erreur:0);},0);
  var statsEl=document.getElementById('batchStatsGlobal');
  statsEl.innerHTML=
    '<div class="stat-card" style="min-width:110px"><div>Factures</div><div class="stat-value" style="color:#667eea">'+nbTotal+'</div></div>'+
    '<div class="stat-card erreur"><div>Avec erreurs</div><div class="stat-value">'+nbErr+'</div></div>'+
    '<div class="stat-card ok"><div>Sans erreur</div><div class="stat-value">'+nbOk+'</div></div>'+
    '<div class="stat-card" style="border-color:#fca5a5"><div>Erreurs totales</div><div class="stat-value" style="color:#ef4444">'+totalErreurs+'</div></div>';

  // Tableau
  var tbody=document.getElementById('batchTableBody');
  tbody.innerHTML='';

  batch.forEach(function(inv,i){
    var invRow=document.createElement('div');
    invRow.className='batch-inv-row';

    if(inv.error){
      // Ligne en erreur technique
      var mainDiv=document.createElement('div');
      mainDiv.className='batch-inv-main inv-error';
      mainDiv.innerHTML=
        '<div><div class="batch-inv-name">'+escHtml(inv.name)+'<span class="bsub">Erreur technique</span></div></div>'+
        '<div class="batch-sc" colspan="3" style="grid-column:span 3;color:#f59e0b">—</div>'+
        '<div></div>'+
        '<div style="padding:0 6px;color:#f59e0b;font-size:0.82em">⚠ '+escHtml(inv.error)+'</div>'+
        '<div></div>';
      invRow.appendChild(mainDiv);
      tbody.appendChild(invRow);
      return;
    }

    var stats=inv.stats||{};
    var nbOkInv=stats.ok||0;
    var nbErrInv=stats.erreur||0;
    var nbAmbInv=stats.ambigu||0;
    var nbTotInv=stats.total||1;
    var pct=Math.round(nbOkInv/nbTotInv*100);
    var pctClass=pct>=90?'good':(pct>=70?'mid':'bad');

    // Tags d'erreur (les BTs en ERREUR)
    var errTags='';
    if(nbErrInv===0&&nbAmbInv===0){
      errTags='<span style="font-size:0.82em;color:#10b981;font-weight:600;padding:0 6px">✅ Aucune erreur</span>';
    } else {
      var errResults=(inv.results||[]).filter(function(r){return r.status==='ERREUR'||r.status==='AMBIGU';});
      var shown=errResults.slice(0,6);
      shown.forEach(function(r){
        errTags+='<span class="batch-etag'+(r.status==='AMBIGU'?' amb':'')+'">'+escHtml(r.balise)+'</span>';
      });
      if(errResults.length>6){
        errTags+='<span class="batch-etag-more" onclick="batchToggleDetail('+i+')" style="cursor:pointer">+'+(errResults.length-6)+' autres…</span>';
      }
    }

    // N° de facture depuis BT-1 des résultats (RDI en priorité, sinon XML)
    var bt1res=(inv.results||[]).find(function(r){return r.balise==='BT-1';});
    var invoiceNum=inv.invoice_number||(bt1res?(bt1res.rdi||bt1res.xml||''):'');

    var mainDiv=document.createElement('div');
    mainDiv.className='batch-inv-main '+(nbErrInv>0?'has-err':'all-ok');
    mainDiv.innerHTML=
      '<div><div class="batch-inv-num">'+(invoiceNum?escHtml(invoiceNum):'<span style="color:#94a3b8;font-weight:400;font-style:italic;font-size:0.85em">N° inconnu</span>')+'</div><div class="batch-inv-filename" data-fullname="'+escHtml(inv.name)+'">'+escHtml(inv.name)+'</div></div>'+
      '<div class="batch-sc ok">'+nbOkInv+'</div>'+
      '<div class="batch-sc err">'+nbErrInv+'</div>'+
      '<div class="batch-sc amb">'+nbAmbInv+'</div>'+
      '<div class="batch-pct-wrap"><div class="batch-pct-track"><div class="batch-pct-fill '+pctClass+'" style="width:'+pct+'%"></div></div><span class="batch-pct-lbl '+pctClass+'">'+pct+'%</span></div>'+
      '<div style="padding:0 6px;display:flex;flex-wrap:wrap;align-items:center;gap:2px">'+errTags+'</div>'+
      '<div style="padding:0 6px"><button class="btn-batch-detail" id="batchDetailBtn_'+i+'" onclick="batchToggleDetail('+i+')"><span class="b-arrow">▶</span> Détail</button></div>';

    // Zone détail
    var detailZone=document.createElement('div');
    detailZone.className='batch-detail-zone';
    detailZone.id='batchDetailZone_'+i;
    if(inv.categories_results){
      var actionsBar='<div style="display:flex;gap:8px;margin-bottom:14px;align-items:center;flex-wrap:wrap">'+
        '<button class="btn-secondary" onclick="batchExpandAll(\''+i+'\')" style="font-size:0.8em;padding:5px 11px">Tout déplier</button>'+
        '<button class="btn-secondary" onclick="batchCollapseAll(\''+i+'\')" style="font-size:0.8em;padding:5px 11px">Tout replier</button>'+
        '<span style="font-size:0.78em;color:#94a3b8;margin-left:auto">'+(invoiceNum?escHtml(invoiceNum)+' — ':'')+escHtml(inv.name)+' · '+nbTotInv+' champs</span>'+
        '</div>';
      var catHtml=batchBuildCategoriesHTML(inv.categories_results,inv.type_controle,'b'+i+'_');
      detailZone.innerHTML=actionsBar+catHtml;
      // Attacher les événements après injection
      setTimeout(function(di,dz){return function(){batchAttachDetailEvents(dz,di);};}(i,detailZone),0);
    } else {
      detailZone.innerHTML='<div style="color:#94a3b8;font-size:0.85em;font-style:italic">Aucune donnée disponible.</div>';
    }

    invRow.appendChild(mainDiv);
    invRow.appendChild(detailZone);
    tbody.appendChild(invRow);
  });

  document.getElementById('batchResults').style.display='block';

  // Export CSV
  document.getElementById('btnBatchCsvAll').onclick=function(){batchExportCsv(batch);};
}

function batchToggleDetail(i){
  var zone=document.getElementById('batchDetailZone_'+i);
  var btn=document.getElementById('batchDetailBtn_'+i);
  if(!zone||!btn)return;
  var open=zone.classList.contains('open');
  if(open){zone.classList.remove('open');btn.classList.remove('open');}
  else{zone.classList.add('open');btn.classList.add('open');}
}

function batchExpandAll(i){
  var zone=document.getElementById('batchDetailZone_'+i);
  if(!zone)return;
  zone.querySelectorAll('.category-content').forEach(function(c){c.classList.add('open');});
  zone.querySelectorAll('.article-content').forEach(function(c){c.style.display='block';});
}
function batchCollapseAll(i){
  var zone=document.getElementById('batchDetailZone_'+i);
  if(!zone)return;
  zone.querySelectorAll('.category-content').forEach(function(c){c.classList.remove('open');});
  zone.querySelectorAll('.article-content').forEach(function(c){c.style.display='none';});
}

function batchBuildCategoriesHTML(categoriesResults,typeControle,idPfx){
  var categoryOrder={'BG-INFOS-GENERALES':1,'BG-TOTAUX':2,'BG-TVA':3,'BG-LIGNES':4,'BG-VENDEUR':5,'BG-ACHETEUR':6};
  var sorted=Object.keys(categoriesResults).sort(function(a,b){
    return (categoryOrder[a]||999)-(categoryOrder[b]||999);
  });
  var out='';
  sorted.forEach(function(bgId){
    var cat=categoriesResults[bgId];
    if(!cat.champs||cat.champs.length===0)return;
    var errCount=cat.stats.erreur||0;
    var headerBg=errCount>0?'background:#7b1e1e':(cat.stats.ok===cat.stats.total&&cat.stats.total>0?'background:#2e7d32':'background:#366092');
    var catId=idPfx+'cat-'+bgId;
    out+='<div class="category">'+
      '<div class="category-header" data-cat="'+catId+'" style="'+headerBg+'">'+
      '<div>'+escHtml(cat.titre)+'</div>'+
      '<div>'+cat.stats.total+' champs | OK: '+cat.stats.ok+' | Err: '+errCount+'</div></div>'+
      '<div class="category-content" id="'+catId+'">';
    var nonArt=cat.champs.filter(function(r){return r.article_index===undefined;});
    var artChamps=cat.champs.filter(function(r){return r.article_index!==undefined;});
    if(nonArt.length>0){
      out+='<table class="main-table"><thead><tr>'+
        '<th class="col-status"></th><th class="col-bt">BT</th>'+
        '<th class="col-libelle">Libellé</th><th class="col-regles">Règles testées</th>'+
        '<th class="col-valeurs">Valeurs</th><th class="col-erreurs">Détails erreurs</th>'+
        '</tr></thead><tbody>';
      nonArt.forEach(function(r){
        var isXmlOnly=(typeControle==='cii'||typeControle==='xmlonly');
        var valHtml='';
        var tooltipContent='';
        if(!isXmlOnly){var rv=r.rdi||'(vide)';tooltipContent='<strong>RDI:</strong> '+escHtml(r.rdi_field)+' = '+escHtml(rv);valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+escHtml(rv)+'</div>';}
        if(typeControle==='xml'||isXmlOnly){var xv=r.xml||'(vide)';if(tooltipContent)tooltipContent+='<br>';tooltipContent+='<strong>XML:</strong> '+escHtml(r.xml_tag_name)+' = '+escHtml(xv);valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+escHtml(xv)+'</div>';}
        if(r.regles_testees&&r.regles_testees.length>0){tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles :</strong><ul style="margin:2px 0 0;padding-left:16px">';r.regles_testees.forEach(function(rg){tooltipContent+='<li>'+escHtml(rg)+'</li>';});tooltipContent+='</ul>';}
        if(r.details_erreurs&&r.details_erreurs.length>0&&!(r.details_erreurs.length===1&&r.details_erreurs[0]==='RAS')){tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0;padding-left:16px;color:#fcc">';r.details_erreurs.forEach(function(e){tooltipContent+='<li>'+escHtml(e)+'</li>';});tooltipContent+='</ul>';}
        if(r.rule_details){Object.keys(r.rule_details).forEach(function(rn){var rd=r.rule_details[rn];if(!rd||!rd.length)return;tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+escHtml(rn)+' :</strong><ul style="margin:2px 0 0;padding-left:16px;font-family:monospace;font-size:0.85em">';rd.forEach(function(l){tooltipContent+='<li>'+escHtml(l)+'</li>';});tooltipContent+='</ul>';});}
        var sIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
        var btLbl=r.obligatoire==='Oui'?'<span class="bt-oblig">'+escHtml(r.balise)+'</span>':escHtml(r.balise);
        var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
        var errClass=(r.details_erreurs&&r.details_erreurs.length>0)?'col-erreurs':'col-erreurs-hidden';
        out+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
          '<td class="col-status">'+sIcon+'</td>'+
          '<td class="col-bt"><strong>'+btLbl+'</strong></td>'+
          '<td>'+escHtml(r.libelle)+'</td>'+
          '<td><ul>'; r.regles_testees.forEach(function(rg){out+='<li>'+escHtml(rg)+'</li>';}); out+='</ul></td>'+
          '<td class="col-valeurs">'+valHtml+'</td>'+
          '<td class="'+errClass+'"><ul>'; r.details_erreurs.forEach(function(e){out+='<li>'+escHtml(e)+'</li>';}); out+='</ul></td></tr>';
      });
      out+='</tbody></table>';
    }
    if(artChamps.length>0){
      var artGroups={};var artOrder=[];
      artChamps.forEach(function(r){var k=r.article_index;if(!artGroups[k]){artGroups[k]=[];artOrder.push(k);}artGroups[k].push(r);});
      out+='<div style="margin-top:8px;padding:4px 10px;font-size:12px;color:#aaa;border-top:1px solid #333">'+artOrder.length+' article(s) — cliquez pour déplier</div>';
      artOrder.forEach(function(aIdx){
        var ac=artGroups[aIdx];
        var aLid=ac[0].article_line_id||'?';
        var aName=ac[0].article_name||'';
        var aErr=ac.filter(function(r){return r.status==='ERREUR';}).length;
        var aOk=ac.filter(function(r){return r.status==='OK';}).length;
        var aHdrBg=aErr>0?'background:#5a1a1a':'background:#1a3a1a';
        var artContentId=idPfx+'art-'+aIdx;
        out+='<div class="article-block" style="margin:4px 0;border:1px solid #444;border-radius:6px;overflow:hidden">'+
          '<div class="article-header" data-art="'+artContentId+'" style="'+aHdrBg+';padding:8px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;color:#fff;font-size:13px">'+
          '<div><strong>📦 Ligne '+escHtml(aLid)+'</strong>'+(aName?' — '+escHtml(aName):'')+'</div>'+
          '<div>'+ac.length+' champs | ✅ '+aOk+' | ❌ '+aErr+'</div></div>'+
          '<div class="article-content" id="'+artContentId+'" style="display:none">'+
          '<table class="main-table"><thead><tr><th class="col-status"></th><th class="col-bt">BT</th><th class="col-libelle">Libellé</th><th class="col-regles">Règles</th><th class="col-valeurs">Valeurs</th><th class="col-erreurs">Erreurs</th></tr></thead><tbody>';
        ac.forEach(function(r){
          var isXmlOnly=(typeControle==='cii'||typeControle==='xmlonly');
          var valHtml='';var tooltipContent='';
          if(!isXmlOnly){var rv=r.rdi||'(vide)';tooltipContent='<strong>RDI:</strong> '+escHtml(r.rdi_field)+' = '+escHtml(rv);valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+escHtml(rv)+'</div>';}
          if(typeControle==='xml'||isXmlOnly){var xv=r.xml||'(vide)';if(tooltipContent)tooltipContent+='<br>';tooltipContent+='<strong>XML:</strong> '+escHtml(r.xml_tag_name)+' = '+escHtml(xv);valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+escHtml(xv)+'</div>';}
          if(r.regles_testees&&r.regles_testees.length>0){tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles :</strong><ul style="margin:2px 0 0;padding-left:16px">';r.regles_testees.forEach(function(rg){tooltipContent+='<li>'+escHtml(rg)+'</li>';});tooltipContent+='</ul>';}
          if(r.details_erreurs&&r.details_erreurs.length>0&&!(r.details_erreurs.length===1&&r.details_erreurs[0]==='RAS')){tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0;padding-left:16px;color:#fcc">';r.details_erreurs.forEach(function(e){tooltipContent+='<li>'+escHtml(e)+'</li>';});tooltipContent+='</ul>';}
          if(r.rule_details){Object.keys(r.rule_details).forEach(function(rn){var rd=r.rule_details[rn];if(!rd||!rd.length)return;tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+escHtml(rn)+' :</strong><ul style="margin:2px 0 0;padding-left:16px;font-family:monospace;font-size:0.85em">';rd.forEach(function(l){tooltipContent+='<li>'+escHtml(l)+'</li>';});tooltipContent+='</ul>';});}
          var sIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
          var btLbl=r.obligatoire==='Oui'?'<span class="bt-oblig">'+escHtml(r.balise)+'</span>':escHtml(r.balise);
          var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
          var errClass=(r.details_erreurs&&r.details_erreurs.length>0)?'col-erreurs':'col-erreurs-hidden';
          out+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
            '<td class="col-status">'+sIcon+'</td><td class="col-bt"><strong>'+btLbl+'</strong></td>'+
            '<td>'+escHtml(r.libelle)+'</td><td><ul>';
          r.regles_testees.forEach(function(rg){out+='<li>'+escHtml(rg)+'</li>';});
          out+='</ul></td><td class="col-valeurs">'+valHtml+'</td><td class="'+errClass+'"><ul>';
          r.details_erreurs.forEach(function(e){out+='<li>'+escHtml(e)+'</li>';});
          out+='</ul></td></tr>';
        });
        out+='</tbody></table></div></div>';
      });
    }
    out+='</div></div>';
  });
  return out;
}

function batchAttachDetailEvents(containerEl,i){
  containerEl.querySelectorAll('.category-header').forEach(function(hdr){
    hdr.addEventListener('click',function(){
      document.getElementById(this.getAttribute('data-cat')).classList.toggle('open');
    });
  });
  containerEl.querySelectorAll('.article-header').forEach(function(hdr){
    hdr.addEventListener('click',function(){
      var el=document.getElementById(this.getAttribute('data-art'));
      if(el)el.style.display=el.style.display==='none'?'block':'none';
    });
  });
  containerEl.querySelectorAll('.data-row').forEach(function(row){
    row.addEventListener('mouseenter',function(e){tooltip.innerHTML=this.getAttribute('data-tooltip');tooltip.style.display='block';positionTooltip(e);});
    row.addEventListener('mousemove',function(e){positionTooltip(e);});
    row.addEventListener('mouseleave',function(){tooltip.style.display='none';});
  });
}

function batchExportCsv(batch){
  var lines=['Facture,BT,Libellé,Statut,RDI,XML,Erreurs'];
  batch.forEach(function(inv){
    if(!inv.results)return;
    inv.results.forEach(function(r){
      var cols=[inv.name,r.balise,r.libelle,r.status,r.rdi||'',r.xml||'',(r.details_erreurs||[]).join(' | ')];
      lines.push(cols.map(function(c){return '"'+String(c).replace(/"/g,'""')+'"';}).join(','));
    });
  });
  var blob=new Blob(['\uFEFF'+lines.join('\n')],{type:'text/csv;charset=utf-8'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='facturix-batch-'+new Date().toISOString().slice(0,10)+'.csv';
  a.click();
}

function batchReset(){
  batchFilesMap={};
  batchTmpCounter=0;
  document.getElementById('batchResults').style.display='none';
  document.getElementById('batchFilesSection').style.display='none';
  document.getElementById('batchFilesBody').innerHTML='';
  batchUpdateLaunchBtn();
}

function escHtml(s){
  if(s==null)return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* Construit le bloc tooltip "Schematron officiel EN16931" pour une ligne du tableau.
   Source : r.schematron_errors = [{rule_id, severity, flag, message, location, bts}, ...] */
function buildSchematronTooltip(r){
  if(!r||!r.schematron_errors||r.schematron_errors.length===0)return '';
  var html='<hr style="margin:6px 0;border-color:#7c3aed">'+
    '<strong style="color:#c4b5fd">📜 Schematron officiel EN16931 (CII)</strong>'+
    '<div style="font-size:0.85em;color:#cbd5e1;margin:2px 0 4px">'+
      r.schematron_errors.length+' règle(s) du standard non respectée(s)</div>';
  r.schematron_errors.forEach(function(e){
    var sevColor=(e.flag==='fatal')?'#fca5a5':'#fde68a';
    var sevLabel=(e.flag==='fatal')?'fatale':(e.severity||e.flag||'warning');
    html+='<div style="margin:6px 0;padding:6px 8px;background:rgba(124,58,237,0.18);border-left:3px solid #a78bfa;border-radius:4px">'+
      '<div style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:3px">'+
        '<strong style="color:#ddd6fe;font-family:monospace">'+escHtml(e.rule_id||'')+'</strong>'+
        '<span style="color:'+sevColor+';font-size:0.78em;font-weight:700;text-transform:uppercase">'+escHtml(sevLabel)+'</span>'+
      '</div>'+
      '<div style="color:#fde2e2;font-size:0.86em;line-height:1.35">'+escHtml(e.message||'')+'</div>';
    if(e.bts&&e.bts.length>0){
      html+='<div style="margin-top:4px;font-size:0.78em;color:#a5b4fc"><strong>BT cités :</strong> '+
        e.bts.map(function(b){return escHtml(b);}).join(', ')+'</div>';
    }
    if(e.location){
      html+='<div style="margin-top:3px;font-size:0.72em;color:#94a3b8;font-family:monospace;word-break:break-all">'+
        '<strong style="color:#cbd5e1">XPath :</strong> '+escHtml(e.location)+'</div>';
    }
    html+='</div>';
  });
  return html;
}

/* ---- MAPPING MANAGEMENT FUNCTIONS ---- */
function updateDeleteButtonVisibility() {
    const paramSelect = document.getElementById('typeFormulaireParam');
    const btn = document.getElementById('btnDeleteCurrentMapping');
    if (!paramSelect || !btn) return;
    const opt = paramSelect.options[paramSelect.selectedIndex];
    btn.style.display = (opt && opt.dataset.isDefault === 'false') ? '' : 'none';
}

function deleteCurrentMapping() {
    const paramSelect = document.getElementById('typeFormulaireParam');
    const opt = paramSelect && paramSelect.options[paramSelect.selectedIndex];
    if (!opt || !opt.dataset.mappingId) return;
    openDeleteMappingModal(opt.dataset.mappingId);
}

function openCreateMappingModal() {
    document.getElementById('createMappingModal').style.display = 'block';
    document.getElementById('newMappingName').value = '';

    // Peupler la liste de tous les mappings existants
    const copySelect = document.getElementById('copyFromMapping');
    copySelect.innerHTML = '<option value="">Mapping vide</option>';

    if (mappingsIndex.mappings) {
        mappingsIndex.mappings.forEach(mapping => {
            const option = document.createElement('option');
            option.value = mapping.id;
            option.textContent = mapping.name;
            copySelect.appendChild(option);
        });
    }
}

function closeCreateMappingModal() {
    document.getElementById('createMappingModal').style.display = 'none';
}

function confirmCreateMapping() {
    const name = document.getElementById('newMappingName').value.trim();
    const copyFrom = document.getElementById('copyFromMapping').value;

    if (!name) {
        alert('Veuillez entrer un nom pour le mapping');
        return;
    }

    const payload = { name };
    if (copyFrom) {
        payload.copy_from = copyFrom;
    }
    
    fetch(BASE+'/api/mappings/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            const copyMsg = copyFrom ? ' (copié depuis un mapping existant)' : '';
            alert(`✓ Mapping "${name}" créé avec succès !${copyMsg}`);
            closeCreateMappingModal();
            updateAllMappingDropdowns();
        } else {
            alert('Erreur: ' + (data.error || 'Création impossible'));
        }
    })
    .catch(err => {
        console.error('Erreur:', err);
        alert('Erreur lors de la création du mapping');
    });
}

function openDeleteMappingModal(mappingId) {
    const mapping = mappingsIndex.mappings.find(m => m.id === mappingId);
    if (!mapping) return;
    
    mappingToDelete = mapping;
    document.getElementById('deleteMappingName').textContent = mapping.name;
    document.getElementById('deleteMappingModal').style.display = 'block';
}

function closeDeleteMappingModal() {
    document.getElementById('deleteMappingModal').style.display = 'none';
    mappingToDelete = null;
}

function confirmDeleteMapping() {
    if (!mappingToDelete) return;
    
    fetch(BASE+'/api/mappings/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: mappingToDelete.id })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            alert(`✓ Mapping "${mappingToDelete.name}" supprimé avec succès`);
            closeDeleteMappingModal();
            updateAllMappingDropdowns();
        } else {
            alert('Erreur: ' + (data.error || 'Suppression impossible'));
        }
    })
    .catch(err => {
        console.error('Erreur:', err);
        alert('Erreur lors de la suppression du mapping');
    });
}

// Fonction pour mettre à jour tous les dropdowns de mapping
function updateAllMappingDropdowns() {
    fetch(BASE+'/api/mappings/index')
        .then(r => r.json())
        .then(data => {
            mappingsIndex = data;
            const allMappings = data.mappings || [];

            const controleSelect = document.getElementById('typeFormulaire');
            if (controleSelect) updateSingleDropdown(controleSelect, allMappings);

            const batchSelect = document.getElementById('batchTypeFormulaire');
            if (batchSelect) updateSingleDropdown(batchSelect, allMappings);

            const paramSelect = document.getElementById('typeFormulaireParam');
            if (paramSelect) {
                updateSingleDropdown(paramSelect, allMappings);
                updateDeleteButtonVisibility();
            }
        })
        .catch(err => console.error('Erreur mise à jour dropdowns:', err));
}

function updateSingleDropdown(selectElement, mappings) {
    const currentValue = selectElement.value;
    selectElement.innerHTML = '';
    
    // Ajouter toutes les options sans grouper
    mappings.forEach(mapping => {
        const option = document.createElement('option');

        // Dériver la value depuis l'id DB (source de vérité)
        let value;
        if (mapping.id === 'default_simple') value = 'simple';
        else if (mapping.id === 'default_groupee') value = 'groupee';
        else if (mapping.id === 'default_flux') value = 'flux';
        else value = 'custom_' + mapping.id;

        option.value = value;
        option.textContent = mapping.name;
        option.dataset.filename = mapping.filename;
        option.dataset.mappingId = mapping.id;
        option.dataset.isDefault = mapping.is_default ? 'true' : 'false';
        option.dataset.color = mapping.color || '';
        
        selectElement.appendChild(option);
    });
    
    // Restaurer la sélection
    if (currentValue) {
        const exists = Array.from(selectElement.options).some(o => o.value === currentValue);
        if (exists) {
            selectElement.value = currentValue;
        }
    }
}

// Charger les options au démarrage
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(() => updateAllMappingDropdowns(), 500);
});

// Close modals when clicking outside
// On traque le mousedown pour éviter les faux positifs (ex: glissement depuis l'intérieur)
var _modalMousedownTarget = null;
document.addEventListener('mousedown', function(e) { _modalMousedownTarget = e.target; });

window.onclick = function(event) {
    const createModal = document.getElementById('createMappingModal');
    const deleteModal = document.getElementById('deleteMappingModal');
    const editModal = document.getElementById('editModal');
    const ruleModal = document.getElementById('editRuleModal');
    const historyModal = document.getElementById('historyModal');
    const authorModal = document.getElementById('authorModal');
    // On ne ferme que si mousedown ET click sont tous deux sur le fond
    var t = event.target;
    var md = _modalMousedownTarget;
    if (t === createModal && md === createModal) { closeCreateMappingModal(); }
    if (t === deleteModal && md === deleteModal) { closeDeleteMappingModal(); }
    if (t === editModal   && md === editModal)   { editModal.style.display = 'none'; }
    if (t === ruleModal   && md === ruleModal)   { ruleModal.style.display = 'none'; }
    if (t === historyModal && md === historyModal) { historyModal.style.display = 'none'; }
    if (t === authorModal  && md === authorModal)  { authorModal.style.display = 'none'; pendingAuditCallback = null; }
}

// Echap → ferme le modal ouvert ; Entrée dans un input (hors textarea) → sauvegarde
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var historyModal = document.getElementById('historyModal');
        if (historyModal && historyModal.style.display !== 'none') { historyModal.style.display = 'none'; return; }
        var editModal = document.getElementById('editModal');
        if (editModal && editModal.style.display !== 'none') { editModal.style.display = 'none'; return; }
        return;
    }
    var editModal = document.getElementById('editModal');
    if (!editModal || editModal.style.display === 'none') return;
    if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'SELECT') {
        e.preventDefault();
        document.getElementById('btnSave').click();
    }
});

// Add event listener to create button
document.addEventListener('DOMContentLoaded', function() {
    const btnCreate = document.getElementById('btnCreateMapping');
    if (btnCreate) {
        btnCreate.addEventListener('click', openCreateMappingModal);
    }
});

/* ---- AIDE CONTEXTUELLE + MASQUAGE PDF ---- */
function updateHelp(){
var type=document.getElementById('typeControle').value;
var help=document.getElementById('helpControle');
var groupePdf=document.getElementById('groupePdf');
var groupeCii=document.getElementById('groupeCii');
var groupeRdi=document.getElementById('groupeRdi');
if(type==='rdi'){
help.innerHTML='<strong>Mode RDI</strong><ul><li>Présence obligatoire</li><li>Regles de gestion</li><li>Contrôles CEGEDIM</li></ul>';
groupePdf.style.display='none';
groupeCii.style.display='none';
groupeRdi.style.display='flex';
}else if(type==='cii'){
help.innerHTML='<strong>Mode CII - GCP</strong><ul><li>Controle du XML CII (Cross Industry Invoice) directement</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Contrôles CEGEDIM</li></ul>';
groupePdf.style.display='none';
groupeCii.style.display='flex';
groupeRdi.style.display='none';
}else if(type==='xmlonly'){
help.innerHTML='<strong>Mode XML - Vérif facture uniquement</strong><ul><li>Controle du XML encapsule dans le PDF</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Regles metiers</li></ul>';
groupePdf.style.display='flex';
groupeCii.style.display='none';
groupeRdi.style.display='none';
}else{
help.innerHTML='<strong>Mode RDI vs XML</strong><ul><li>Comparaison sortie SAP vs sortie Exstream</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Contrôles CEGEDIM</li><li>Comparaison RDI vs XML</li></ul>';
groupePdf.style.display='flex';
groupeCii.style.display='none';
groupeRdi.style.display='flex';
}
}
document.getElementById('typeControle').addEventListener('change',updateHelp);
updateHelp();

/* ---- AFFICHER/MASQUER BOUTONS PDF ---- */
document.getElementById('pdfFile').addEventListener('change',function(){
var file=this.files[0];
var isPdf=(file && file.name.toLowerCase().endsWith('.pdf'));
document.getElementById('btnDownloadXml').style.display=isPdf?'inline-block':'none';
document.getElementById('btnRemoveSignature').style.display=isPdf?'inline-block':'none';
});
document.getElementById('btnDownloadXml').addEventListener('click',async function(){
var pdf=document.getElementById('pdfFile').files[0];
if(!pdf){alert('Selectionnez un fichier PDF');return}
var fd=new FormData();
fd.append('pdf',pdf);
try{
var resp=await fetch(BASE+'/api/extract-xml',{method:'POST',body:fd});
if(!resp.ok){var err=await resp.json();alert('Erreur: '+(err.error||'Extraction impossible'));return}
var blob=await resp.blob();
var url=URL.createObjectURL(blob);
var a=document.createElement('a');
a.href=url;
a.download=pdf.name.replace(/\.pdf$/i,'.xml');
document.body.appendChild(a);
a.click();
a.remove();
URL.revokeObjectURL(url);
}catch(e){alert('Erreur: '+e.message)}
});

/* ---- SUPPRIMER SIGNATURE PDF ---- */
document.getElementById('btnRemoveSignature').addEventListener('click',async function(){
var pdf=document.getElementById('pdfFile').files[0];
if(!pdf){alert('Selectionnez un fichier PDF');return}
var fd=new FormData();
fd.append('pdf',pdf);
try{
this.disabled=true;this.textContent='⏳ En cours...';
var resp=await fetch(BASE+'/api/remove-signature',{method:'POST',body:fd});
if(!resp.ok){var err=await resp.json();alert('Erreur: '+(err.error||'Impossible de traiter ce PDF'));return}
var blob=await resp.blob();
var url=URL.createObjectURL(blob);
var a=document.createElement('a');
a.href=url;
a.download=pdf.name.replace(/\.pdf$/i,'_unsigned.pdf');
document.body.appendChild(a);
a.click();
a.remove();
URL.revokeObjectURL(url);
}catch(e){alert('Erreur: '+e.message)
}finally{this.disabled=false;this.innerHTML='<span>✂️</span> Supprimer signature';}
});

/* ---- LANCER CONTROLE ---- */
document.getElementById('btnControle').addEventListener('click',async function(){
var typeControle=document.getElementById('typeControle').value;
var pdf=document.getElementById('pdfFile').files[0];
var rdi=document.getElementById('rdiFile').files[0];
var cii=document.getElementById('ciiFile').files[0];
if(typeControle==='xml'&&!pdf){alert('Selectionnez le fichier PDF ou XML');return}
if(typeControle==='xmlonly'&&!pdf){alert('Selectionnez le fichier PDF');return}
if(typeControle==='cii'&&!cii){alert('Selectionnez le fichier XML CII');return}
if(typeControle!=='cii'&&typeControle!=='xmlonly'&&!rdi){alert('Selectionnez le fichier RDI');return}
document.getElementById('loading').style.display='block';
document.getElementById('results').style.display='none';
var fd=new FormData();
if(pdf)fd.append('pdf',pdf);
if(cii)fd.append('cii',cii);
if(rdi)fd.append('rdi',rdi);
fd.append('type_formulaire',document.getElementById('typeFormulaire').value);
fd.append('type_controle',typeControle);
try{
var resp=await fetch(BASE+'/controle',{method:'POST',body:fd});
var data=await resp.json();
if(data.error){alert('Erreur: '+data.error);return}
document.getElementById('statTotal').textContent=data.stats.total;
document.getElementById('statOk').textContent=data.stats.ok;
document.getElementById('statErreur').textContent=data.stats.erreur;
document.getElementById('statIgnore').textContent=data.stats.ignore||0;
document.getElementById('statAmbigu').textContent=data.stats.ambigu||0;
var artInfo=document.getElementById('statArticles');
if(artInfo){artInfo.textContent=data.stats.nb_articles>0?data.stats.nb_articles:'—';}
var pct=data.stats.total>0?Math.round(data.stats.ok/data.stats.total*100):0;
var fill=document.getElementById('progressFill');
document.getElementById('progressPct').textContent=pct+'%';
fill.style.width=pct+'%';
fill.className='progress-fill';
var gSrc,gMsg;
if(pct<25){gSrc=BASE+'/img/0-25.jpg';fill.classList.add('pct-0');}
else if(pct<50){gSrc=BASE+'/img/25-50.jpg';fill.classList.add('pct-25');}
else if(pct<75){gSrc=BASE+'/img/50-75.jpg';fill.classList.add('pct-50');}
else{gSrc=BASE+'/img/75-100.jpg';fill.classList.add('pct-75');}
document.getElementById('gauloisImg').src=gSrc;
// Survol de la barre : afficher overlay
var track=document.querySelector('.progress-track');
var overlay=document.getElementById('gauloisOverlay');
track.onmousemove=function(e){
  overlay.classList.add('visible');
  var x=e.clientX,y=e.clientY;
  var ow=430,oh=430;
  var left=x+20; if(left+ow>window.innerWidth-10) left=x-ow-20;
  var top=y-oh/2; if(top<10) top=10; if(top+oh>window.innerHeight-10) top=window.innerHeight-oh-10;
  overlay.style.left=left+'px';
  overlay.style.top=top+'px';
};
track.onmouseleave=function(){overlay.classList.remove('visible');};
var cont=document.getElementById('categoriesContainer');
cont.innerHTML='';
// Bandeau de synthèse Schematron officiel EN16931 (CII)
if(data.schematron){
var sch=data.schematron;
var panel=document.createElement('div');
panel.className='schematron-panel';
var headerCls,headerTxt;
var synthSuffix=sch.synthetic?' — XML reconstruit depuis le RDI':'';
if(sch.skipped){headerCls='warn';headerTxt='ℹ️ Schematron EN16931 (CII) — non exécuté';}
else if(sch.error){headerCls='warn';headerTxt='⚠️ Schematron EN16931 — erreur de validation';}
else if(sch.fatal>0){headerCls='err';headerTxt='❌ Schematron EN16931 (CII) — '+sch.fatal+' erreur'+(sch.fatal>1?'s':'')+synthSuffix;}
else if(sch.total>0){headerCls='warn';headerTxt='⚠️ Schematron EN16931 (CII) — '+sch.total+' avertissement'+(sch.total>1?'s':'')+synthSuffix;}
else{headerCls='ok';headerTxt='✅ Schematron EN16931 (CII) — conforme'+synthSuffix;}
var badges='';
if(!sch.error&&!sch.skipped){
  badges='<span class="badge">'+(sch.total||0)+' total</span>'+
         '<span class="badge">'+(sch.fatal||0)+' fatales</span>'+
         '<span class="badge">'+(sch.warning||0)+' warnings</span>'+
         '<span class="badge">'+(sch.matched||0)+' attachées</span>'+
         '<span class="badge">'+((sch.orphans||[]).length)+' orphelines</span>';
  if(sch.skipped_out_of_scope&&sch.skipped_out_of_scope>0){
    badges+='<span class="badge" title="Erreurs schematron dont aucun BT cité n\'est dans ce mapping — masquées">'+
      sch.skipped_out_of_scope+' hors mapping</span>';
  }
}
var hHtml='<div class="schematron-header '+headerCls+'" id="schematronHeader">'+
  '<div>'+headerTxt+'</div><div class="badges">'+badges+'</div></div>';
var bHtml='<div class="schematron-body" id="schematronBody"><div class="intro">Validation contre le schematron officiel <code>EN16931-CII v1.3.16</code> de ConnectingEurope. Les erreurs liées à un BT du mapping sont aussi affichées dans le tableau ci-dessous, à côté du champ concerné.</div>';
if(sch.synthetic&&sch.note){bHtml+='<div class="intro" style="background:#fef3c7;border-left:3px solid #f59e0b;padding:6px 10px;border-radius:4px;color:#78350f;margin-bottom:8px"><strong>ℹ️ XML synthétique :</strong> '+escHtml(sch.note)+'</div>';}
if(sch.skipped){
  bHtml+='<div class="empty" style="color:#b45309">'+escHtml(sch.reason||'Schematron non exécuté.')+'</div>';
}else if(sch.error){
  bHtml+='<div class="empty" style="color:#b45309">Validation impossible : '+escHtml(sch.error)+'</div>';
}else if((sch.errors||[]).length===0){
  bHtml+='<div class="empty">Aucun écart détecté ✨</div>';
}else{
  bHtml+='<table><thead><tr><th>Règle</th><th>Sévérité</th><th>BT concernés</th><th>Message</th><th>XPath</th></tr></thead><tbody>';
  (sch.errors||[]).forEach(function(e){
    var bts=(e.bts||[]).map(function(b){return '<span>'+escHtml(b)+'</span>';}).join('');
    var loc=e.location||'';
    var locCell=loc
      ? '<button type="button" class="copy-xpath" data-xpath="'+escHtml(loc)+'" title="'+escHtml(loc)+'">📋 Copier</button>'
      : '<span style="color:#94a3b8">—</span>';
    bHtml+='<tr>'+
      '<td class="rule">'+escHtml(e.rule_id||'')+'</td>'+
      '<td class="flag '+(e.flag||'')+'">'+escHtml(e.severity||e.flag||'')+'</td>'+
      '<td class="bts">'+(bts||'<span style="color:#94a3b8">—</span>')+'</td>'+
      '<td>'+escHtml(e.message||'')+'</td>'+
      '<td class="location">'+locCell+'</td>'+
    '</tr>';
  });
  bHtml+='</tbody></table>';
  if((sch.orphans||[]).length>0){
    bHtml+='<div class="intro" style="margin-top:12px;color:#b45309"><strong>'+sch.orphans.length+' erreur(s) orpheline(s)</strong> : règles dont le BT cible n\'est pas mappé dans ce formulaire — elles ne sont visibles que dans ce panneau.</div>';
  }
}
bHtml+='</div>';
panel.innerHTML=hHtml+bHtml;
cont.appendChild(panel);
panel.querySelector('#schematronHeader').addEventListener('click',function(){
  panel.querySelector('#schematronBody').classList.toggle('open');
});
// Boutons "📋 Copier" pour chaque XPath
panel.querySelectorAll('button.copy-xpath').forEach(function(btn){
  btn.addEventListener('click',function(ev){
    ev.stopPropagation();
    var xp=this.getAttribute('data-xpath')||'';
    var done=this;
    var ok=function(){
      done.classList.add('copied');
      var prev=done.textContent;
      done.textContent='✓ Copié';
      setTimeout(function(){done.classList.remove('copied');done.textContent=prev;},1500);
    };
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(xp).then(ok).catch(function(){
        // Fallback en cas de blocage clipboard
        var ta=document.createElement('textarea');ta.value=xp;document.body.appendChild(ta);
        ta.select();try{document.execCommand('copy');ok();}catch(e){}finally{ta.remove();}
      });
    }else{
      var ta=document.createElement('textarea');ta.value=xp;document.body.appendChild(ta);
      ta.select();try{document.execCommand('copy');ok();}catch(e){}finally{ta.remove();}
    }
  });
});
// Ouvre par défaut s'il y a des erreurs
if(!sch.error&&(sch.total||0)>0){panel.querySelector('#schematronBody').classList.add('open');}
}
// Trier les catégories dans l'ordre défini
var categoryOrder={'BG-INFOS-GENERALES':1,'BG-TOTAUX':2,'BG-TVA':3,'BG-LIGNES':4,'BG-VENDEUR':5,'BG-ACHETEUR':6};
var sortedCategories=Object.keys(data.categories_results).sort(function(a,b){
var orderA=categoryOrder[a]||999;
var orderB=categoryOrder[b]||999;
return orderA-orderB;
});
for(var i=0;i<sortedCategories.length;i++){
var bgId=sortedCategories[i];
var cat=data.categories_results[bgId];
if(cat.champs.length===0)continue;
var div=document.createElement('div');
div.className='category';
var errCount=cat.stats.erreur||0;
var headerBg=errCount>0?'background:#7b1e1e':(cat.stats.ok===cat.stats.total&&cat.stats.total>0?'background:#2e7d32':'background:#366092');
var html='<div class="category-header" data-cat="'+bgId+'" style="'+headerBg+'">'+
'<div>'+cat.titre+'</div>'+
'<div>'+cat.stats.total+' champs | OK: '+cat.stats.ok+' | Err: '+errCount+'</div></div>'+
'<div class="category-content" id="cat-'+bgId+'">';
// Séparer champs non-article et champs article
var hasArticles=cat.champs.some(function(r){return r.article_index!==undefined;});
var nonArticleChamps=cat.champs.filter(function(r){return r.article_index===undefined;});
var articleChamps=cat.champs.filter(function(r){return r.article_index!==undefined;});

// 1. Rendu des champs non-article dans un tableau classique
if(nonArticleChamps.length>0){
html+='<table class="main-table"><thead><tr>'+
'<th class="col-status"></th>'+
'<th class="col-bt">BT</th>'+
'<th class="col-libelle">Libelle</th>'+
'<th class="col-regles">Regles testees</th>'+
'<th class="col-valeurs">Valeurs</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
nonArticleChamps.forEach(function(r){
var isXmlOnly=(data.type_controle==='cii'||data.type_controle==='xmlonly');
var tooltipContent='';
var valHtml='';
if(!isXmlOnly){
var rdiVal=r.rdi||'(vide)';
tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+rdiVal;
valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+rdiVal+'</div>';
}
if(data.type_controle==='xml'||isXmlOnly){
var xmlVal=r.xml||'(vide)';
if(tooltipContent)tooltipContent+='<br>';
tooltipContent+='<strong>XML:</strong> '+r.xml_tag_name+' = '+xmlVal;
valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+xmlVal+'</div>';
}
if(r.regles_testees&&r.regles_testees.length>0){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles appliquées :</strong><ul style="margin:2px 0 0 0;padding-left:16px">';
r.regles_testees.forEach(function(reg){tooltipContent+='<li>'+reg+'</li>';});
tooltipContent+='</ul>';
}
// Filtrer les details_erreurs pour ne pas dupliquer les schematron (ils ont leur propre section)
var nonSchDetails=(r.details_erreurs||[]).filter(function(e){return !/^\[BR-/.test(e);});
if(nonSchDetails.length>0&&!(nonSchDetails.length===1&&nonSchDetails[0]==='RAS')){
tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0 0;padding-left:16px;color:#fcc">';
nonSchDetails.forEach(function(err){tooltipContent+='<li>'+err+'</li>';});
tooltipContent+='</ul>';
}
tooltipContent+=buildSchematronTooltip(r);
if(r.rule_details){
Object.keys(r.rule_details).forEach(function(ruleName){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+ruleName+' :</strong><ul style="margin:2px 0 0 0;padding-left:16px;font-family:monospace;font-size:0.9em">';
r.rule_details[ruleName].forEach(function(line){tooltipContent+='<li>'+line+'</li>';});
tooltipContent+='</ul>';
});
}
var statusIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
var btLabel=r.obligatoire==='Oui'?'<span class="bt-oblig">'+r.balise+'</span>':r.balise;
var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
var hasErrors=r.details_erreurs&&r.details_erreurs.length>0;
var errClass=hasErrors?'col-erreurs':'col-erreurs-hidden';
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-bt"><strong>'+btLabel+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td class="col-valeurs">'+valHtml+'</td><td class="'+errClass+'"><ul>';
r.details_erreurs.forEach(function(err){html+='<li>'+err+'</li>'});
html+='</ul></td></tr>';
if(r.controles_cegedim&&r.controles_cegedim.length>0){
html+='<tr><td colspan="6" style="padding:0 12px 12px 40px;background:#faf8ff">'+
'<table class="ceg-table">'+
'<thead><tr><th>Ref</th><th>Categorie</th><th>Nature</th><th>Controle</th><th>Message</th></tr></thead><tbody>';
r.controles_cegedim.forEach(function(c){
html+='<tr><td>'+(c.ref||'')+'</td><td>'+(c.categorie||'')+'</td><td>'+(c.nature||'')+'</td><td>'+(c.description||c.controle||'')+'</td><td>'+(c.message||'')+'</td></tr>';
});
html+='</tbody></table></td></tr>';
}
});
html+='</tbody></table>';
}

// 2. Rendu des articles en blocs dépliables
if(articleChamps.length>0){
var articleGroups={};
var articleOrder=[];
articleChamps.forEach(function(r){
var key=r.article_index;
if(!articleGroups[key]){articleGroups[key]=[];articleOrder.push(key);}
articleGroups[key].push(r);
});
html+='<div style="margin-top:8px;padding:4px 10px;font-size:12px;color:#aaa;border-top:1px solid #333">'+articleOrder.length+' article(s) détecté(s) — cliquez pour déplier</div>';
articleOrder.forEach(function(artIdx){
var artChamps=articleGroups[artIdx];
var artLineId=artChamps[0].article_line_id||'?';
var artName=artChamps[0].article_name||'';
var artErrCount=artChamps.filter(function(r){return r.status==='ERREUR'}).length;
var artOkCount=artChamps.filter(function(r){return r.status==='OK'}).length;
var artHeaderBg=artErrCount>0?'background:#5a1a1a':'background:#1a3a1a';
html+='<div class="article-block" style="margin:4px 0;border:1px solid #444;border-radius:6px;overflow:hidden">'+
'<div class="article-header" data-art="art-'+artIdx+'" style="'+artHeaderBg+';padding:8px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;color:#fff;font-size:13px">'+
'<div><strong>📦 Ligne '+artLineId+'</strong>'+(artName?' — '+artName:'')+'</div>'+
'<div>'+artChamps.length+' champs | ✅ '+artOkCount+' | ❌ '+artErrCount+'</div></div>'+
'<div class="article-content" id="art-'+artIdx+'" style="display:none">';
html+='<table class="main-table"><thead><tr>'+
'<th class="col-status"></th>'+
'<th class="col-bt">BT</th>'+
'<th class="col-libelle">Libelle</th>'+
'<th class="col-regles">Regles testees</th>'+
'<th class="col-valeurs">Valeurs</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
artChamps.forEach(function(r){
var isXmlOnly=(data.type_controle==='cii'||data.type_controle==='xmlonly');
var tooltipContent='';
var valHtml='';
if(!isXmlOnly){
var rdiVal=r.rdi||'(vide)';
tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+rdiVal;
valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+rdiVal+'</div>';
}
if(data.type_controle==='xml'||isXmlOnly){
var xmlVal=r.xml||'(vide)';
if(tooltipContent)tooltipContent+='<br>';
tooltipContent+='<strong>XML:</strong> '+r.xml_tag_name+' = '+xmlVal;
valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+xmlVal+'</div>';
}
if(r.regles_testees&&r.regles_testees.length>0){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles appliquées :</strong><ul style="margin:2px 0 0 0;padding-left:16px">';
r.regles_testees.forEach(function(reg){tooltipContent+='<li>'+reg+'</li>';});
tooltipContent+='</ul>';
}
var nonSchDetailsArt=(r.details_erreurs||[]).filter(function(e){return !/^\[BR-/.test(e);});
if(nonSchDetailsArt.length>0&&!(nonSchDetailsArt.length===1&&nonSchDetailsArt[0]==='RAS')){
tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0 0;padding-left:16px;color:#fcc">';
nonSchDetailsArt.forEach(function(err){tooltipContent+='<li>'+err+'</li>';});
tooltipContent+='</ul>';
}
tooltipContent+=buildSchematronTooltip(r);
if(r.rule_details){
Object.keys(r.rule_details).forEach(function(ruleName){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+ruleName+' :</strong><ul style="margin:2px 0 0 0;padding-left:16px;font-family:monospace;font-size:0.9em">';
r.rule_details[ruleName].forEach(function(line){tooltipContent+='<li>'+line+'</li>';});
tooltipContent+='</ul>';
});
}
var statusIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
var btLabel=r.obligatoire==='Oui'?'<span class="bt-oblig">'+r.balise+'</span>':r.balise;
var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
var hasErrors=r.details_erreurs&&r.details_erreurs.length>0;
var errClass=hasErrors?'col-erreurs':'col-erreurs-hidden';
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-bt"><strong>'+btLabel+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td class="col-valeurs">'+valHtml+'</td><td class="'+errClass+'"><ul>';
r.details_erreurs.forEach(function(err){html+='<li>'+err+'</li>'});
html+='</ul></td></tr>';
});
html+='</tbody></table></div></div>';
});
}
html+='</div>';
div.innerHTML=html;
div.querySelector('.category-header').addEventListener('click',function(){
document.getElementById('cat-'+this.getAttribute('data-cat')).classList.toggle('open');
});
// Event listeners pour les headers d'articles
div.querySelectorAll('.article-header').forEach(function(hdr){
hdr.addEventListener('click',function(){
var contentId=this.getAttribute('data-art');
var content=document.getElementById(contentId);
if(content){content.style.display=content.style.display==='none'?'block':'none';}
});
});
div.querySelectorAll('.data-row').forEach(function(row){
row.addEventListener('mouseenter',function(e){
tooltip.innerHTML=this.getAttribute('data-tooltip');
tooltip.style.display='block';
positionTooltip(e);
});
row.addEventListener('mousemove',function(e){
positionTooltip(e);
});
row.addEventListener('mouseleave',function(){tooltip.style.display='none'});
});
cont.appendChild(div);
}
document.getElementById('results').style.display='block';

// Filtrage par BT et par erreurs
var searchInput=document.getElementById('searchBT');
var clearBtn=document.getElementById('btnClearSearch');
var searchContentInput=document.getElementById('searchContent');
var clearContentBtn=document.getElementById('btnClearContent');
var filterErrorsCheckbox=document.getElementById('filterErrors');
var filterAmbigusCheckbox=document.getElementById('filterAmbigus');

function applyAllFilters(){
var searchTerm=searchInput.value.toLowerCase().trim();
var contentTerm=searchContentInput.value.toLowerCase().trim();
var showErrorsOnly=filterErrorsCheckbox.checked;
var showAmbigusOnly=filterAmbigusCheckbox.checked;
clearBtn.style.display=searchTerm?'inline-block':'none';
clearContentBtn.style.display=contentTerm?'inline-block':'none';
filterResults(searchTerm,contentTerm,showErrorsOnly,showAmbigusOnly);
}

searchInput.removeEventListener('input',applyAllFilters);
searchInput.addEventListener('input',applyAllFilters);
searchContentInput.removeEventListener('input',applyAllFilters);
searchContentInput.addEventListener('input',applyAllFilters);
filterErrorsCheckbox.removeEventListener('change',applyAllFilters);
filterErrorsCheckbox.addEventListener('change',applyAllFilters);
filterAmbigusCheckbox.removeEventListener('change',applyAllFilters);
filterAmbigusCheckbox.addEventListener('change',applyAllFilters);
clearBtn.onclick=function(){
searchInput.value='';
clearBtn.style.display='none';
applyAllFilters();
};
clearContentBtn.onclick=function(){
searchContentInput.value='';
clearContentBtn.style.display='none';
applyAllFilters();
};

// Tout déplier / Tout replier
document.getElementById('btnExpandAll').addEventListener('click',function(){
document.querySelectorAll('.category-content').forEach(function(c){c.classList.add('open');});
document.querySelectorAll('.article-content').forEach(function(c){c.style.display='block';});
});
document.getElementById('btnCollapseAll').addEventListener('click',function(){
document.querySelectorAll('.category-content').forEach(function(c){c.classList.remove('open');});
document.querySelectorAll('.article-content').forEach(function(c){c.style.display='none';});
});

// Afficher/masquer les contrôles CEGEDIM
var cegedimCheckbox=document.getElementById('showCegedim');
function toggleCegedim(){
var show=cegedimCheckbox.checked;
document.querySelectorAll('.ceg-table').forEach(function(t){
t.closest('tr').style.display=show?'':'none';
});
}
cegedimCheckbox.addEventListener('change',toggleCegedim);
toggleCegedim();
applyAllFilters();

function filterResults(term,contentTerm,errorsOnly,ambigusOnly){
var categories=document.querySelectorAll('.category');
var visibleCount=0;
var hasActiveFilter=!!(term||contentTerm||errorsOnly||ambigusOnly);
categories.forEach(function(cat){
var hasMatch=false;
// Filtrer les lignes standard (hors articles)
var rows=cat.querySelectorAll('.main-table > tbody > .data-row, table.main-table > tbody > .data-row');
rows.forEach(function(row){
var btStrong=row.querySelector('td:nth-child(2) strong');
if(!btStrong) return;
var btText=btStrong.textContent.toLowerCase();
var valCell=row.querySelector('.col-valeurs');
var valText=valCell?valCell.textContent.toLowerCase():'';
var statusIcon=row.querySelector('.col-status').textContent.trim();
var isError=(statusIcon==='❌');
var isAmbigu=(statusIcon==='⚠️');
var nextRow=row.nextElementSibling;
var isCegedimRow=nextRow && nextRow.querySelector('.ceg-table');
var matchesSearch=!term||btText.includes(term);
var matchesContent=!contentTerm||valText.includes(contentTerm);
var matchesErrorFilter=!errorsOnly||isError;
var matchesAmbigusFilter=!ambigusOnly||isAmbigu;
if(matchesSearch&&matchesContent&&matchesErrorFilter&&matchesAmbigusFilter){
row.style.display='';
if(isCegedimRow){nextRow.style.display=cegedimCheckbox.checked?'':'none';}
hasMatch=true;
}else{
row.style.display='none';
if(isCegedimRow){nextRow.style.display='none';}
}
});
// Filtrer les blocs articles
var artBlocks=cat.querySelectorAll('.article-block');
artBlocks.forEach(function(block){
var artHasMatch=false;
var artRows=block.querySelectorAll('.data-row');
artRows.forEach(function(row){
var btStrong=row.querySelector('td:nth-child(2) strong');
if(!btStrong) return;
var btText=btStrong.textContent.toLowerCase();
var valCell=row.querySelector('.col-valeurs');
var valText=valCell?valCell.textContent.toLowerCase():'';
var statusIcon=row.querySelector('.col-status').textContent.trim();
var isError=(statusIcon==='❌');
var isAmbigu=(statusIcon==='⚠️');
var matchesSearch=!term||btText.includes(term);
var matchesContent=!contentTerm||valText.includes(contentTerm);
var matchesErrorFilter=!errorsOnly||isError;
var matchesAmbigusFilter=!ambigusOnly||isAmbigu;
if(matchesSearch&&matchesContent&&matchesErrorFilter&&matchesAmbigusFilter){
row.style.display='';
artHasMatch=true;
}else{
row.style.display='none';
}
});
if(artHasMatch){
block.style.display='';
hasMatch=true;
}else{
block.style.display=hasActiveFilter?'none':'';
}
});
if(hasMatch){
cat.classList.remove('hidden');
var catContent=cat.querySelector('.category-content');
if(hasActiveFilter&&catContent){catContent.classList.add('open');}
visibleCount++;
}else{
cat.classList.add('hidden');
}
});
}

}catch(e){
console.error(e);
alert('Erreur: '+e.message);
}finally{
document.getElementById('loading').style.display='none';
}
});

/* ---- PARAMETRAGE ---- */
function getCurrentMappingColor(){
var sel=document.getElementById('typeFormulaireParam');
var opt=sel&&sel.options[sel.selectedIndex];
return (opt&&opt.dataset.color)||'';
}
function applyMappingColor(){
var color=getCurrentMappingColor();
var swatch=document.getElementById('mappingColorSwatch');
var picker=document.getElementById('mappingColorPicker');
if(color){swatch.style.background=color;picker.value=color;}
else{swatch.style.background='#667eea';picker.value='#667eea';}
}
async function loadMappings(){
var type=document.getElementById('typeFormulaireParam').value;
var resp=await fetch(BASE+'/api/mapping/'+type);
currentMapping=await resp.json();
applyMappingColor();
var list=document.getElementById('mappingList');
list.innerHTML='';
if(!currentMapping||!currentMapping.champs||!currentMapping.champs.length){
list.innerHTML='<p style="color:#94a3b8;font-size:0.85em;padding:12px">Aucun champ dans ce mapping.</p>';
return;
}

// 1. Grouper par categorie_bg
var groups={};
var groupOrder=[];
currentMapping.champs.forEach(function(champ,index){
var bg=champ.categorie_bg||'BG-OTHER';
var rawTitre=champ.categorie_titre||bg;
var titre=rawTitre.replace(/[^\w\s\-'éèêëàâùûîïôçÉÈÊËÀÂÙÛÎÏÔÇ]/g,'').trim()||bg;
if(!groups[bg]){groups[bg]={titre:titre,champs:[],hasArticle:false};groupOrder.push(bg);}
groups[bg].champs.push({champ:champ,index:index});
if(champ.is_article) groups[bg].hasArticle=true;
});

// 2. Barre de filtres pills
var filterBar=document.createElement('div');
filterBar.className='cat-filter-bar';
var allPill=document.createElement('span');
allPill.className='cat-pill active';
allPill.dataset.bg='ALL';
allPill.textContent='Tout ('+currentMapping.champs.length+')';
filterBar.appendChild(allPill);
groupOrder.forEach(function(bg){
var g=groups[bg];
var pill=document.createElement('span');
pill.className='cat-pill'+(g.hasArticle?' art':'');
pill.dataset.bg=bg;
pill.textContent=g.titre+' ('+g.champs.length+')';
filterBar.appendChild(pill);
});
var collapseBtn=document.createElement('button');
collapseBtn.textContent='Tout replier';
collapseBtn.style.cssText='margin-left:auto;padding:3px 11px;border:1px solid #e2e8f0;border-radius:20px;font-size:0.73em;cursor:pointer;background:#f8fafc;color:#475569;font-weight:600;white-space:nowrap';
filterBar.appendChild(collapseBtn);
list.appendChild(filterBar);

var allCollapsed=false;
collapseBtn.addEventListener('click',function(){
allCollapsed=!allCollapsed;
collapseBtn.textContent=allCollapsed?'Tout déplier':'Tout replier';
document.querySelectorAll('.cat-group-body').forEach(function(b){b.style.display=allCollapsed?'none':'';});
document.querySelectorAll('.cat-group-hdr').forEach(function(h){
if(allCollapsed)h.classList.remove('open');else h.classList.add('open');
});
});

// 3. Rendu des groupes
groupOrder.forEach(function(bg){
var g=groups[bg];
var isArt=g.hasArticle;
var nbValide=g.champs.filter(function(e){return e.champ.valide===true;}).length;

var groupDiv=document.createElement('div');
groupDiv.className='cat-group';
groupDiv.dataset.bg=bg;

var hdr=document.createElement('div');
hdr.className='cat-group-hdr open'+(isArt?' art':'');
var ratioHtml=nbValide>0?'<span class="cat-group-ok-ratio">✓ '+nbValide+'/'+g.champs.length+'</span>':'';
hdr.innerHTML=
'<span class="cat-group-arrow">▶</span>'+
'<span class="cat-group-name">'+(isArt?'▤ ':'')+g.titre+'</span>'+
ratioHtml+
'<span class="cat-group-count">'+g.champs.length+' BT</span>';

var body=document.createElement('div');
body.className='cat-group-body';
var ul=document.createElement('ul');
ul.className='mapping-list';

g.champs.forEach(function(entry){
var champ=entry.champ;
var index=entry.index;
var li=document.createElement('li');
var isValide=champ.valide===true;
var isIgnored=champ.ignore==='Oui';
var isArticle=!!champ.is_article;
li.className='mapping-item'+(isValide?' valide':'')+(isArticle?' article':'')+(isIgnored?' ignored':'');
li.draggable=true;
li.setAttribute('data-index',index);
if(isIgnored)li.classList.add('has-ignored-tip');
li.innerHTML=
'<div class="mapping-item-info">'+
'<div class="item-main"><strong>'+champ.balise+'</strong> — '+champ.libelle+'</div>'+
'<div class="item-sub">RDI: <code>'+champ.rdi+'</code> | Oblig.: '+champ.obligatoire+' | Ignoré : '+(isIgnored?'Oui':'Non')+'</div>'+
'<div class="item-xpath">XPath: '+(champ.xpath||'—')+'</div>'+
'</div>'+
'<div class="mapping-actions">'+
'<label class="valide-toggle"><input type="checkbox" class="chk-valide" data-index="'+index+'"'+(isValide?' checked':'')+'> Valide</label>'+
'<button class="btn-edit" data-index="'+index+'">Editer</button>'+
'<button class="btn-delete" data-index="'+index+'">Supprimer</button>'+
'</div>';
// Drag & drop
li.addEventListener('dragstart',function(e){this.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/html',this.innerHTML);});
li.addEventListener('dragend',function(){this.classList.remove('dragging');document.querySelectorAll('.mapping-item').forEach(function(it){it.classList.remove('drag-over');});});
li.addEventListener('dragover',function(e){e.preventDefault();var d=document.querySelector('.dragging');if(d&&d!==this)this.classList.add('drag-over');});
li.addEventListener('dragleave',function(){this.classList.remove('drag-over');});
li.addEventListener('drop',async function(e){
e.preventDefault();this.classList.remove('drag-over');
var d=document.querySelector('.dragging');
if(d&&d!==this){
var fi=parseInt(d.getAttribute('data-index'));
var ti=parseInt(this.getAttribute('data-index'));
var it=currentMapping.champs.splice(fi,1)[0];
currentMapping.champs.splice(ti,0,it);
await saveMapping();loadMappings();
}
});
ul.appendChild(li);
});

body.appendChild(ul);
groupDiv.appendChild(hdr);
groupDiv.appendChild(body);
list.appendChild(groupDiv);

hdr.addEventListener('click',function(){
var open=hdr.classList.contains('open');
hdr.classList.toggle('open');
body.style.display=open?'none':'';
});
});

// 4. Délégation d'événements chk-valide / btn-edit / btn-delete
list.addEventListener('change',async function(e){
var chk=e.target.closest('.chk-valide');
if(chk){
var idx=parseInt(chk.getAttribute('data-index'));
currentMapping.champs[idx].valide=chk.checked;
await saveMapping();loadMappings();
}
});
list.addEventListener('click',function(e){
var eb=e.target.closest('.btn-edit');
if(eb){editMapping(eb.getAttribute('data-index'));return;}
var db=e.target.closest('.btn-delete');
if(db){deleteMapping(db.getAttribute('data-index'));}
});

// 5. Pills filter
filterBar.querySelectorAll('.cat-pill').forEach(function(pill){
pill.addEventListener('click',function(){
filterBar.querySelectorAll('.cat-pill').forEach(function(p){p.classList.remove('active');});
pill.classList.add('active');
var bg=pill.dataset.bg;
document.querySelectorAll('.cat-group').forEach(function(g){
g.style.display=(bg==='ALL'||g.dataset.bg===bg)?'':'none';
});
});
});

applySearchParamFilter();
}

function applySearchParamFilter(){
var query=document.getElementById('searchBTParam').value.toLowerCase().trim();
var groups=document.querySelectorAll('.cat-group');
var items=document.querySelectorAll('.mapping-item');
if(query){
// Expand all groups
groups.forEach(function(g){
g.style.display='';
var hdr=g.querySelector('.cat-group-hdr');
var body=g.querySelector('.cat-group-body');
if(hdr)hdr.classList.add('open');
if(body)body.style.display='';
});
// Filtrer les items
items.forEach(function(item){
var mainEl=item.querySelector('.item-main');
var text=mainEl?mainEl.textContent.toLowerCase():'';
item.style.display=text.includes(query)?'flex':'none';
});
// Cacher les groupes vides
groups.forEach(function(g){
var hasVisible=false;
g.querySelectorAll('.mapping-item').forEach(function(it){if(it.style.display!=='none')hasVisible=true;});
g.style.display=hasVisible?'':'none';
});
}else{
items.forEach(function(it){it.style.display='flex';});
var activePill=document.querySelector('.cat-pill.active');
if(activePill&&activePill.dataset.bg!=='ALL'){
groups.forEach(function(g){g.style.display=g.dataset.bg===activePill.dataset.bg?'':'none';});
}else{
groups.forEach(function(g){g.style.display='';});
}
}
}

function editMapping(index){
currentIndex=parseInt(index);
var champ=currentMapping.champs[currentIndex];
var selOpt=document.getElementById('typeFormulaireParam');
var mappingName=selOpt&&selOpt.options[selOpt.selectedIndex]?selOpt.options[selOpt.selectedIndex].textContent:'';
document.getElementById('modalTitle').textContent=mappingName||'Mapping';
document.getElementById('modalSubtitle').textContent='Mise à jour du champ BT';
// Appliquer la couleur du mapping sur le header
var color=getCurrentMappingColor();
var header=document.querySelector('.edit-field-header');
if(header){
if(color){header.style.background=color;}
else{header.style.background='linear-gradient(135deg,#667eea 0%,#764ba2 100%)';}
}
document.getElementById('editBalise').value=champ.balise;
document.getElementById('editLibelle').value=champ.libelle;
// Construire la valeur du select à partir de categorie_bg et categorie_titre
// Mapper les anciennes catégories vers les nouvelles si nécessaire
var categorieValue=(champ.categorie_bg||'BG-INFOS-GENERALES')+'|'+(champ.categorie_titre||'INFORMATIONS GÉNÉRALES DE LA FACTURE');
// Si la catégorie n'existe pas dans le select, utiliser la première option
var select=document.getElementById('editCategorie');
var exists=false;
for(var i=0;i<select.options.length;i++){
if(select.options[i].value===categorieValue){
exists=true;
break;
}
}
if(!exists){
// Par défaut, mapper vers la première catégorie
categorieValue='BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE';
}
document.getElementById('editCategorie').value=categorieValue;
document.getElementById('editRdi').value=champ.rdi;
document.getElementById('editTypeEnreg').value=champ.type_enregistrement||'';
document.getElementById('editXpath').value=(champ.xpath||'').replace(/^\/\//,'');
document.getElementById('editAttribute').value=champ.attribute||'';
document.getElementById('editObligatoire').value=champ.obligatoire;
document.getElementById('editIgnore').value=champ.ignore||'Non';
document.getElementById('editRdg').value=champ.rdg||'';
document.getElementById('btnCloneField').style.display='inline-flex';
document.getElementById('editModal').style.display='block';
}
async function deleteMapping(index){
if(!confirm('Supprimer ce champ?'))return;
var idx=parseInt(index);
var deletedChamp=Object.assign({},currentMapping.champs[idx]);
currentMapping.champs.splice(idx,1);
await saveMapping();
var type=document.getElementById('typeFormulaireParam').value;
askAuthorThen(async function(author){
await logAudit(type,author,'delete',deletedChamp.balise,deletedChamp,null);
});
loadMappings();
}
// IDs des mappings cibles pour un ajout multi-mapping
var addTargetMappingIds = [];
// Mode clone (depuis le bouton "Cloner vers…" dans editModal)
var cloneMode = false;

document.getElementById('btnAdd').addEventListener('click',async function(){
// Charger la liste de tous les mappings disponibles
var resp = await fetch(BASE+'/api/mappings/index');
var data = await resp.json();
var allMappings = data.mappings || [];
var sel = document.getElementById('typeFormulaireParam');
var currentMappingId = (sel.options[sel.selectedIndex] && sel.options[sel.selectedIndex].dataset.mappingId) || sel.value;

// Remplir les checkboxes
var listEl = document.getElementById('selectMappingsList');
listEl.innerHTML = '';
allMappings.forEach(function(m){
var isCurrent = (m.id === currentMappingId);
var label = document.createElement('label');
label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f8f9fa;border-radius:6px;cursor:pointer;font-size:0.95em';
label.innerHTML = '<input type="checkbox" class="chk-target-mapping" value="'+m.id+'"'+(isCurrent?' checked':'')+' style="width:16px;height:16px"> '+
'<span><strong>'+m.name+'</strong>'+(isCurrent?' <em style="color:#888;font-size:0.85em">(actuel)</em>':'')+'</span>';
listEl.appendChild(label);
});

cloneMode = false;
document.querySelector('#selectMappingsModal h2').textContent='Ajouter le champ à quel(s) mapping(s) ?';
document.querySelector('#selectMappingsModal p').textContent='Sélectionnez les mappings dans lesquels ce nouveau champ sera ajouté. Le mapping actuel est présélectionné.';
document.getElementById('selectMappingsModal').style.display='block';
});

document.getElementById('selectMappingsClose').addEventListener('click',function(){
document.getElementById('selectMappingsModal').style.display='none';
cloneMode=false;
});
document.getElementById('selectMappingsCancel').addEventListener('click',function(){
document.getElementById('selectMappingsModal').style.display='none';
cloneMode=false;
});
document.getElementById('selectMappingsConfirm').addEventListener('click',async function(){
addTargetMappingIds = Array.from(document.querySelectorAll('.chk-target-mapping:checked')).map(function(c){return c.value;});
if(addTargetMappingIds.length===0){alert('Sélectionnez au moins un mapping.');return;}
document.getElementById('selectMappingsModal').style.display='none';
if(cloneMode){
cloneMode=false;
// Lire les valeurs actuelles du formulaire
var categorieValue=document.getElementById('editCategorie').value;
var categorieParts=categorieValue.split('|');
var categorieBg=categorieParts[0]||'BG-OTHER';
var categorieTitre=categorieParts[1]||'Autres';
var clonedChamp={
balise:document.getElementById('editBalise').value,
libelle:document.getElementById('editLibelle').value,
rdi:document.getElementById('editRdi').value,
type_enregistrement:document.getElementById('editTypeEnreg').value||undefined,
xpath:document.getElementById('editXpath').value,
attribute:document.getElementById('editAttribute').value||undefined,
is_article:(function(){return (categorieBg==='BG-LIGNES'||/ligne/i.test(categorieBg+' '+categorieTitre))?true:undefined;})(),
obligatoire:document.getElementById('editObligatoire').value,
ignore:document.getElementById('editIgnore').value,
rdg:document.getElementById('editRdg').value,
categorie_bg:categorieBg,
categorie_titre:categorieTitre,
controles_cegedim:[],
valide:false,
type:(currentIndex!==null&&currentMapping.champs[currentIndex]?currentMapping.champs[currentIndex].type:undefined)||undefined
};
var cloneTargets=addTargetMappingIds.slice();
addTargetMappingIds=[];
askAuthorThen(async function(author){
for(var i=0;i<cloneTargets.length;i++){
var tid=cloneTargets[i];
var r=await fetch(BASE+'/api/mapping/'+tid);
var targetMapping=await r.json();
targetMapping.champs.push(clonedChamp);
await fetch(BASE+'/api/mapping/'+tid,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(targetMapping)});
await logAudit(tid,author,'add',clonedChamp.balise,null,clonedChamp);
}
alert('✓ Champ "'+clonedChamp.balise+'" cloné vers '+cloneTargets.length+' mapping(s).');
});
return;
}
// Ouvrir le formulaire d'ajout
currentIndex=null;
var selOptAdd=document.getElementById('typeFormulaireParam');
var mappingNameAdd=selOptAdd&&selOptAdd.options[selOptAdd.selectedIndex]?selOptAdd.options[selOptAdd.selectedIndex].textContent:'';
document.getElementById('modalTitle').textContent=mappingNameAdd||'Mapping';
document.getElementById('modalSubtitle').textContent='Ajout d\'un nouveau champ BT'+(addTargetMappingIds.length>1?' ('+addTargetMappingIds.length+' mappings)':'');
document.getElementById('editBalise').value='';
document.getElementById('editLibelle').value='';
document.getElementById('editCategorie').value='BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE';
document.getElementById('editRdi').value='';
document.getElementById('editTypeEnreg').value='';
document.getElementById('editXpath').value='';
document.getElementById('editAttribute').value='';
document.getElementById('editObligatoire').value='Non';
document.getElementById('editIgnore').value='Non';
document.getElementById('editRdg').value='';
document.getElementById('btnCloneField').style.display='none';
// Appliquer la couleur du mapping sur le header pour l'ajout aussi
var colorAdd=getCurrentMappingColor();
var headerAdd=document.querySelector('.edit-field-header');
if(headerAdd){
if(colorAdd){headerAdd.style.background=colorAdd;}
else{headerAdd.style.background='linear-gradient(135deg,#667eea 0%,#764ba2 100%)';}
}
document.getElementById('editModal').style.display='block';
});
document.getElementById('modalClose').addEventListener('click',function(){
document.getElementById('editModal').style.display='none';
});
document.getElementById('editCancelBtn').addEventListener('click',function(){
document.getElementById('editModal').style.display='none';
});
document.getElementById('btnCloneField').addEventListener('click',async function(){
// Ouvrir le sélecteur de mappings en mode clone (exclure le mapping courant)
var resp = await fetch(BASE+'/api/mappings/index');
var data = await resp.json();
var allMappings = data.mappings || [];
var sel2 = document.getElementById('typeFormulaireParam');
var currentMappingId2 = (sel2.options[sel2.selectedIndex] && sel2.options[sel2.selectedIndex].dataset.mappingId) || sel2.value;
var listEl = document.getElementById('selectMappingsList');
listEl.innerHTML = '';
allMappings.forEach(function(m){
if(m.id === currentMappingId2) return; // exclure le mapping courant
var label = document.createElement('label');
label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f8f9fa;border-radius:6px;cursor:pointer;font-size:0.95em';
label.innerHTML = '<input type="checkbox" class="chk-target-mapping" value="'+m.id+'" style="width:16px;height:16px"> <span><strong>'+m.name+'</strong></span>';
listEl.appendChild(label);
});
if(!listEl.children.length){alert('Aucun autre mapping disponible.');return;}
document.querySelector('#selectMappingsModal h2').textContent='Cloner le champ vers quel(s) mapping(s) ?';
document.querySelector('#selectMappingsModal p').textContent='Sélectionnez les mappings dans lesquels ce champ sera copié (le mapping actuel est exclu).';
cloneMode = true;
document.getElementById('selectMappingsModal').style.display='block';
});
// ── Fonctions auteur ──────────────────────────────────────────────────────
function getAuthor(){return sessionStorage.getItem('facturix_author')||'';}
function setAuthor(name){sessionStorage.setItem('facturix_author',name);}
function askAuthorThen(callback){
var author=getAuthor();
if(author){callback(author);return;}
pendingAuditCallback=callback;
document.getElementById('authorInput').value='';
document.getElementById('authorModal').style.display='block';
setTimeout(function(){document.getElementById('authorInput').focus();},80);
}
document.getElementById('authorConfirmBtn').addEventListener('click',function(){
var name=document.getElementById('authorInput').value.trim();
if(!name){alert('Veuillez saisir votre nom.');return;}
setAuthor(name);
document.getElementById('authorModal').style.display='none';
if(pendingAuditCallback){var cb=pendingAuditCallback;pendingAuditCallback=null;cb(name);}
});
document.getElementById('authorCancelBtn').addEventListener('click',function(){
document.getElementById('authorModal').style.display='none';
pendingAuditCallback=null;
});
document.getElementById('authorInput').addEventListener('keydown',function(e){
if(e.key==='Enter')document.getElementById('authorConfirmBtn').click();
});

var AUDIT_DIFF_FIELDS=['libelle','rdi','xpath','obligatoire','ignore','rdg','categorie_bg','attribute','type_enregistrement'];
async function logAudit(type,author,action,btBalise,oldChamp,newChamp){
var payload={author:author,action:action,bt_balise:btBalise};
if(action==='edit'){
AUDIT_DIFF_FIELDS.forEach(function(f){
payload['old_'+f]=oldChamp?String(oldChamp[f]||''):'';
payload['new_'+f]=newChamp?String(newChamp[f]||''):'';
});
}else if(action==='add'&&newChamp){
payload.snapshot=JSON.stringify(newChamp);
}else if(action==='delete'&&oldChamp){
payload.snapshot=JSON.stringify(oldChamp);
}
await fetch(BASE+'/api/mapping/'+type+'/audit',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(payload)
});
}

document.getElementById('btnSave').addEventListener('click',async function(){
var base=currentIndex!==null?currentMapping.champs[currentIndex]:{};
// Parser la valeur du select (format: "BG-XX|Titre")
var categorieValue=document.getElementById('editCategorie').value;
var categorieParts=categorieValue.split('|');
var categorieBg=categorieParts[0]||'BG-OTHER';
var categorieTitre=categorieParts[1]||'Autres';
var newChamp={
balise:document.getElementById('editBalise').value,
libelle:document.getElementById('editLibelle').value,
rdi:document.getElementById('editRdi').value,
type_enregistrement:document.getElementById('editTypeEnreg').value||undefined,
xpath:document.getElementById('editXpath').value,
attribute:document.getElementById('editAttribute').value||undefined,
is_article:(function(){var bg=categorieBg||'';return (bg==='BG-LIGNES'||bg==='BG-25'||/ligne/i.test(bg+' '+(categorieTitre||'')))?true:undefined;})(),
obligatoire:document.getElementById('editObligatoire').value,
ignore:document.getElementById('editIgnore').value,
rdg:document.getElementById('editRdg').value,
categorie_bg:categorieBg,
categorie_titre:categorieTitre,
controles_cegedim:base.controles_cegedim||[],
valide:base.valide||false,
type:base.type||undefined
};
var oldChamp=currentIndex!==null?Object.assign({},currentMapping.champs[currentIndex]):null;
var isEdit=currentIndex!==null;
// Si édition sans modification réelle : sauvegarder silencieusement, sans demander l'auteur ni logguer
if(isEdit&&oldChamp){
var AUDIT_FIELDS=['balise','libelle','rdi','type_enregistrement','xpath','attribute','obligatoire','ignore','rdg','categorie_bg','categorie_titre'];
var hasChanged=AUDIT_FIELDS.some(function(k){return (oldChamp[k]||'')!==(newChamp[k]||'');});
if(!hasChanged){
currentMapping.champs[currentIndex]=newChamp;
await saveMapping();
document.getElementById('editModal').style.display='none';
loadMappings();
return;
}
}
askAuthorThen(async function(author){
if(isEdit){
// Édition d'un champ existant
currentMapping.champs[currentIndex]=newChamp;
await saveMapping();
var type=document.getElementById('typeFormulaireParam').value;
await logAudit(type,author,'edit',newChamp.balise,oldChamp,newChamp);
}else{
// Ajout d'un nouveau champ : enregistrer dans tous les mappings sélectionnés
var currentType=document.getElementById('typeFormulaireParam').value;
for(var i=0;i<addTargetMappingIds.length;i++){
var tid=addTargetMappingIds[i];
var targetMapping;
if(tid===currentType){
targetMapping=currentMapping;
}else{
var r=await fetch(BASE+'/api/mapping/'+tid);
targetMapping=await r.json();
}
targetMapping.champs.push(newChamp);
await fetch(BASE+'/api/mapping/'+tid,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(targetMapping)
});
await logAudit(tid,author,'add',newChamp.balise,null,newChamp);
}
// Recharger le mapping courant en mémoire
var r2=await fetch(BASE+'/api/mapping/'+currentType);
currentMapping=await r2.json();
addTargetMappingIds=[];
}
document.getElementById('editModal').style.display='none';
loadMappings();
});
});


// Sauvegarder une version horodatée
async function saveMapping(){
var type=document.getElementById('typeFormulaireParam').value;
await fetch(BASE+'/api/mapping/'+type,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentMapping)
});
}
document.getElementById('typeFormulaireParam').addEventListener('change',function(){
    loadMappings();
    updateDeleteButtonVisibility();
});

// ── Color picker ──────────────────────────────────────────────────────────
document.getElementById('mappingColorBtn').addEventListener('click',function(){
document.getElementById('mappingColorPicker').click();
});
document.getElementById('mappingColorPicker').addEventListener('input',function(){
document.getElementById('mappingColorSwatch').style.background=this.value;
});
document.getElementById('mappingColorPicker').addEventListener('change',async function(){
var color=this.value;
document.getElementById('mappingColorSwatch').style.background=color;
var type=document.getElementById('typeFormulaireParam').value;
// Mettre à jour le dataset de l'option sélectionnée
var sel=document.getElementById('typeFormulaireParam');
if(sel&&sel.options[sel.selectedIndex]){sel.options[sel.selectedIndex].dataset.color=color;}
await fetch(BASE+'/api/mapping/'+type+'/color',{
method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({color:color})
});
// Sync le select du contrôle aussi
var controleOpts=document.querySelectorAll('#typeFormulaire option');
controleOpts.forEach(function(opt){if(opt.value===sel.value)opt.dataset.color=color;});
});

// ── Historique (audit) ────────────────────────────────────────────────────
var AUDIT_FIELD_LABELS={libelle:'Libellé',rdi:'Champ RDI',xpath:'XPath',
obligatoire:'Obligatoire',ignore:'Ignorer',rdg:'Règle de gestion',
attribute:'Attribut',type_enregistrement:'Type enreg.',categorie_bg:'Catégorie'};

function buildDiffHtml(e){
var html='';
var action=e.action==='revert'?'edit':e.action;
if(action==='edit'){
AUDIT_DIFF_FIELDS.forEach(function(f){
var ov=e['old_'+f]||'',nv=e['new_'+f]||'';
if(ov===nv)return;
html+='<div class="audit-diff-row">'+
'<span class="audit-diff-key">'+(AUDIT_FIELD_LABELS[f]||f)+'</span>'+
'<span class="audit-diff-old">'+escapeHtml(ov)+'</span>'+
'<span class="audit-diff-arrow">→</span>'+
'<span class="audit-diff-new">'+escapeHtml(nv)+'</span>'+
'</div>';
});
}else if((action==='add'||action==='delete')&&e.snapshot){
try{
var snap=JSON.parse(e.snapshot);
var SNAP_FIELDS=['balise','libelle','rdi','xpath','obligatoire','ignore','rdg','categorie_bg','attribute','type_enregistrement'];
var SNAP_LABELS=Object.assign({balise:'Balise BT'},AUDIT_FIELD_LABELS);
SNAP_FIELDS.forEach(function(f){
var v=snap[f];
if(!v)return;
html+='<div class="audit-diff-row">'+
'<span class="audit-diff-key">'+(SNAP_LABELS[f]||f)+'</span>'+
'<span class="audit-diff-new" style="color:'+(action==='add'?'#059669':'#dc2626')+'">'+escapeHtml(String(v))+'</span>'+
'</div>';
});
}catch(err){}
}
return html;
}

document.getElementById('btnHistory').addEventListener('click',async function(){
var type=document.getElementById('typeFormulaireParam').value;
var entries=await (await fetch(BASE+'/api/mapping/'+type+'/audit')).json();
var list=document.getElementById('auditList');
list.innerHTML='';
if(!entries||entries.length===0){
list.innerHTML='<p style="color:#94a3b8;text-align:center;padding:20px">Aucune modification enregistrée pour ce mapping.</p>';
}else{
entries.forEach(function(e){
var item=document.createElement('div');
var isRevert=e.action==='revert';
item.className='audit-item'+(isRevert?' audit-item-revert':'');
var actionLabel={'edit':'MODIF','add':'AJOUT','delete':'SUPPRESSION','revert':'ROLLBACK'}[e.action]||e.action;
var actionClass={'edit':'edit','add':'add','delete':'delete','revert':'revert'}[e.action]||'edit';

var header=document.createElement('div');
header.className='audit-item-header';

var numSpan=document.createElement('span');numSpan.className='audit-num';numSpan.textContent='#'+e.id;
var tsSpan=document.createElement('span');tsSpan.className='audit-ts';tsSpan.textContent=e.timestamp||'';
var authSpan=document.createElement('span');authSpan.className='audit-author';authSpan.textContent=e.author||'';
var actSpan=document.createElement('span');actSpan.className='audit-action '+actionClass;actSpan.textContent=actionLabel;
var btSpan=document.createElement('span');btSpan.className='audit-bt';btSpan.textContent=e.bt_balise||'';
header.appendChild(numSpan);header.appendChild(tsSpan);header.appendChild(authSpan);header.appendChild(actSpan);header.appendChild(btSpan);

if(isRevert&&e.revert_of){
var rbSpan=document.createElement('span');rbSpan.className='audit-rollback-label';rbSpan.textContent='Rollback de la modification #'+e.revert_of;
header.appendChild(rbSpan);
}

if(e.action==='edit'||e.action==='delete'){
var btn=document.createElement('button');
btn.className='audit-revert-btn';
btn.textContent='↩ Revenir';
btn.dataset.id=String(e.id);
header.appendChild(btn);
}
item.appendChild(header);

var diffHtml=buildDiffHtml(e);
if(diffHtml){
var diffDiv=document.createElement('div');
diffDiv.className='audit-diff';
diffDiv.innerHTML=diffHtml;
item.appendChild(diffDiv);
}
list.appendChild(item);
});

list.querySelectorAll('.audit-revert-btn').forEach(function(btn){
btn.addEventListener('click',function(){
var id=this.dataset.id;
if(!confirm('Revenir à l\'état précédent de ce champ ?'))return;
askAuthorThen(async function(author){
var res=await (await fetch(BASE+'/api/mapping/'+type+'/audit/'+id+'/revert',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({author:author})
})).json();
if(res.success){
loadMappings();
document.getElementById('btnHistory').click();
}else{alert('Erreur : '+(res.error||'Impossible de revenir en arrière'));}
});
});
});
}
document.getElementById('historyModal').style.display='block';
});
document.getElementById('historyModalClose').addEventListener('click',function(){
document.getElementById('historyModal').style.display='none';
});
document.getElementById('historyCloseBtn').addEventListener('click',function(){
document.getElementById('historyModal').style.display='none';
});

function escapeHtml(s){if(!s)return '';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}


/* ---- TOOLTIP IGNORÉ ---- */
(function(){
var tip=document.getElementById('ignored-tooltip');
document.addEventListener('mouseover',function(e){
var li=e.target.closest('.has-ignored-tip');
if(!li){tip.style.display='none';return;}
tip.style.display='block';
var r=li.getBoundingClientRect();
var tx=r.left;
var ty=r.bottom+6;
if(tx+tip.offsetWidth>window.innerWidth-12)tx=window.innerWidth-tip.offsetWidth-12;
if(ty+tip.offsetHeight>window.innerHeight-12)ty=r.top-tip.offsetHeight-6;
tip.style.left=tx+'px';
tip.style.top=ty+'px';
});
document.addEventListener('mouseout',function(e){
var li=e.target.closest('.has-ignored-tip');
if(li&&!li.contains(e.relatedTarget))tip.style.display='none';
});
})();

/* ---- RECHERCHE BT PARAMETRAGE ---- */
document.getElementById('searchBTParam').addEventListener('input',function(){
var btn=document.getElementById('btnClearSearchParam');
btn.style.display=this.value?'block':'none';
applySearchParamFilter();
});
document.getElementById('btnClearSearchParam').addEventListener('click',function(){
document.getElementById('searchBTParam').value='';
this.style.display='none';
applySearchParamFilter();
});

/* ---- RÈGLES MÉTIERS ---- */
var currentRules={rules:[]};
var availableBTs=[];
var availableRDIs=[];

async function loadAvailableBTs(){
// Charger tous les BT depuis tous les mappings
var types=['simple','groupee','ventesdiverses'];
var allBTs={};
var allRDIs={};
for(var i=0;i<types.length;i++){
try{
var resp=await fetch(BASE+'/api/mapping/'+types[i]);
var mapping=await resp.json();
if(mapping&&mapping.champs){
mapping.champs.forEach(function(champ){
if(champ.balise){
allBTs[champ.balise]=champ.libelle||champ.balise;
}
if(champ.rdi&&!allRDIs[champ.rdi]){
allRDIs[champ.rdi]=champ.balise||champ.rdi;
}
});
}
}catch(e){}
}
// Convertir en array et trier par numéro de BT
availableBTs=Object.keys(allBTs).sort(function(a,b){
// Extraire les numéros des BT (ex: BT-131-0 -> [131, 0])
var aMatch=a.match(/BT-(\d+)(?:-(\d+))?/);
var bMatch=b.match(/BT-(\d+)(?:-(\d+))?/);
if(!aMatch||!bMatch)return a.localeCompare(b);
var aNum1=parseInt(aMatch[1]);
var bNum1=parseInt(bMatch[1]);
if(aNum1!==bNum1)return aNum1-bNum1;
// Si même premier numéro, comparer le second
var aNum2=aMatch[2]?parseInt(aMatch[2]):0;
var bNum2=bMatch[2]?parseInt(bMatch[2]):0;
return aNum2-bNum2;
}).map(function(bt){
return {value:bt,label:bt+' ('+allBTs[bt]+')'};
});
// Convertir les RDI en array trié alphabétiquement
availableRDIs=Object.keys(allRDIs).sort().map(function(rdi){
return {value:rdi,label:rdi+' ('+allRDIs[rdi]+')'};
});
}

var ruleCategories=['Calculs','Exonérations TVA','B2G / Chorus','Notes & mentions','Paiement','Cohérence','Autre'];
var activeRuleCategory='ALL';
var availableForms=[]; // [{value, label, mappingId}]

function refreshCategorySelect(){
var sel=document.getElementById('ruleCategory');
if(!sel)return;
sel.innerHTML=ruleCategories.map(function(c){return '<option value="'+c+'">'+c+'</option>';}).join('');
}

function mappingIdToFormValue(id){
if(id==='default_simple')return 'simple';
if(id==='default_groupee')return 'groupee';
if(id==='default_flux')return 'flux';
if(id==='default_ventesdiverses')return 'ventesdiverses';
return 'custom_'+id;
}

function getFormLabel(value){
for(var i=0;i<availableForms.length;i++){
if(availableForms[i].value===value)return availableForms[i].label;
}
return value;
}

async function loadAvailableForms(){
try{
var resp=await fetch(BASE+'/api/mappings/index');
var data=await resp.json();
availableForms=(data.mappings||[]).map(function(m){
return {value:mappingIdToFormValue(m.id),label:m.name,mappingId:m.id};
});
}catch(e){availableForms=[];}
// Mettre à jour le select de filtre
var filterSel=document.getElementById('filterFormType');
if(filterSel){
var current=filterSel.value||'all';
filterSel.innerHTML='<option value="all">Toutes les factures</option>'+
availableForms.map(function(f){return '<option value="'+f.value+'">'+f.label+' uniquement</option>';}).join('');
filterSel.value=Array.from(filterSel.options).some(function(o){return o.value===current;})?current:'all';
}
}

function renderFormCheckboxes(selectedForms){
var container=document.getElementById('ruleFormsContainer');
if(!container)return;
var allChecked=!selectedForms||selectedForms.length===0;
container.innerHTML=availableForms.map(function(f){
var checked=allChecked||selectedForms.indexOf(f.value)!==-1;
return '<label style="display:flex;align-items:center;gap:8px;font-weight:normal">'+
'<input type="checkbox" class="rule-form-cb" data-value="'+f.value+'"'+(checked?' checked':'')+' style="width:18px;height:18px">'+
'<span>'+f.label+'</span>'+
'</label>';
}).join('');
}

async function loadRules(){
await loadAvailableBTs();
await loadAvailableForms();
var resp=await fetch(BASE+'/api/rules');
currentRules=await resp.json();
if(currentRules.categories&&currentRules.categories.length){ruleCategories=currentRules.categories;}
refreshCategorySelect();
displayRules();
}

function displayRules(){
var container=document.getElementById('rulesList');
var filter=document.getElementById('filterFormType').value;
container.innerHTML='';
if(!currentRules.rules || currentRules.rules.length===0){
container.innerHTML='<p>Aucune règle définie</p>';
return;
}
var filteredRules=currentRules.rules.filter(function(rule){
if(filter==='all')return true;
var forms=rule.applicable_forms||[];
return forms.length===0||forms.includes(filter);
});
if(filteredRules.length===0){
container.innerHTML='<p>Aucune règle applicable à ce type de factures</p>';
return;
}
// Regrouper par catégorie
var byCategory={};
filteredRules.forEach(function(rule){
var cat=rule.category||'Autre';
if(!byCategory[cat])byCategory[cat]=[];
byCategory[cat].push(rule);
});
// Ordre d'affichage : catégories connues d'abord, puis les inconnues triées
var orderedCats=ruleCategories.filter(function(c){return byCategory[c];});
Object.keys(byCategory).forEach(function(c){if(orderedCats.indexOf(c)===-1)orderedCats.push(c);});
// Si la catégorie active n'existe plus (changement de filtre type-facture), retomber sur ALL
if(activeRuleCategory!=='ALL'&&!byCategory[activeRuleCategory]){activeRuleCategory='ALL';}
// Barre de filtres pills (style Paramétrage)
var filterBar=document.createElement('div');
filterBar.className='cat-filter-bar';
var allPill=document.createElement('span');
allPill.className='cat-pill'+(activeRuleCategory==='ALL'?' active':'');
allPill.dataset.cat='ALL';
allPill.textContent='Tout ('+filteredRules.length+')';
filterBar.appendChild(allPill);
orderedCats.forEach(function(cat){
var pill=document.createElement('span');
pill.className='cat-pill'+(activeRuleCategory===cat?' active':'');
pill.dataset.cat=cat;
pill.textContent=cat+' ('+byCategory[cat].length+')';
filterBar.appendChild(pill);
});
container.appendChild(filterBar);
filterBar.querySelectorAll('.cat-pill').forEach(function(pill){
pill.addEventListener('click',function(){
activeRuleCategory=pill.dataset.cat;
displayRules();
});
});
// Filtrer selon la pill active
var visibleCats=(activeRuleCategory==='ALL')?orderedCats:[activeRuleCategory];
visibleCats.forEach(function(cat){
var rules=byCategory[cat];
if(!rules)return;
var header=document.createElement('div');
header.className='rule-category-header';
header.innerHTML='<h3 style="margin:18px 0 8px;padding:6px 12px;background:#eef2f7;border-left:4px solid #3b82f6;border-radius:4px;font-size:0.95em;color:#1e293b">'+cat+' <span style="color:#64748b;font-weight:normal;font-size:0.85em">('+rules.length+')</span></h3>';
container.appendChild(header);
rules.forEach(function(rule){
var index=currentRules.rules.indexOf(rule);
var div=document.createElement('div');
div.className='rule-card';
var enabledClass=rule.enabled?'enabled':'disabled';
var enabledText=rule.enabled?'✓ Activée':'✗ Désactivée';
// Afficher les formulaires applicables
var formsText='';
var forms=rule.applicable_forms||[];
if(forms.length===0){
formsText='<span style="color:#999;font-size:0.85em">Tous les types</span>';
}else{
formsText='<span style="color:#666;font-size:0.85em">'+forms.map(function(f){return getFormLabel(f);}).join(', ')+'</span>';
}
// Construire le texte de la règle
var conditionsText='';
if(rule.conditions && rule.conditions.length>0){
conditionsText='<strong>Si :</strong> ';
rule.conditions.forEach(function(c,i){
if(i>0)conditionsText+=' ET ';
conditionsText+=c.field+' '+getOperatorLabel(c.operator)+' "'+c.value+'"';
});
}else{
conditionsText='<strong>Toujours</strong>';
}
var actionsText='<strong>Alors :</strong> ';
rule.actions.forEach(function(a,i){
if(i>0)actionsText+=', ';
if(a.type==='make_mandatory'){
actionsText+=a.field+' devient obligatoire';
}else if(a.type==='make_optional'){
actionsText+=a.field+' devient non obligatoire';
}else if(a.type==='must_equal'){
actionsText+=a.field+' doit égaler "'+a.value+'"';
}else if(a.type==='must_be_negative'){
actionsText+=a.field+' doit être négatif';
}else if(a.type==='must_equal_sum'){
actionsText+=a.field+' doit égaler '+(a.field1||'?')+' + '+(a.field2||'?');
}else if(a.type==='must_equal_product'){
actionsText+=a.field+' doit égaler '+(a.field1||'?')+' × '+(a.field2||'?')+' (tolérance '+(a.tolerance||'0.01')+')';
}else if(a.type==='must_equal_sum_of_all'){
actionsText+=a.field+' doit égaler Σ '+(a.sum_field||'?')+' (tolérance '+(a.tolerance||'0.01')+')';
}
});
div.innerHTML='<div class="rule-header '+enabledClass+'">'+
'<div class="rule-title">'+
'<strong>'+rule.name+'</strong>'+
'<span class="rule-status">'+enabledText+'</span>'+
'</div>'+
'<div class="rule-actions-btn">'+
'<button class="btn-toggle" data-index="'+index+'">'+(rule.enabled?'Désactiver':'Activer')+'</button>'+
'<button class="btn-edit" data-index="'+index+'">Éditer</button>'+
'<button class="btn-clone" data-index="'+index+'" title="Dupliquer cette règle">⎘ Cloner</button>'+
'<button class="btn-delete" data-index="'+index+'">Supprimer</button>'+
'</div>'+
'</div>'+
'<div class="rule-body">'+
(rule.description?'<div class="rule-description">'+rule.description+'</div>':'')+
'<div style="margin-bottom:10px"><strong>Types de factures :</strong> '+formsText+'</div>'+
'<div class="rule-logic">'+
'<div>'+conditionsText+'</div>'+
'<div>'+actionsText+'</div>'+
'</div>'+
'</div>';
container.appendChild(div);
});
});
document.querySelectorAll('.btn-toggle').forEach(function(btn){
btn.addEventListener('click',function(){
var idx=parseInt(this.getAttribute('data-index'));
currentRules.rules[idx].enabled=!currentRules.rules[idx].enabled;
saveRules();
});
});
document.querySelectorAll('.btn-edit').forEach(function(btn){
btn.addEventListener('click',function(){
editRule(parseInt(this.getAttribute('data-index')));
});
});
document.querySelectorAll('.btn-clone').forEach(function(btn){
btn.addEventListener('click',function(){
var idx=parseInt(this.getAttribute('data-index'));
var src=currentRules.rules[idx];
var copy=JSON.parse(JSON.stringify(src));
copy.id='rule_'+Date.now();
copy.name=(src.name||'Règle')+' (copie)';
currentRules.rules.splice(idx+1,0,copy);
saveRules();
});
});
document.querySelectorAll('.btn-delete').forEach(function(btn){
btn.addEventListener('click',function(){
if(confirm('Supprimer cette règle ?')){
currentRules.rules.splice(parseInt(this.getAttribute('data-index')),1);
saveRules();
}
});
});
}

function getOperatorLabel(op){
var labels={
'equals':'=',
'not_equals':'≠',
'contains':'contient',
'not_contains':'ne contient pas',
'starts_with':'commence par',
'not_starts_with':'ne commence pas par',
'less_than':'<',
'greater_than':'>',
'is_empty':'est vide',
'is_not_empty':'n\'est pas vide'
};
return labels[op]||op;
}

async function saveRules(){
await fetch(BASE+'/api/rules',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentRules)
});
displayRules();
}

document.getElementById('btnReloadRules').addEventListener('click',loadRules);
document.getElementById('filterFormType').addEventListener('change',displayRules);
document.getElementById('btnAddRule').addEventListener('click',function(){
currentRuleIndex=null;
document.getElementById('ruleModalTitle').textContent='Créer une règle';
document.getElementById('ruleName').value='';
document.getElementById('ruleDescription').value='';
refreshCategorySelect();
document.getElementById('ruleCategory').value='Autre';
document.getElementById('ruleEnabled').checked=true;
renderFormCheckboxes(null); // tout coché par défaut
editingConditions=[];
editingActions=[];
renderConditions();
renderActions();
document.getElementById('editRuleModal').style.display='block';
});

var currentRuleIndex=null;
var editingConditions=[];
var editingActions=[];

function editRule(index){
currentRuleIndex=index;
var rule=currentRules.rules[index];
document.getElementById('ruleModalTitle').textContent='Éditer la règle';
document.getElementById('ruleName').value=rule.name;
document.getElementById('ruleDescription').value=rule.description||'';
refreshCategorySelect();
var cat=rule.category||'Autre';
var catSel=document.getElementById('ruleCategory');
if(ruleCategories.indexOf(cat)===-1){
// Catégorie inconnue saisie manuellement : on l'ajoute à la liste
var opt=document.createElement('option');opt.value=cat;opt.textContent=cat;catSel.appendChild(opt);
}
catSel.value=cat;
document.getElementById('ruleEnabled').checked=rule.enabled!==false;
renderFormCheckboxes(rule.applicable_forms||[]);
editingConditions=JSON.parse(JSON.stringify(rule.conditions||[]));
editingActions=JSON.parse(JSON.stringify(rule.actions||[]));
renderConditions();
renderActions();
document.getElementById('editRuleModal').style.display='block';
}

function renderConditions(){
var container=document.getElementById('conditionsList');
container.innerHTML='';
if(editingConditions.length===0){
container.innerHTML='<p style="color:#999;font-size:0.9em">Aucune condition (la règle s\'appliquera toujours)</p>';
return;
}
editingConditions.forEach(function(cond,i){
var div=document.createElement('div');
div.className='condition-item';
var isRdi=(cond.field_type==='rdi');
// Construire les options selon le type sélectionné
var fieldOptions='<option value="">Champ...</option>';
var opts=isRdi?availableRDIs:availableBTs;
opts.forEach(function(bt){
fieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
div.innerHTML=
'<select class="cond-type" data-index="'+i+'" title="Type de champ" style="width:60px;flex-shrink:0">'+
'<option value="bt"'+(isRdi?'':' selected')+'>BT</option>'+
'<option value="rdi"'+(isRdi?' selected':'')+'>RDI</option>'+
'</select>'+
'<select class="cond-field" data-index="'+i+'">'+fieldOptions+'</select>'+
'<select class="cond-op" data-index="'+i+'">'+
'<option value="equals">= (égal)</option>'+
'<option value="not_equals">≠ (différent)</option>'+
'<option value="contains">contient</option>'+
'<option value="not_contains">ne contient pas</option>'+
'<option value="starts_with">commence par</option>'+
'<option value="not_starts_with">ne commence pas par</option>'+
'<option value="less_than">&lt; (inférieur)</option>'+
'<option value="greater_than">&gt; (supérieur)</option>'+
'<option value="is_empty">est vide</option>'+
'<option value="is_not_empty">n\'est pas vide</option>'+
'</select>'+
'<input type="text" class="cond-value" data-index="'+i+'" placeholder="Valeur" value="'+cond.value+'">'+
'<button class="btn-remove" data-index="'+i+'">Supprimer</button>';
container.appendChild(div);
div.querySelector('.cond-field').value=cond.field;
div.querySelector('.cond-op').value=cond.operator;
});
document.querySelectorAll('.cond-type').forEach(function(el){
el.addEventListener('change',function(){
var idx=parseInt(this.getAttribute('data-index'));
editingConditions[idx].field_type=this.value;
editingConditions[idx].field='';
renderConditions();
});
});
document.querySelectorAll('.cond-field').forEach(function(el){
el.addEventListener('change',function(){
editingConditions[this.getAttribute('data-index')].field=this.value;
});
});
document.querySelectorAll('.cond-op').forEach(function(el){
el.addEventListener('change',function(){
editingConditions[this.getAttribute('data-index')].operator=this.value;
});
});
document.querySelectorAll('.cond-value').forEach(function(el){
el.addEventListener('input',function(){
editingConditions[this.getAttribute('data-index')].value=this.value;
});
});
document.querySelectorAll('.condition-item .btn-remove').forEach(function(btn){
btn.addEventListener('click',function(){
editingConditions.splice(parseInt(this.getAttribute('data-index')),1);
renderConditions();
});
});
}

function renderActions(){
var container=document.getElementById('actionsList');
container.innerHTML='';
if(editingActions.length===0){
container.innerHTML='<p style="color:#999;font-size:0.9em">Aucune action</p>';
return;
}
editingActions.forEach(function(action,i){
var div=document.createElement('div');
div.className='action-item';
var isRdi=(action.field_type==='rdi');
// Construire les options dynamiquement avec libellés complets
var fieldOptions='<option value="">Champ...</option>';
var opts=isRdi?availableRDIs:availableBTs;
opts.forEach(function(bt){
fieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
// Options BT toujours pour les champs de calcul (field1/field2/sum-field)
var btFieldOptions='<option value="">Champ...</option>';
availableBTs.forEach(function(bt){
btFieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
var needsValue=(action.type==='must_equal');
var needsSum=(action.type==='must_equal_sum');
var needsProduct=(action.type==='must_equal_product');
var needsSumAll=(action.type==='must_equal_sum_of_all');
// ORDRE: Type (BT/RDI), Champ, Type d'action, Valeur (si nécessaire), Supprimer
div.innerHTML=
'<select class="action-ftype" data-index="'+i+'" title="Type de champ" style="width:60px;flex-shrink:0">'+
'<option value="bt"'+(isRdi?'':' selected')+'>BT</option>'+
'<option value="rdi"'+(isRdi?' selected':'')+'>RDI</option>'+
'</select>'+
'<select class="action-field" data-index="'+i+'">'+fieldOptions+'</select>'+
'<select class="action-type" data-index="'+i+'">'+
'<option value="make_mandatory">Rendre obligatoire</option>'+
'<option value="make_optional">Rendre non obligatoire</option>'+
'<option value="must_equal">Doit égaler</option>'+
'<option value="must_be_negative">Doit être négatif</option>'+
'<option value="must_equal_sum">Doit égaler la somme de</option>'+
'<option value="must_equal_product">Doit égaler le produit de</option>'+
'<option value="must_equal_sum_of_all">Doit égaler Σ de toutes les lignes</option>'+
'</select>'+
(needsValue?'<input type="text" class="action-value" data-index="'+i+'" placeholder="Valeur" value="'+(action.value||'')+'">':'')+
(needsSum?'<select class="action-field1" data-index="'+i+'">'+btFieldOptions+'</select><span style="padding:0 4px;font-weight:bold">+</span><select class="action-field2" data-index="'+i+'">'+btFieldOptions+'</select>':'')+
(needsProduct?'<select class="action-field1" data-index="'+i+'">'+btFieldOptions+'</select><span style="padding:0 4px;font-weight:bold">×</span><select class="action-field2" data-index="'+i+'">'+btFieldOptions+'</select><input type="number" class="action-tolerance" data-index="'+i+'" placeholder="Tolérance (€)" step="0.01" min="0" style="width:110px" value="'+(action.tolerance!=null?action.tolerance:'0.01')+'"><span style="padding:0 4px;font-size:0.85em;color:#888">€ écart max</span>':'')+
(needsSumAll?'<span style="padding:0 4px">Σ</span><select class="action-sum-field" data-index="'+i+'">'+btFieldOptions+'</select><input type="number" class="action-tolerance" data-index="'+i+'" placeholder="Tolérance (€)" step="0.01" min="0" style="width:110px" value="'+(action.tolerance!=null?action.tolerance:'0.01')+'"><span style="padding:0 4px;font-size:0.85em;color:#888">€ écart max</span>':'')+
'<button class="btn-remove" data-index="'+i+'">Supprimer</button>';
container.appendChild(div);
div.querySelector('.action-field').value=action.field;
div.querySelector('.action-type').value=action.type;
if(needsSum||needsProduct){
if(div.querySelector('.action-field1'))div.querySelector('.action-field1').value=action.field1||'';
if(div.querySelector('.action-field2'))div.querySelector('.action-field2').value=action.field2||'';
}
if(needsSumAll){
if(div.querySelector('.action-sum-field'))div.querySelector('.action-sum-field').value=action.sum_field||'';
}
});
document.querySelectorAll('.action-ftype').forEach(function(el){
el.addEventListener('change',function(){
var idx=parseInt(this.getAttribute('data-index'));
editingActions[idx].field_type=this.value;
editingActions[idx].field='';
renderActions();
});
});
document.querySelectorAll('.action-type').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].type=this.value;
renderActions();
});
});
document.querySelectorAll('.action-field').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].field=this.value;
});
});
document.querySelectorAll('.action-value').forEach(function(el){
el.addEventListener('input',function(){
editingActions[this.getAttribute('data-index')].value=this.value;
});
});
document.querySelectorAll('.action-field1').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].field1=this.value;
});
});
document.querySelectorAll('.action-field2').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].field2=this.value;
});
});
document.querySelectorAll('.action-sum-field').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].sum_field=this.value;
});
});
document.querySelectorAll('.action-tolerance').forEach(function(el){
el.addEventListener('input',function(){
var v=parseFloat(this.value);
editingActions[this.getAttribute('data-index')].tolerance=isNaN(v)?0.01:v;
});
});
document.querySelectorAll('.action-item .btn-remove').forEach(function(btn){
btn.addEventListener('click',function(){
editingActions.splice(parseInt(this.getAttribute('data-index')),1);
renderActions();
});
});
}

document.getElementById('btnAddCondition').addEventListener('click',function(){
editingConditions.push({field_type:'bt',field:'',operator:'equals',value:''});
renderConditions();
});

document.getElementById('btnAddAction').addEventListener('click',function(){
editingActions.push({field_type:'bt',type:'make_mandatory',field:''});
renderActions();
});

document.getElementById('ruleModalClose').addEventListener('click',function(){
document.getElementById('editRuleModal').style.display='none';
});

document.getElementById('btnSaveRule').addEventListener('click',function(){
var applicableForms=[];
document.querySelectorAll('#ruleFormsContainer .rule-form-cb').forEach(function(cb){
if(cb.checked)applicableForms.push(cb.dataset.value);
});
// Si toutes cochées, on stocke un tableau vide (= tous types)
if(applicableForms.length===availableForms.length)applicableForms=[];
var rule={
id:currentRuleIndex!==null?currentRules.rules[currentRuleIndex].id:'rule_'+Date.now(),
name:document.getElementById('ruleName').value,
description:document.getElementById('ruleDescription').value,
category:document.getElementById('ruleCategory').value||'Autre',
enabled:document.getElementById('ruleEnabled').checked,
applicable_forms:applicableForms,
conditions:editingConditions.filter(function(c){return c.field}),
actions:editingActions.filter(function(a){return a.field})
};
if(!rule.name){
alert('Veuillez donner un nom à la règle');
return;
}
if(rule.actions.length===0){
alert('Veuillez ajouter au moins une action');
return;
}
if(currentRuleIndex!==null){
currentRules.rules[currentRuleIndex]=rule;
}else{
currentRules.rules.push(rule);
}
saveRules();
document.getElementById('editRuleModal').style.display='none';
});

</script>
</body>
</div></div>

<script>
(function(){
  var seq=[38,38,40,40,37,39,37,39,66,65];
  var idx=0;
  document.addEventListener('keydown',function(e){
    if(e.keyCode===seq[idx]){
      idx++;
      if(idx===seq.length){
        document.getElementById('konamiOverlay').classList.add('visible');
        idx=0;
      }
    } else {
      idx=(e.keyCode===seq[0])?1:0;
    }
  });
  document.getElementById('konamiOverlay').addEventListener('click',function(e){
    if(e.target===this) this.classList.remove('visible');
  });
})();
</script>
</html>"""

@app.route('/img/<path:filename>')
def serve_image(filename):
    from flask import send_from_directory
    return send_from_directory(os.path.join(SCRIPT_DIR, 'img'), filename)

@app.route('/')
def index():
    prefix = request.script_root or URL_PREFIX
    return HTML.replace('__URL_PREFIX__', prefix)

@app.route('/api/mapping/<type_formulaire>')
def get_mapping(type_formulaire):
    data = load_mapping(type_formulaire)
    return jsonify(data if data else {'champs': []})

@app.route('/api/mapping/<type_formulaire>', methods=['POST'])
def save_mapping_route(type_formulaire):
    data = request.json
    success = save_mapping(data, type_formulaire)
    return jsonify({'success': success})

@app.route('/api/mapping/<type_formulaire>/version', methods=['POST'])
def save_version_route(type_formulaire):
    data = request.json
    result = save_mapping_version(data, type_formulaire)
    return jsonify(result)

@app.route('/api/mapping/<type_formulaire>/versions')
def list_versions_route(type_formulaire):
    versions = list_mapping_versions(type_formulaire)
    return jsonify(versions)

@app.route('/api/mapping/<type_formulaire>/restore', methods=['POST'])
def restore_version_route(type_formulaire):
    data = request.json
    filename = data.get('filename')
    if not filename:
        return jsonify({'success': False, 'error': 'Nom de fichier manquant'}), 400
    result = restore_mapping_version(filename, type_formulaire)
    return jsonify(result)

@app.route('/api/mapping/<type_formulaire>/color', methods=['POST'])
def save_color_route(type_formulaire):
    data = request.json
    color = data.get('color', '')
    mapping_id = _get_mapping_id(type_formulaire)
    try:
        conn = get_db()
        conn.execute("UPDATE mappings SET color=? WHERE id=?", (color, mapping_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

_AUDIT_FIELDS = ['libelle', 'rdi', 'xpath', 'obligatoire', 'ignore',
                 'rdg', 'categorie_bg', 'attribute', 'type_enregistrement']

@app.route('/api/mapping/<type_formulaire>/audit', methods=['GET'])
def get_audit_route(type_formulaire):
    mapping_id = _get_mapping_id(type_formulaire)
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, timestamp, author, action, bt_balise, revert_of, snapshot, "
            "old_libelle, new_libelle, old_rdi, new_rdi, old_xpath, new_xpath, "
            "old_obligatoire, new_obligatoire, old_ignore, new_ignore, "
            "old_rdg, new_rdg, old_categorie_bg, new_categorie_bg, "
            "old_attribute, new_attribute, old_type_enregistrement, new_type_enregistrement "
            "FROM mapping_audit WHERE mapping_id=? ORDER BY id DESC LIMIT 100",
            (mapping_id,)
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            entry = {
                'id': r['id'], 'timestamp': r['timestamp'],
                'author': r['author'], 'action': r['action'],
                'bt_balise': r['bt_balise'],
                'revert_of': r['revert_of'],
                'snapshot': r['snapshot'],
            }
            for f in _AUDIT_FIELDS:
                entry[f'old_{f}'] = r[f'old_{f}']
                entry[f'new_{f}'] = r[f'new_{f}']
            result.append(entry)
        return jsonify(result)
    except Exception:
        return jsonify([])

@app.route('/api/mapping/<type_formulaire>/audit', methods=['POST'])
def log_audit_route(type_formulaire):
    from datetime import datetime
    data = request.json
    mapping_id = _get_mapping_id(type_formulaire)
    try:
        cols = ['mapping_id', 'timestamp', 'author', 'action', 'bt_balise']
        vals = [
            mapping_id,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            data.get('author', ''),
            data.get('action', 'edit'),
            data.get('bt_balise', ''),
        ]
        for f in _AUDIT_FIELDS:
            cols.append(f'old_{f}'); vals.append(data.get(f'old_{f}'))
            cols.append(f'new_{f}'); vals.append(data.get(f'new_{f}'))
        # snapshot pour add/delete
        snapshot = data.get('snapshot')
        if snapshot is not None:
            cols.append('snapshot'); vals.append(snapshot)
        ph = ','.join('?' * len(vals))
        conn = get_db()
        conn.execute(
            f"INSERT INTO mapping_audit ({','.join(cols)}) VALUES ({ph})", vals
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/mapping/<type_formulaire>/audit/<int:audit_id>/revert', methods=['POST'])
def revert_audit_route(type_formulaire, audit_id):
    """Revenir à l'état précédent d'un champ via l'entrée d'audit."""
    from datetime import datetime
    mapping_id = _get_mapping_id(type_formulaire)
    author = (request.json or {}).get('author', '') if request.is_json else ''
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM mapping_audit WHERE id=? AND mapping_id=?",
            (audit_id, mapping_id)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'Entrée introuvable'}), 404

        action   = row['action']
        bt_balise = row['bt_balise']

        if action == 'edit':
            # Mettre à jour les colonnes individuelles avec les anciennes valeurs
            updates = {}
            for f in _AUDIT_FIELDS:
                old_val = row[f'old_{f}']
                if old_val is not None:
                    db_col = 'ignore_field' if f == 'ignore' else f
                    updates[db_col] = old_val
            if updates:
                set_clause = ', '.join(f'{k}=?' for k in updates)
                conn.execute(
                    f"UPDATE mapping_champs SET {set_clause} WHERE mapping_id=? AND balise=?",
                    list(updates.values()) + [mapping_id, bt_balise]
                )

        elif action == 'add':
            # Annuler un ajout = supprimer le champ
            conn.execute(
                "DELETE FROM mapping_champs WHERE mapping_id=? AND balise=?",
                (mapping_id, bt_balise)
            )

        elif action in ('delete', 'revert'):
            # Annuler une suppression ou un revert = réinsérer depuis snapshot
            snapshot = row['snapshot']
            if snapshot:
                champ = json.loads(snapshot)
                # Position en fin de liste
                pos_row = conn.execute(
                    "SELECT COALESCE(MAX(position)+1, 0) AS pos FROM mapping_champs WHERE mapping_id=?",
                    (mapping_id,)
                ).fetchone()
                pos = pos_row['pos'] if pos_row else 0
                conn.execute(_CHAMP_INSERT_SQL, _champ_to_row(mapping_id, pos, champ))

        # Enregistrer le rollback dans l'audit (old/new inversés par rapport à la modif d'origine)
        rb_cols = ['mapping_id', 'timestamp', 'author', 'action', 'bt_balise', 'revert_of']
        rb_vals = [mapping_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), author, 'revert', bt_balise, audit_id]
        for f in _AUDIT_FIELDS:
            rb_cols.append(f'old_{f}'); rb_vals.append(row[f'new_{f}'])
            rb_cols.append(f'new_{f}'); rb_vals.append(row[f'old_{f}'])
        ph = ','.join('?' * len(rb_vals))
        conn.execute(f"INSERT INTO mapping_audit ({','.join(rb_cols)}) VALUES ({ph})", rb_vals)

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rules', methods=['GET'])
def get_rules():
    rules = load_business_rules()
    payload = dict(rules)
    payload['categories'] = list(_RULE_CATEGORIES_ORDER)
    return jsonify(payload)

@app.route('/api/rules', methods=['POST'])
def save_rules():
    rules_data = request.json
    success = save_business_rules(rules_data)
    return jsonify({'success': success})

def _process_invoice(rdi_path, pdf_path, cii_path, type_formulaire, type_controle):
    """Traite une facture à partir de chemins de fichiers déjà sauvegardés.
    Retourne (result_dict, error_str). result_dict contient results, stats, categories_results, type_controle."""
    try:
        rdi_data = {}
        rdi_articles = []
        rdi_multi = {}
        if rdi_path:
            rdi_data, rdi_articles, rdi_multi = parse_rdi(rdi_path)

        xml_doc = None
        if type_controle == 'cii' and cii_path:
            with open(cii_path, 'r', encoding='utf-8') as f:
                xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except Exception:
                return None, 'XML CII invalide'
        elif pdf_path:
            if pdf_path.lower().endswith('.pdf'):
                xml_content = extract_xml_from_pdf(pdf_path)
                if not xml_content:
                    return None, 'XML introuvable dans le PDF'
            else:
                with open(pdf_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except Exception:
                return None, 'XML invalide'

        mapping_data = load_mapping(type_formulaire)
        if not mapping_data:
            return None, 'Mapping introuvable'

        mapping = mapping_data.get('champs', [])
        namespaces = build_xml_namespaces(xml_doc)

        xpath_cache = {}
        if xml_doc is not None:
            for field in mapping:
                _xpath_raw = field.get('xpath', '') or ''
                if _xpath_raw and _xpath_raw not in xpath_cache:
                    _xpath = _xpath_raw if _xpath_raw.startswith('/') else '//' + _xpath_raw
                    try:
                        xpath_cache[_xpath_raw] = etree.XPath(_xpath, namespaces=namespaces)
                    except Exception:
                        xpath_cache[_xpath_raw] = None

        # Articles XML
        xml_articles = []
        if xml_doc is not None:
            try:
                line_items_xpath = etree.XPath(
                    '/rsm:CrossIndustryInvoice/rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem',
                    namespaces=namespaces)
                for item in line_items_xpath(xml_doc):
                    xml_art = {}
                    for field in mapping:
                        if not field.get('is_article'):
                            continue
                        _xpath_raw = field.get('xpath', '') or ''
                        if not _xpath_raw:
                            continue
                        marker = 'ram:IncludedSupplyChainTradeLineItem/'
                        idx = _xpath_raw.find(marker)
                        if idx < 0:
                            continue
                        rel_xpath = './' + _xpath_raw[idx + len(marker):]
                        try:
                            elements = etree.XPath(rel_xpath, namespaces=namespaces)(item)
                            if elements:
                                attribute = field.get('attribute')
                                if attribute and hasattr(elements[0], 'get'):
                                    xml_art[field['balise']] = elements[0].get(attribute, '').strip()
                                elif hasattr(elements[0], 'text') and elements[0].text:
                                    xml_art[field['balise']] = elements[0].text.strip()
                        except Exception:
                            pass
                    xml_articles.append(xml_art)
            except Exception as e:
                print(f'[batch] Erreur articles XML: {e}')

        article_fields = [f for f in mapping if f.get('is_article')]
        header_fields = [f for f in mapping if not f.get('is_article')]
        results = []

        for index, field in enumerate(header_fields):
            rdi_field_name = field.get('rdi', '')
            type_enreg = (field.get('type_enregistrement') or '').strip().upper()
            is_ambiguous = False
            rdi_value = ''
            if rdi_field_name:
                field_upper = rdi_field_name.upper()
                occurrences = rdi_multi.get(field_upper, [])
                if type_enreg:
                    matches = [v for rt, v in occurrences if rt.upper() == type_enreg]
                    rdi_value = matches[0].strip() if matches else ''
                elif len(occurrences) > 1:
                    is_ambiguous = True
                else:
                    rdi_value = rdi_data.get(rdi_field_name, '').strip()
                    if not rdi_value:
                        for key in rdi_data.keys():
                            if key.upper() == field_upper:
                                rdi_value = rdi_data[key].strip()
                                break

            xml_value = ''
            xml_all = []
            if xml_doc is not None:
                try:
                    _xpath_raw = field.get('xpath', '') or ''
                    if _xpath_raw:
                        compiled = xpath_cache.get(_xpath_raw)
                        if compiled is not None:
                            elements = compiled(xml_doc)
                            if elements:
                                attribute = field.get('attribute')
                                for el in elements:
                                    if attribute and hasattr(el, 'get'):
                                        xml_all.append(el.get(attribute, '').strip())
                                    elif hasattr(el, 'text') and el.text:
                                        xml_all.append(el.text.strip())
                                    else:
                                        xml_all.append('')
                                if xml_all:
                                    xml_value = xml_all[0]
                except Exception:
                    pass

            if is_ambiguous:
                status = 'AMBIGU'
                regles_testees = []
                details_erreurs = [f"Plusieurs valeurs pour '{rdi_field_name}' dans le RDI."]
            else:
                status, regles_testees, details_erreurs = perform_controls(field, rdi_value, xml_value, type_controle)

            categorie_bg_raw = field.get('categorie_bg', 'BG-OTHER')
            categorie_titre_raw = field.get('categorie_titre', 'Autres')
            categorie_bg, categorie_titre = normalize_category(categorie_bg_raw, categorie_titre_raw)

            ceg_details = []
            for c in field.get('controles_cegedim', []):
                ceg_details.append({
                    'ref': c.get('ref', ''), 'categorie': c.get('categorie', ''),
                    'nature': c.get('nature', ''),
                    'description': c.get('description', c.get('controle', '')),
                    'message': c.get('message', '')
                })

            results.append({
                'balise': field.get('balise', ''), 'libelle': field.get('libelle', ''),
                'rdi': rdi_value, 'xml': xml_value, 'xml_all': xml_all,
                'rdi_field': rdi_field_name,
                'xml_short_name': get_xml_short_name(field.get('xpath', '')),
                'xml_tag_name': get_xml_tag_name(field.get('xpath', '')),
                'status': status, 'regles_testees': regles_testees,
                'details_erreurs': details_erreurs, 'rule_details': {},
                'controles_cegedim': ceg_details,
                'categorie_bg': categorie_bg, 'categorie_titre': categorie_titre,
                'obligatoire': field.get('obligatoire', 'Non'), 'order_index': index
            })

        # Articles
        def _get_rdi_art_line_id(rdi_art):
            for k, v in rdi_art.items():
                if 'BT126' in k:
                    return v.strip().lstrip('0') or '0'
            return ''

        def _get_rdi_art_name(rdi_art):
            for k, v in rdi_art.items():
                if 'BT153' in k:
                    return v.strip()
            return ''

        xml_by_line_id = {}
        for xi, xa in enumerate(xml_articles):
            lid = xa.get('BT-126', '').strip().lstrip('0') or '0'
            xml_by_line_id.setdefault(lid, []).append((xi, xa))

        matched_pairs = []
        xml_used = set()
        for rdi_art in rdi_articles:
            rdi_lid = _get_rdi_art_line_id(rdi_art)
            xml_art = {}
            if rdi_lid in xml_by_line_id:
                for xi, xa in xml_by_line_id[rdi_lid]:
                    if xi not in xml_used:
                        xml_art = xa
                        xml_used.add(xi)
                        break
            matched_pairs.append((rdi_art, xml_art, rdi_lid))
        for xi, xa in enumerate(xml_articles):
            if xi not in xml_used:
                xml_lid = xa.get('BT-126', '').strip().lstrip('0') or '0'
                matched_pairs.append(({}, xa, xml_lid))

        nb_articles = len(matched_pairs)
        articles_results = []
        for art_idx, (rdi_art, xml_art, line_id) in enumerate(matched_pairs):
            display_line_id = line_id or str(art_idx + 1)
            article_name = _get_rdi_art_name(rdi_art) or xml_art.get('BT-153', '').strip() or ''
            for field in article_fields:
                rdi_field_name = field.get('rdi', '')
                rdi_value = ''
                if rdi_art:
                    rdi_value = rdi_art.get(rdi_field_name, '').strip()
                    if not rdi_value and rdi_field_name:
                        for key in rdi_art.keys():
                            if key.upper() == rdi_field_name.upper():
                                rdi_value = rdi_art[key].strip()
                                break
                xml_value = xml_art.get(field.get('balise', ''), '').strip()
                status, regles_testees, details_erreurs = perform_controls(field, rdi_value, xml_value, type_controle)
                articles_results.append({
                    'balise': field.get('balise', ''), 'libelle': field.get('libelle', ''),
                    'rdi': rdi_value, 'xml': xml_value, 'rdi_field': rdi_field_name,
                    'xml_short_name': get_xml_short_name(field.get('xpath', '')),
                    'xml_tag_name': get_xml_tag_name(field.get('xpath', '')),
                    'status': status, 'regles_testees': regles_testees,
                    'details_erreurs': details_erreurs, 'rule_details': {},
                    'controles_cegedim': [],
                    'categorie_bg': 'BG-LIGNES', 'categorie_titre': '📋 LIGNES DE FACTURE',
                    'obligatoire': field.get('obligatoire', 'Non'),
                    'order_index': 1000 + art_idx * 100 + article_fields.index(field),
                    'article_index': art_idx, 'article_line_id': display_line_id,
                    'article_name': article_name,
                })
        results.extend(articles_results)
        results = apply_business_rules(results, type_formulaire)

        stats = {
            'total': len(results),
            'ok': sum(1 for r in results if r['status'] == 'OK'),
            'erreur': sum(1 for r in results if r['status'] == 'ERREUR'),
            'ignore': sum(1 for r in results if r['status'] == 'IGNORE'),
            'ambigu': sum(1 for r in results if r['status'] == 'AMBIGU'),
            'nb_articles': nb_articles,
        }

        categories_results = defaultdict(lambda: {'champs': [], 'stats': {'total': 0, 'ok': 0, 'erreur': 0}})
        for result in results:
            bg_id = result['categorie_bg']
            categories_results[bg_id]['champs'].append(result)
            categories_results[bg_id]['titre'] = result['categorie_titre']
            categories_results[bg_id]['stats']['total'] += 1
            if result['status'] == 'OK':
                categories_results[bg_id]['stats']['ok'] += 1
            elif result['status'] == 'ERREUR':
                categories_results[bg_id]['stats']['erreur'] += 1
        for bg_id in categories_results:
            categories_results[bg_id]['champs'].sort(key=lambda x: x.get('order_index', 9999))

        return {
            'results': results,
            'stats': stats,
            'categories_results': dict(categories_results),
            'type_controle': type_controle
        }, None

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, str(e)


@app.route('/controle-batch', methods=['POST'])
def controle_batch():
    try:
        type_formulaire = request.form.get('type_formulaire', 'simple')
        type_controle = request.form.get('type_controle', 'xml')
        pair_count = int(request.form.get('pair_count', 0))

        batch_results = []
        saved_paths = []

        for i in range(pair_count):
            pdf_file = request.files.get(f'pdf_{i}')
            rdi_file = request.files.get(f'rdi_{i}')
            name = request.form.get(f'name_{i}', f'Facture {i + 1}')
            invoice_number_hint = request.form.get(f'invoice_number_{i}', '')

            pdf_path = None
            rdi_path = None

            if pdf_file:
                pdf_path = os.path.join(UPLOAD_FOLDER, f'batch_{i}_{pdf_file.filename}')
                pdf_file.save(pdf_path)
                saved_paths.append(pdf_path)
            if rdi_file:
                rdi_path = os.path.join(UPLOAD_FOLDER, f'batch_{i}_{rdi_file.filename}')
                rdi_file.save(rdi_path)
                saved_paths.append(rdi_path)

            result, error = _process_invoice(rdi_path, pdf_path, None, type_formulaire, type_controle)

            if error:
                batch_results.append({'name': name, 'error': error,
                                       'stats': None, 'results': None,
                                       'categories_results': None,
                                       'type_controle': type_controle})
                _log_invoice_to_history(
                    type_formulaire, type_controle, 'batch',
                    invoice_number=invoice_number_hint or None, filename=name,
                    stats=None, results=None, error=error
                )
            else:
                result['name'] = name
                result['invoice_number'] = invoice_number_hint or None
                batch_results.append(result)
                # Détecte le N° de facture dans les résultats si non fourni
                inv_num = invoice_number_hint or None
                if not inv_num:
                    for r in result.get('results', []):
                        if r.get('balise') == 'BT-1':
                            inv_num = (r.get('rdi') or r.get('xml') or '').strip() or None
                            break
                _log_invoice_to_history(
                    type_formulaire, type_controle, 'batch',
                    invoice_number=inv_num, filename=name,
                    stats=result.get('stats'), results=result.get('results')
                )

        for p in saved_paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

        return jsonify({'batch': batch_results})

    except Exception as e:
        print(f"ERREUR batch: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/controle', methods=['POST'])
def controle():
    try:
        pdf_file = request.files.get('pdf')
        rdi_file = request.files.get('rdi')
        cii_file = request.files.get('cii')
        type_formulaire = request.form.get('type_formulaire', 'simple')
        type_controle = request.form.get('type_controle', 'xml')

        print(f"Controle: {type_formulaire}, {type_controle}")

        # Validation selon le mode
        if type_controle == 'cii':
            if not cii_file:
                return jsonify({'error': 'Fichier XML CII manquant'}), 400
        elif type_controle == 'xmlonly':
            if not pdf_file:
                return jsonify({'error': 'Fichier PDF manquant'}), 400
        else:
            if not rdi_file:
                return jsonify({'error': 'Fichier RDI manquant'}), 400
            if type_controle == 'xml' and not pdf_file:
                return jsonify({'error': 'Fichier PDF/XML manquant pour le mode XML'}), 400

        # Lecture du RDI (pas nécessaire en mode CII)
        rdi_data = {}
        rdi_articles = []
        rdi_multi = {}
        rdi_path = None
        if rdi_file:
            rdi_path = os.path.join(UPLOAD_FOLDER, rdi_file.filename)
            rdi_file.save(rdi_path)
            rdi_data, rdi_articles, rdi_multi = parse_rdi(rdi_path)
            print("==== rdi_data ====")
            print(rdi_data)
            print(f"==== rdi_articles ({len(rdi_articles)} articles) ====")
            for i, art in enumerate(rdi_articles):
                print(f"  Article {i}: {art}")

        xml_doc = None
        pdf_path = None
        cii_path = None

        if type_controle == 'cii' and cii_file:
            # Mode CII : lire le XML directement
            cii_path = os.path.join(UPLOAD_FOLDER, cii_file.filename)
            cii_file.save(cii_path)
            with open(cii_path, 'r', encoding='utf-8') as f:
                xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except:
                return jsonify({'error': 'XML CII invalide'}), 400

        elif type_controle == 'xmlonly' and pdf_file:
            # Mode XML only : extraire le XML du PDF et contrôler uniquement le XML
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_file.filename)
            pdf_file.save(pdf_path)
            if pdf_path.lower().endswith('.pdf'):
                xml_content = extract_xml_from_pdf(pdf_path)
                if not xml_content:
                    return jsonify({'error': 'XML introuvable dans le PDF'}), 400
            else:
                with open(pdf_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except:
                return jsonify({'error': 'XML invalide'}), 400

        elif type_controle == 'xml' and pdf_file:
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_file.filename)
            pdf_file.save(pdf_path)
            if pdf_path.lower().endswith('.pdf'):
                xml_content = extract_xml_from_pdf(pdf_path)
                if not xml_content:
                    return jsonify({'error': 'XML introuvable dans le PDF'}), 400
            else:
                with open(pdf_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except:
                return jsonify({'error': 'XML invalide'}), 400

        mapping_data = load_mapping(type_formulaire)
        if not mapping_data:
            return jsonify({'error': 'Mapping introuvable'}), 500

        mapping = mapping_data.get('champs', [])
        namespaces = build_xml_namespaces(xml_doc)

        # Pré-compiler les XPath pour accélérer le traitement
        xpath_cache = {}
        if xml_doc is not None:
            for field in mapping:
                _xpath_raw = field.get('xpath', '') or ''
                if _xpath_raw and _xpath_raw not in xpath_cache:
                    _xpath = _xpath_raw if _xpath_raw.startswith('/') else '//' + _xpath_raw
                    try:
                        xpath_cache[_xpath_raw] = etree.XPath(_xpath, namespaces=namespaces)
                    except:
                        xpath_cache[_xpath_raw] = None

        # Extraire les articles XML (IncludedSupplyChainTradeLineItem)
        xml_articles = []
        if xml_doc is not None:
            try:
                line_items_xpath = etree.XPath(
                    '/rsm:CrossIndustryInvoice/rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem',
                    namespaces=namespaces)
                xml_line_items = line_items_xpath(xml_doc)
                for item in xml_line_items:
                    xml_art = {}
                    # Pour chaque champ article du mapping, extraire la valeur de cet item XML
                    for field in mapping:
                        if not field.get('is_article'):
                            continue
                        _xpath_raw = field.get('xpath', '') or ''
                        if not _xpath_raw:
                            continue
                        # Convertir le XPath absolu en relatif à l'item
                        rel_xpath = _xpath_raw
                        # Retirer le préfixe jusqu'à IncludedSupplyChainTradeLineItem
                        marker = 'ram:IncludedSupplyChainTradeLineItem/'
                        idx = rel_xpath.find(marker)
                        if idx >= 0:
                            rel_xpath = './' + rel_xpath[idx + len(marker):]
                        else:
                            continue
                        try:
                            compiled_rel = etree.XPath(rel_xpath, namespaces=namespaces)
                            elements = compiled_rel(item)
                            if elements:
                                attribute = field.get('attribute')
                                if attribute and hasattr(elements[0], 'get'):
                                    xml_art[field['balise']] = elements[0].get(attribute, '').strip()
                                elif hasattr(elements[0], 'text') and elements[0].text:
                                    xml_art[field['balise']] = elements[0].text.strip()
                        except:
                            pass
                    xml_articles.append(xml_art)
                print(f"==== xml_articles ({len(xml_articles)} articles) ====")
                for i, art in enumerate(xml_articles):
                    print(f"  XML Art {i}: {art}")
            except Exception as e:
                print(f"Erreur extraction articles XML: {e}")

        # Séparer les champs articles des champs non-articles dans le mapping
        article_fields = [f for f in mapping if f.get('is_article')]
        header_fields = [f for f in mapping if not f.get('is_article')]

        results = []

        # 1. Traiter les champs d'en-tête (non-articles) normalement
        for index, field in enumerate(header_fields):
            rdi_field_name = field.get('rdi', '')
            type_enreg = (field.get('type_enregistrement') or '').strip().upper()
            is_ambiguous = False
            rdi_value = ''

            if rdi_field_name:
                field_upper = rdi_field_name.upper()
                occurrences = rdi_multi.get(field_upper, [])
                if type_enreg:
                    # Filtrer par type d'enregistrement demandé
                    matches = [v for rt, v in occurrences if rt.upper() == type_enreg]
                    rdi_value = matches[0].strip() if matches else ''
                elif len(occurrences) > 1:
                    # Plusieurs valeurs sans filtre → ambiguïté
                    is_ambiguous = True
                else:
                    # Cas normal : 0 ou 1 occurrence
                    rdi_value = rdi_data.get(rdi_field_name, '').strip()
                    if not rdi_value:
                        for key in rdi_data.keys():
                            if key.upper() == field_upper:
                                rdi_value = rdi_data[key].strip()
                                break

            xml_value = ''
            xml_all = []
            if xml_doc is not None:
                try:
                    _xpath_raw = field.get('xpath', '') or ''
                    if _xpath_raw:
                        compiled = xpath_cache.get(_xpath_raw)
                        if compiled is not None:
                            elements = compiled(xml_doc)
                            if elements:
                                attribute = field.get('attribute')
                                for el in elements:
                                    if attribute and hasattr(el, 'get'):
                                        xml_all.append(el.get(attribute, '').strip())
                                    elif hasattr(el, 'text') and el.text:
                                        xml_all.append(el.text.strip())
                                    else:
                                        xml_all.append('')
                                if xml_all:
                                    xml_value = xml_all[0]
                except:
                    pass

            if is_ambiguous:
                status = 'AMBIGU'
                regles_testees = []
                details_erreurs = [f"Plusieurs valeurs trouvées pour '{rdi_field_name}' dans le RDI. Précisez le type d'enregistrement dans le mapping."]
            else:
                status, regles_testees, details_erreurs = perform_controls(field, rdi_value, xml_value, type_controle)
            xml_short_name = get_xml_short_name(field.get('xpath', ''))
            xml_tag_name = get_xml_tag_name(field.get('xpath', ''))

            categorie_bg_raw = field.get('categorie_bg', 'BG-OTHER')
            categorie_titre_raw = field.get('categorie_titre', 'Autres')
            categorie_bg, categorie_titre = normalize_category(categorie_bg_raw, categorie_titre_raw)

            ceg_details = []
            for c in field.get('controles_cegedim', []):
                ceg_details.append({
                    'ref': c.get('ref', ''),
                    'categorie': c.get('categorie', ''),
                    'nature': c.get('nature', ''),
                    'description': c.get('description', c.get('controle', '')),
                    'message': c.get('message', '')
                })

            results.append({
                'balise': field.get('balise', ''),
                'libelle': field.get('libelle', ''),
                'rdi': rdi_value,
                'xml': xml_value,
                'xml_all': xml_all,
                'rdi_field': rdi_field_name,
                'xml_short_name': xml_short_name,
                'xml_tag_name': xml_tag_name,
                'status': status,
                'regles_testees': regles_testees,
                'details_erreurs': details_erreurs,
                'rule_details': {},
                'controles_cegedim': ceg_details,
                'categorie_bg': categorie_bg,
                'categorie_titre': categorie_titre,
                'obligatoire': field.get('obligatoire', 'Non'),
                'order_index': index
            })

        # 2. Traiter les articles (blocs répétitifs)
        # Rapprocher les articles RDI et XML par BT-126 (numéro de ligne)
        # Un même BT-126 peut apparaître plusieurs fois (ex: régularisation multi-périodes)

        def get_rdi_art_line_id(rdi_art):
            for k, v in rdi_art.items():
                if 'BT126' in k:
                    return v.strip().lstrip('0') or '0'
            return ''

        def get_rdi_art_name(rdi_art):
            for k, v in rdi_art.items():
                if 'BT153' in k:
                    return v.strip()
            return ''

        # Construire la liste de paires (rdi_art, xml_art) rapprochées par BT-126
        # Pour les articles ayant le même BT-126 (multi-périodes), on les matche dans l'ordre
        matched_pairs = []
        xml_used = set()

        # Index des articles XML par line ID (numérique, sans zéros)
        xml_by_line_id = {}
        for xi, xa in enumerate(xml_articles):
            lid = xa.get('BT-126', '').strip().lstrip('0') or '0'
            xml_by_line_id.setdefault(lid, []).append((xi, xa))

        for _, rdi_art in enumerate(rdi_articles):
            rdi_lid = get_rdi_art_line_id(rdi_art)
            # Chercher un article XML avec le même BT-126 non encore utilisé
            xml_art = {}
            if rdi_lid in xml_by_line_id:
                for xi, xa in xml_by_line_id[rdi_lid]:
                    if xi not in xml_used:
                        xml_art = xa
                        xml_used.add(xi)
                        break
            matched_pairs.append((rdi_art, xml_art, rdi_lid))

        # Ajouter les articles XML qui n'ont pas de correspondance RDI
        for xi, xa in enumerate(xml_articles):
            if xi not in xml_used:
                xml_lid = xa.get('BT-126', '').strip().lstrip('0') or '0'
                matched_pairs.append(({}, xa, xml_lid))

        nb_articles = len(matched_pairs)

        articles_results = []
        for art_idx, (rdi_art, xml_art, line_id) in enumerate(matched_pairs):
            display_line_id = line_id or str(art_idx + 1)
            article_name = get_rdi_art_name(rdi_art) or xml_art.get('BT-153', '').strip() or ''

            for field in article_fields:
                rdi_field_name = field.get('rdi', '')
                rdi_value = ''
                if rdi_art:
                    rdi_value = rdi_art.get(rdi_field_name, '').strip()
                    if not rdi_value and rdi_field_name:
                        for key in rdi_art.keys():
                            if key.upper() == rdi_field_name.upper():
                                rdi_value = rdi_art[key].strip()
                                break

                xml_value = xml_art.get(field.get('balise', ''), '').strip()

                status, regles_testees, details_erreurs = perform_controls(field, rdi_value, xml_value, type_controle)
                xml_short_name = get_xml_short_name(field.get('xpath', ''))
                xml_tag_name = get_xml_tag_name(field.get('xpath', ''))

                articles_results.append({
                    'balise': field.get('balise', ''),
                    'libelle': field.get('libelle', ''),
                    'rdi': rdi_value,
                    'xml': xml_value,
                    'rdi_field': rdi_field_name,
                    'xml_short_name': xml_short_name,
                    'xml_tag_name': xml_tag_name,
                    'status': status,
                    'regles_testees': regles_testees,
                    'details_erreurs': details_erreurs,
                    'rule_details': {},
                    'controles_cegedim': [],
                    'categorie_bg': 'BG-LIGNES',
                    'categorie_titre': '📋 LIGNES DE FACTURE',
                    'obligatoire': field.get('obligatoire', 'Non'),
                    'order_index': 1000 + art_idx * 100 + article_fields.index(field),
                    'article_index': art_idx,
                    'article_line_id': display_line_id,
                    'article_name': article_name,
                })

        results.extend(articles_results)

        # Appliquer les règles métiers configurables
        results = apply_business_rules(results, type_formulaire)

        # Validation schematron officielle EN16931 CII (en plus des contrôles BT)
        # — si on a un XML CII (modes 'xml', 'xmlonly', 'cii') on l'utilise tel quel,
        # — sinon (mode RDI seul) on reconstruit un XML synthétique depuis le mapping.
        # Toggle global : business_rules.schematron_enabled (défaut True)
        _global_settings = load_business_rules() or {}
        _schematron_on = _global_settings.get('schematron_enabled', True)
        if not _schematron_on:
            schematron_summary = {
                'skipped': True,
                'reason': 'Validation schématron désactivée dans les paramètres globaux.',
                'total': 0, 'fatal': 0, 'warning': 0, 'matched': 0,
                'rules': [], 'errors': [], 'orphans': [],
            }
        elif xml_doc is not None:
            schematron_summary = apply_schematron(xml_content, results)
        elif rdi_data or rdi_articles:
            synthetic_xml = build_cii_xml(rdi_data, rdi_articles, mapping)
            if synthetic_xml:
                schematron_summary = apply_schematron(synthetic_xml, results)
                if schematron_summary:
                    schematron_summary['synthetic'] = True
                    schematron_summary['note'] = (
                        'Aucun XML CII fourni : la validation tourne sur un XML '
                        'reconstruit depuis le RDI via le mapping. Les attributs CII '
                        'que le RDI ne porte pas (schemeID, etc.) peuvent générer '
                        'de faux positifs.'
                    )
            else:
                schematron_summary = {
                    'skipped': True,
                    'reason': 'Impossible de reconstruire un XML depuis ce RDI.',
                    'total': 0, 'fatal': 0, 'warning': 0, 'matched': 0,
                    'rules': [], 'errors': [], 'orphans': [],
                }
        else:
            schematron_summary = {
                'skipped': True,
                'reason': 'Aucune donnée RDI ni XML pour la validation EN16931.',
                'total': 0, 'fatal': 0, 'warning': 0, 'matched': 0,
                'rules': [], 'errors': [], 'orphans': [],
            }

        stats = {
            'total': len(results),
            'ok': sum(1 for r in results if r['status'] == 'OK'),
            'erreur': sum(1 for r in results if r['status'] == 'ERREUR'),
            'ignore': sum(1 for r in results if r['status'] == 'IGNORE'),
            'ambigu': sum(1 for r in results if r['status'] == 'AMBIGU'),
            'nb_articles': nb_articles,
        }
        if schematron_summary:
            stats['schematron_total'] = schematron_summary.get('total', 0)
            stats['schematron_fatal'] = schematron_summary.get('fatal', 0)

        categories_results = defaultdict(lambda: {'champs': [], 'stats': {'total': 0, 'ok': 0, 'erreur': 0}})
        for result in results:
            bg_id = result['categorie_bg']
            categories_results[bg_id]['champs'].append(result)
            categories_results[bg_id]['titre'] = result['categorie_titre']
            categories_results[bg_id]['stats']['total'] += 1
            if result['status'] == 'OK':
                categories_results[bg_id]['stats']['ok'] += 1
            elif result['status'] == 'ERREUR':
                categories_results[bg_id]['stats']['erreur'] += 1

        # Trier les champs dans chaque catégorie selon l'ordre du mapping (order_index)
        for bg_id in categories_results:
            categories_results[bg_id]['champs'].sort(key=lambda x: x.get('order_index', 9999))

        # Nettoyage
        if rdi_path and os.path.exists(rdi_path):
            os.remove(rdi_path)
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        if cii_path and os.path.exists(cii_path):
            os.remove(cii_path)

        # Log dans l'historique (statistiques)
        inv_num = None
        for r in results:
            if r.get('balise') == 'BT-1':
                inv_num = (r.get('rdi') or r.get('xml') or '').strip() or None
                break
        src_filename = None
        if pdf_file is not None:
            src_filename = pdf_file.filename
        elif rdi_file is not None:
            src_filename = rdi_file.filename
        elif cii_file is not None:
            src_filename = cii_file.filename
        _log_invoice_to_history(
            type_formulaire, type_controle, 'unitaire',
            invoice_number=inv_num, filename=src_filename,
            stats=stats, results=results
        )

        return jsonify({
            'results': results,
            'stats': stats,
            'categories_results': dict(categories_results),
            'type_controle': type_controle,
            'schematron': schematron_summary,
        })
    except Exception as e:
        print(f"ERREUR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/batch-preview', methods=['POST'])
def api_batch_preview():
    """Lit le numéro de facture (BT-1) depuis un fichier RDI ou PDF/XML.
    Accepte type_formulaire pour trouver la clé RDI exacte de BT-1.
    Retourne {filename, invoice_number, type}."""
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Pas de fichier'}), 400
    type_formulaire = request.form.get('type_formulaire', 'simple')
    fname = file.filename
    ext = fname.lower().rsplit('.', 1)[-1] if '.' in fname else ''
    tmp_path = os.path.join(UPLOAD_FOLDER, f'preview_{fname}')
    file.save(tmp_path)
    invoice_number = None
    file_type = 'pdf'
    try:
        if ext in ('txt', 'rdi'):
            file_type = 'rdi'
            rdi_data, _, _ = parse_rdi(tmp_path)
            # Trouver la clé RDI de BT-1 via le mapping actif
            bt1_rdi_key = None
            mapping_data = load_mapping(type_formulaire)
            if mapping_data:
                for field in mapping_data.get('champs', []):
                    if field.get('balise') == 'BT-1':
                        bt1_rdi_key = field.get('rdi', '')
                        break
            if bt1_rdi_key:
                # Recherche insensible à la casse
                for key, val in rdi_data.items():
                    if key.upper() == bt1_rdi_key.upper():
                        invoice_number = val.strip()
                        break
            # Fallback : clés courantes si mapping introuvable
            if not invoice_number:
                for fallback in ('WNUM_FACT', 'GS_CHORUS_MD-INVOICE-NUMBER', 'NUM_FACTURE'):
                    if fallback in rdi_data and rdi_data[fallback].strip():
                        invoice_number = rdi_data[fallback].strip()
                        break
        elif ext in ('pdf', 'xml'):
            file_type = 'pdf'
            if ext == 'pdf':
                xml_content = extract_xml_from_pdf(tmp_path)
            else:
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
            if xml_content:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
                ns = {
                    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
                    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
                }
                els = xml_doc.xpath('/rsm:CrossIndustryInvoice/rsm:ExchangedDocument/ram:ID', namespaces=ns)
                if els and els[0].text:
                    invoice_number = els[0].text.strip()
    except Exception as e:
        print(f'[batch-preview] Erreur: {e}')
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
    return jsonify({'filename': fname, 'invoice_number': invoice_number, 'type': file_type})


@app.route('/api/extract-xml', methods=['POST'])
def api_extract_xml():
    """Extrait le XML embarqué dans un PDF et le renvoie en téléchargement"""
    pdf_file = request.files.get('pdf')
    if not pdf_file:
        return jsonify({'error': 'Fichier PDF manquant'}), 400
    pdf_path = os.path.join(UPLOAD_FOLDER, pdf_file.filename)
    pdf_file.save(pdf_path)
    try:
        xml_content = extract_xml_from_pdf(pdf_path)
        if not xml_content:
            return jsonify({'error': 'Aucun XML trouvé dans ce PDF'}), 400
        # Nom du fichier XML basé sur le nom du PDF
        xml_filename = os.path.splitext(pdf_file.filename)[0] + '.xml'
        xml_bytes = xml_content.encode('utf-8') if isinstance(xml_content, str) else xml_content
        return send_file(
            io.BytesIO(xml_bytes),
            mimetype='application/xml',
            as_attachment=True,
            download_name=xml_filename
        )
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

# ===== NOUVELLES ROUTES API POUR GESTION DES MAPPINGS =====

@app.route('/api/mappings/index', methods=['GET'])
def api_get_mappings_index():
    """Retourne la liste de tous les mappings"""
    return jsonify(load_mappings_index())

@app.route('/api/mappings/options', methods=['GET'])
def api_get_mappings_options():
    """Retourne les options de mapping pour les listes déroulantes"""
    index = load_mappings_index()
    mappings = index.get('mappings', [])
    
    # Grouper par type
    options = {
        'CART Simple': [],
        'CART Groupée': [],
        'Ventes Diverses': []
    }
    
    for mapping in mappings:
        mapping_type = mapping.get('type', 'CART Simple')
        if mapping_type in options:
            options[mapping_type].append({
                'id': mapping['id'],
                'name': mapping['name'],
                'filename': mapping['filename'],
                'is_default': mapping.get('is_default', False)
            })
    
    return jsonify(options)

@app.route('/api/mappings/create', methods=['POST'])
def api_create_mapping():
    """Crée un nouveau mapping"""
    try:
        from datetime import datetime
        import uuid

        data = request.json
        name = data.get('name')
        copy_from = data.get('copy_from', None)  # ID du mapping source (optionnel)

        if not name:
            return jsonify({'success': False, 'error': 'Nom requis'})

        new_id   = str(uuid.uuid4())[:8]
        created  = datetime.now().strftime('%Y-%m-%d')

        new_mapping = {
            "id":           new_id,
            "name":         name,
            "type":         "",
            "filename":     f"mapping_custom_{new_id}.json",
            "created_date": created,
            "is_default":   False
        }

        # Insérer le nouveau mapping
        conn = get_db()
        conn.execute(
            "INSERT INTO mappings (id, name, type, filename, created_date, is_default) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (new_id, name, "", new_mapping['filename'], created)
        )

        # Copier les champs depuis le mapping source si demandé
        if copy_from:
            src_rows = conn.execute(
                "SELECT * FROM mapping_champs WHERE mapping_id=? ORDER BY position",
                (copy_from,)
            ).fetchall()
            for row in src_rows:
                champ = _row_to_champ(row)
                conn.execute(_CHAMP_INSERT_SQL, _champ_to_row(new_id, row['position'], champ))

        conn.commit()
        conn.close()

        return jsonify({'success': True, 'mapping': new_mapping})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/mappings/delete', methods=['POST'])
def api_delete_mapping():
    """Supprime un mapping"""
    try:
        data = request.json
        mapping_id = data.get('id')

        if not mapping_id:
            return jsonify({'success': False, 'error': 'ID requis'})

        conn = get_db()
        row = conn.execute(
            "SELECT is_default FROM mappings WHERE id = ?", (mapping_id,)
        ).fetchone()

        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'Mapping non trouvé'})

        if row['is_default']:
            conn.close()
            return jsonify({'success': False, 'error': 'Impossible de supprimer un mapping par défaut'})

        # La suppression en cascade efface aussi mapping_content et mapping_versions
        conn.execute("DELETE FROM mappings WHERE id = ?", (mapping_id,))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/remove-signature', methods=['POST'])
def api_remove_signature():
    """Supprime les signatures numériques d'un PDF et renvoie le PDF reconstruit."""
    pdf_file = request.files.get('pdf')
    if not pdf_file:
        return jsonify({'error': 'Fichier PDF manquant'}), 400
    pdf_path = os.path.join(UPLOAD_FOLDER, pdf_file.filename)
    pdf_file.save(pdf_path)
    try:
        output = remove_pdf_signature(pdf_path)
        out_filename = os.path.splitext(pdf_file.filename)[0] + '_unsigned.pdf'
        return send_file(
            output,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=out_filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

# ===== FIN NOUVELLES ROUTES API =====

# ===== ROUTES API STATISTIQUES =====

def _stats_build_filters(args):
    """Construit la clause WHERE et la liste de paramètres à partir des query string."""
    clauses = []
    params = []
    type_f = (args.get('type') or '').strip()
    if type_f and type_f != 'all':
        clauses.append("type_formulaire = ?")
        params.append(type_f)
    mode = (args.get('mode') or '').strip()
    if mode and mode in ('unitaire', 'batch'):
        clauses.append("mode = ?")
        params.append(mode)
    start = (args.get('start') or '').strip()
    if start:
        clauses.append("substr(timestamp,1,10) >= ?")
        params.append(start)
    end = (args.get('end') or '').strip()
    if end:
        clauses.append("substr(timestamp,1,10) <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@app.route('/api/stats/summary', methods=['GET'])
def api_stats_summary():
    """Compteurs globaux : nb factures, taux moyen, ventilations par type / mode."""
    try:
        where, params = _stats_build_filters(request.args)
        conn = get_db()
        # Total + moyenne
        row = conn.execute(
            f"SELECT COUNT(*) AS n, "
            f"       AVG(conformity_pct) AS pct, "
            f"       SUM(CASE WHEN (error IS NOT NULL AND error <> '') OR erreur > 0 THEN 1 ELSE 0 END) AS nb_errors "
            f"FROM invoice_history{where}",
            params
        ).fetchone()
        total_invoices = row['n'] or 0
        avg_pct = round(row['pct'] or 0, 2)
        nb_errors = row['nb_errors'] or 0

        # Ventilation par type
        by_type_rows = conn.execute(
            f"SELECT type_formulaire AS k, COUNT(*) AS n, "
            f"       AVG(conformity_pct) AS pct "
            f"FROM invoice_history{where} GROUP BY type_formulaire ORDER BY n DESC",
            params
        ).fetchall()
        by_type = [
            {'type': r['k'] or '', 'count': r['n'], 'avg_pct': round(r['pct'] or 0, 2)}
            for r in by_type_rows
        ]

        # Ventilation par mode
        by_mode_rows = conn.execute(
            f"SELECT mode AS k, COUNT(*) AS n, AVG(conformity_pct) AS pct "
            f"FROM invoice_history{where} GROUP BY mode",
            params
        ).fetchall()
        by_mode = [
            {'mode': r['k'] or '', 'count': r['n'], 'avg_pct': round(r['pct'] or 0, 2)}
            for r in by_mode_rows
        ]

        # Ventilation type x mode
        by_type_mode_rows = conn.execute(
            f"SELECT type_formulaire AS t, mode AS m, COUNT(*) AS n "
            f"FROM invoice_history{where} GROUP BY type_formulaire, mode",
            params
        ).fetchall()
        by_type_mode = [
            {'type': r['t'] or '', 'mode': r['m'] or '', 'count': r['n']}
            for r in by_type_mode_rows
        ]

        # Bornes des dates pour le filtre par défaut
        bounds = conn.execute(
            "SELECT MIN(substr(timestamp,1,10)) AS dmin, "
            "       MAX(substr(timestamp,1,10)) AS dmax FROM invoice_history"
        ).fetchone()

        conn.close()
        return jsonify({
            'total_invoices': total_invoices,
            'avg_conformity_pct': avg_pct,
            'nb_errors': nb_errors,
            'by_type': by_type,
            'by_mode': by_mode,
            'by_type_mode': by_type_mode,
            'date_min': bounds['dmin'],
            'date_max': bounds['dmax'],
        })
    except Exception as e:
        print(f"[STATS] summary erreur : {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/conformity-trend', methods=['GET'])
def api_stats_conformity_trend():
    """Série temporelle du taux de conformité moyen, par jour, ventilée par type."""
    try:
        where, params = _stats_build_filters(request.args)
        conn = get_db()
        rows = conn.execute(
            f"SELECT substr(timestamp,1,10) AS d, type_formulaire AS t, "
            f"       AVG(conformity_pct) AS pct, COUNT(*) AS n "
            f"FROM invoice_history{where} "
            f"GROUP BY d, t ORDER BY d ASC",
            params
        ).fetchall()
        conn.close()
        # Regrouper par type
        series = {}
        all_dates = set()
        for r in rows:
            t = r['t'] or 'inconnu'
            d = r['d'] or ''
            series.setdefault(t, {})[d] = {
                'pct': round(r['pct'] or 0, 2),
                'count': r['n']
            }
            all_dates.add(d)
        ordered_dates = sorted(all_dates)
        result = {
            'dates': ordered_dates,
            'series': [
                {
                    'type': t,
                    'points': [
                        {
                            'date': d,
                            'pct': series[t].get(d, {}).get('pct'),
                            'count': series[t].get(d, {}).get('count', 0)
                        } for d in ordered_dates
                    ]
                } for t in sorted(series.keys())
            ]
        }
        return jsonify(result)
    except Exception as e:
        print(f"[STATS] trend erreur : {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/top-ko', methods=['GET'])
def api_stats_top_ko():
    """Top des champs qui tombent le plus souvent en KO, ventilé par type."""
    try:
        # Filtres : type_formulaire / start / end / mode (le mode requiert un join)
        clauses = []
        params = []
        type_f = (request.args.get('type') or '').strip()
        if type_f and type_f != 'all':
            clauses.append("k.type_formulaire = ?")
            params.append(type_f)
        start = (request.args.get('start') or '').strip()
        if start:
            clauses.append("substr(k.timestamp,1,10) >= ?")
            params.append(start)
        end = (request.args.get('end') or '').strip()
        if end:
            clauses.append("substr(k.timestamp,1,10) <= ?")
            params.append(end)
        mode = (request.args.get('mode') or '').strip()
        join = ""
        if mode in ('unitaire', 'batch'):
            join = " JOIN invoice_history h ON h.id = k.invoice_history_id "
            clauses.append("h.mode = ?")
            params.append(mode)
        try:
            limit = int(request.args.get('limit', 10))
        except ValueError:
            limit = 10
        limit = max(1, min(limit, 100))

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = get_db()
        rows = conn.execute(
            f"SELECT k.balise AS balise, "
            f"       MAX(k.libelle) AS libelle, "
            f"       MAX(k.obligatoire) AS obligatoire, "
            f"       k.type_formulaire AS type_formulaire, "
            f"       SUM(CASE WHEN k.status='ERREUR' THEN 1 ELSE 0 END) AS nb_erreur, "
            f"       SUM(CASE WHEN k.status='AMBIGU' THEN 1 ELSE 0 END) AS nb_ambigu, "
            f"       COUNT(*) AS total "
            f"FROM invoice_field_ko k{join}{where} "
            f"GROUP BY k.balise, k.type_formulaire "
            f"ORDER BY total DESC, k.balise ASC "
            f"LIMIT ?",
            params + [limit]
        ).fetchall()
        conn.close()
        return jsonify({
            'items': [
                {
                    'balise': r['balise'],
                    'libelle': r['libelle'] or '',
                    'obligatoire': r['obligatoire'] or '',
                    'type_formulaire': r['type_formulaire'] or '',
                    'nb_erreur': r['nb_erreur'] or 0,
                    'nb_ambigu': r['nb_ambigu'] or 0,
                    'total': r['total'] or 0,
                } for r in rows
            ]
        })
    except Exception as e:
        print(f"[STATS] top-ko erreur : {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/history', methods=['GET'])
def api_stats_history():
    """Liste paginée des dernières factures contrôlées."""
    try:
        where, params = _stats_build_filters(request.args)
        try:
            limit = int(request.args.get('limit', 50))
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 500))
        conn = get_db()
        rows = conn.execute(
            f"SELECT id, timestamp, type_formulaire, type_controle, mode, "
            f"       invoice_number, filename, total, ok, erreur, "
            f"       ignore_count, ambigu, conformity_pct, error "
            f"FROM invoice_history{where} ORDER BY id DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        conn.close()
        return jsonify({
            'items': [dict(r) for r in rows]
        })
    except Exception as e:
        print(f"[STATS] history erreur : {e}")
        return jsonify({'error': str(e)}), 500


_STATS_BUILTIN_LABELS = {
    'simple':         'CART Simple',
    'CARTsimple':     'CART Simple',
    'groupee':        'CART Groupée',
    'flux':           'Flux Générique',
    'ventesdiverses': 'Ventes Diverses',
}


def _resolve_type_label(type_formulaire, names_by_id=None):
    """Convertit un type_formulaire stocké en libellé humain.
    - 'simple'/'groupee'/'flux'/'ventesdiverses'/'CARTsimple' : libellé fixe
    - 'custom_<id>' : nom du mapping correspondant en base, sinon fallback
    """
    if not type_formulaire:
        return 'inconnu'
    if type_formulaire in _STATS_BUILTIN_LABELS:
        return _STATS_BUILTIN_LABELS[type_formulaire]
    mapping_id = _get_mapping_id(type_formulaire)
    if names_by_id is None:
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT name FROM mappings WHERE id = ?", (mapping_id,)
            ).fetchone()
            conn.close()
            if row and row['name']:
                return row['name']
        except Exception:
            pass
    elif mapping_id in names_by_id:
        return names_by_id[mapping_id]
    return type_formulaire


@app.route('/api/stats/types', methods=['GET'])
def api_stats_types():
    """Liste les types de formulaires présents dans l'historique (pour le filtre)."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT type_formulaire FROM invoice_history "
            "WHERE type_formulaire <> '' ORDER BY type_formulaire"
        ).fetchall()
        names = {
            r['id']: r['name']
            for r in conn.execute("SELECT id, name FROM mappings").fetchall()
        }
        conn.close()
        types = [
            {'id': r['type_formulaire'],
             'label': _resolve_type_label(r['type_formulaire'], names)}
            for r in rows
        ]
        # Map id->label complet pour le front (alias inclus)
        labels = {t['id']: t['label'] for t in types}
        return jsonify({'types': types, 'labels': labels})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== FIN ROUTES API STATISTIQUES =====

init_db()

if __name__ == '__main__':
    print("="*60)
    print("APPLICATION FACTUR-X V12.0 - Enhanced Mapping Management")
    print("Ouvrez ce lien dans votre navigateur : http://localhost:5000")
    print("="*60)
    app.run(debug=True, host='0.0.0.0', port=5000)
