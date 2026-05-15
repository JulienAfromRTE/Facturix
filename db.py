"""Couche SQLite : init, migrations, mappings, business_rules, history, audit, versions.

Configurer DB_FILE et SCRIPT_DIR depuis l'application avant utilisation.
"""
import os
import json
import sqlite3

from default_rules import _DEFAULT_RULES, _RULE_CATEGORY_BY_ID
from default_mapping_reforme import _REFORME_CHAMPS

# Configures par app.py au demarrage
DB_FILE = None
SCRIPT_DIR = None


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
            error           TEXT,
            archive_rdi     TEXT,
            archive_pdf     TEXT,
            archive_cii     TEXT,
            archive_xml     TEXT
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
    for _col in ('archive_rdi', 'archive_pdf', 'archive_cii', 'archive_xml', 'results_json'):
        try:
            conn.execute(f"ALTER TABLE invoice_history ADD COLUMN {_col} TEXT")
            conn.commit()
        except Exception:
            pass  # colonne déjà présente
    try:
        conn.execute("ALTER TABLE mapping_champs ADD COLUMN type_enregistrement TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # colonne déjà présente
    _migrate_to_relational(conn)
    conn.commit()
    _seed_default_data(conn)
    _ensure_default_mappings_populated(conn)
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

    # Le mapping Réforme Factur-X est géré par _ensure_default_mappings_populated
    # (idempotent, s'adapte aux installations existantes)

def _ensure_default_mappings_populated(conn):
    """Injecte le mapping Réforme Factur-X s'il est absent ou vide.

    Idempotent : ne touche jamais aux mappings existants.
    S'exécute à chaque démarrage.
    """
    c = conn.cursor()

    exists = c.execute(
        "SELECT 1 FROM mappings WHERE id='default_reforme'"
    ).fetchone()
    if not exists:
        c.execute(
            "INSERT INTO mappings (id, name, type, filename, created_date, is_default) "
            "VALUES (?,?,?,?,?,1)",
            ("default_reforme", "Réforme Factur-X (EN16931)", "Réforme",
             "default_mapping_reforme.py", "2026-05-15")
        )
    count = c.execute(
        "SELECT COUNT(*) FROM mapping_champs WHERE mapping_id='default_reforme'"
    ).fetchone()[0]
    if count == 0:
        for pos, champ in enumerate(_REFORME_CHAMPS):
            c.execute(_CHAMP_INSERT_SQL, _champ_to_row("default_reforme", pos, champ))
        conn.commit()
        print(f"[DB] Mapping ajouté : Réforme Factur-X (EN16931) ({len(_REFORME_CHAMPS)} champs)")


# ── Statistiques : log d'une facture contrôlée ──────────────────────────────

def _log_invoice_to_history(type_formulaire, type_controle, mode,
                            invoice_number=None, filename=None,
                            stats=None, results=None, error=None,
                            results_json=None):
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
            " filename, total, ok, erreur, ignore_count, ambigu, conformity_pct, error, results_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, type_formulaire or '', type_controle or '', mode or 'unitaire',
             invoice_number or None, filename or None,
             total, ok, erreur, ign, amb, pct, error, results_json or None)
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
        return invoice_id
    except Exception as e:
        print(f"[STATS] Erreur log historique : {e}")
        return None

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
