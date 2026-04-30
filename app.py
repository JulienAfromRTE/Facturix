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

ARCHIVE_FOLDER = os.path.join(SCRIPT_DIR, 'archive_files')
os.makedirs(ARCHIVE_FOLDER, exist_ok=True)
ARCHIVE_KINDS = ('rdi', 'pdf', 'cii', 'xml')
ARCHIVE_RETENTION_DAYS = 7

DB_FILE = os.path.join(SCRIPT_DIR, 'facturix.db')

print(f"[FACTURX] Dossier de travail : {SCRIPT_DIR}")

from default_rules import _RULE_CATEGORIES_ORDER, _RULE_CATEGORY_BY_ID, _DEFAULT_RULES

import db
db.DB_FILE = DB_FILE
db.SCRIPT_DIR = SCRIPT_DIR
from db import (
    get_db, _get_mapping_id, init_db,
    load_mapping, save_mapping,
    save_mapping_version, list_mapping_versions, restore_mapping_version,
    load_mappings_index, save_mappings_index,
    load_business_rules, save_business_rules,
    _log_invoice_to_history,
)


from parsers import (
    parse_rdi, extract_xml_from_pdf, remove_pdf_signature,
    FACTURX_FALLBACK_NS, build_xml_namespaces,
    get_xml_tag_name, get_xml_short_name,
)

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


# HTML charge depuis templates/index.html (extrait pour economiser des tokens)
_HTML_PATH = os.path.join(SCRIPT_DIR, 'templates', 'index.html')

def _read_html():
    with open(_HTML_PATH, 'r', encoding='utf-8') as f:
        return f.read()

# Pré-charge initial (utilisé en fallback si le fichier devient illisible).
HTML = _read_html()

@app.route('/img/<path:filename>')
def serve_image(filename):
    from flask import send_from_directory
    return send_from_directory(os.path.join(SCRIPT_DIR, 'img'), filename)

@app.route('/')
def index():
    prefix = request.script_root or URL_PREFIX
    # Cache-buster basé sur la mtime de app.js : force le navigateur à recharger
    # le JS dès qu'il change, plutôt que de servir une version périmée du cache.
    try:
        js_ver = str(int(os.path.getmtime(os.path.join(SCRIPT_DIR, 'static', 'js', 'app.js'))))
    except OSError:
        js_ver = '0'
    # Relit le template à chaque requête : évite de devoir relancer le serveur
    # à chaque modif du HTML. Coût négligeable (lecture disque ~ qq Ko).
    try:
        html = _read_html()
    except OSError:
        html = HTML
    return (
        html
        .replace('__URL_PREFIX__', prefix)
        .replace('static/js/app.js"', f'static/js/app.js?v={js_ver}"')
    )

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

def _safe_archive_name(name, fallback):
    if not name:
        return fallback
    base = os.path.basename(name)
    cleaned = re.sub(r'[^A-Za-z0-9._-]', '_', base).strip('._-')
    return cleaned or fallback


def archive_invoice_files(invoice_id, *, rdi_path=None, pdf_path=None,
                          cii_path=None, xml_content=None):
    """Copie les fichiers d'entrée + le XML extrait dans archive_files/<invoice_id>/
    et met à jour les colonnes archive_* de invoice_history."""
    if not invoice_id:
        return
    import shutil
    try:
        target_dir = os.path.join(ARCHIVE_FOLDER, str(invoice_id))
        os.makedirs(target_dir, exist_ok=True)
        paths = {}
        if rdi_path and os.path.exists(rdi_path):
            name = _safe_archive_name(os.path.basename(rdi_path), 'rdi.txt')
            dest = os.path.join(target_dir, 'rdi__' + name)
            shutil.copy2(rdi_path, dest)
            paths['archive_rdi'] = os.path.relpath(dest, ARCHIVE_FOLDER)
        if pdf_path and os.path.exists(pdf_path):
            name = _safe_archive_name(os.path.basename(pdf_path), 'document.pdf')
            dest = os.path.join(target_dir, 'pdf__' + name)
            shutil.copy2(pdf_path, dest)
            paths['archive_pdf'] = os.path.relpath(dest, ARCHIVE_FOLDER)
        if cii_path and os.path.exists(cii_path):
            name = _safe_archive_name(os.path.basename(cii_path), 'cii.xml')
            dest = os.path.join(target_dir, 'cii__' + name)
            shutil.copy2(cii_path, dest)
            paths['archive_cii'] = os.path.relpath(dest, ARCHIVE_FOLDER)
        if xml_content:
            dest = os.path.join(target_dir, 'xml__extracted.xml')
            content = xml_content if isinstance(xml_content, str) else xml_content.decode('utf-8', errors='replace')
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(content)
            paths['archive_xml'] = os.path.relpath(dest, ARCHIVE_FOLDER)
        if paths:
            conn = get_db()
            sets = ', '.join(f'{k} = ?' for k in paths)
            conn.execute(
                f'UPDATE invoice_history SET {sets} WHERE id = ?',
                list(paths.values()) + [invoice_id]
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[ARCHIVE] Erreur archivage facture {invoice_id}: {e}")


_LAST_ARCHIVE_PURGE = [0.0]

def purge_old_archive_files(retention_days=ARCHIVE_RETENTION_DAYS, min_interval_sec=300):
    """Supprime les dossiers archive_files/<id>/ pour les lignes invoice_history
    plus anciennes que retention_days, et nullifie les colonnes archive_*.
    Appelée paresseusement ; min_interval_sec évite les passes trop rapprochées."""
    import time, shutil
    from datetime import datetime, timedelta
    now = time.time()
    if now - _LAST_ARCHIVE_PURGE[0] < min_interval_sec:
        return
    _LAST_ARCHIVE_PURGE[0] = now
    try:
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec='seconds')
        conn = get_db()
        rows = conn.execute(
            "SELECT id FROM invoice_history "
            "WHERE timestamp < ? AND ("
            "    archive_rdi IS NOT NULL OR archive_pdf IS NOT NULL "
            " OR archive_cii IS NOT NULL OR archive_xml IS NOT NULL)",
            (cutoff,)
        ).fetchall()
        expired_ids = {r['id'] for r in rows}
        for inv_id in expired_ids:
            d = os.path.join(ARCHIVE_FOLDER, str(inv_id))
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        if expired_ids:
            conn.executemany(
                "UPDATE invoice_history SET archive_rdi=NULL, archive_pdf=NULL, "
                "archive_cii=NULL, archive_xml=NULL WHERE id = ?",
                [(i,) for i in expired_ids]
            )
            conn.commit()
        # Orphelins : dossier sans ligne en BDD
        if os.path.isdir(ARCHIVE_FOLDER):
            for entry in os.listdir(ARCHIVE_FOLDER):
                full = os.path.join(ARCHIVE_FOLDER, entry)
                if not os.path.isdir(full):
                    continue
                try:
                    inv_id = int(entry)
                except ValueError:
                    continue
                row = conn.execute(
                    "SELECT 1 FROM invoice_history WHERE id = ?", (inv_id,)
                ).fetchone()
                if not row:
                    shutil.rmtree(full, ignore_errors=True)
        conn.close()
        if expired_ids:
            print(f"[ARCHIVE] Purgé {len(expired_ids)} archive(s) > {retention_days}j.")
    except Exception as e:
        print(f"[ARCHIVE] Erreur purge: {e}")


def _process_invoice(rdi_path, pdf_path, cii_path, type_formulaire, type_controle):
    """Traite une facture à partir de chemins de fichiers déjà sauvegardés.
    Retourne (result_dict, error_str, xml_content). result_dict contient results, stats, categories_results, type_controle."""
    xml_content = None
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
                return None, 'XML CII invalide', xml_content
        elif pdf_path:
            if pdf_path.lower().endswith('.pdf'):
                xml_content = extract_xml_from_pdf(pdf_path)
                if not xml_content:
                    return None, 'XML introuvable dans le PDF', None
            else:
                with open(pdf_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except Exception:
                return None, 'XML invalide', xml_content

        mapping_data = load_mapping(type_formulaire)
        if not mapping_data:
            return None, 'Mapping introuvable', xml_content

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

        # Validation schematron officielle EN16931 — même flux que controle() :
        # respect du toggle global, XML réel si dispo sinon reconstruction depuis le RDI.
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
        for bg_id in categories_results:
            categories_results[bg_id]['champs'].sort(key=lambda x: x.get('order_index', 9999))

        return {
            'results': results,
            'stats': stats,
            'categories_results': dict(categories_results),
            'type_controle': type_controle,
            'schematron': schematron_summary,
        }, None, xml_content

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, str(e), xml_content


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

            result, error, xml_content = _process_invoice(
                rdi_path, pdf_path, None, type_formulaire, type_controle
            )

            if error:
                batch_results.append({'name': name, 'error': error,
                                       'stats': None, 'results': None,
                                       'categories_results': None,
                                       'type_controle': type_controle})
                invoice_id = _log_invoice_to_history(
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
                invoice_id = _log_invoice_to_history(
                    type_formulaire, type_controle, 'batch',
                    invoice_number=inv_num, filename=name,
                    stats=result.get('stats'), results=result.get('results')
                )
            archive_invoice_files(
                invoice_id,
                rdi_path=rdi_path, pdf_path=pdf_path, cii_path=None,
                xml_content=xml_content,
            )

        purge_old_archive_files()

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
        xml_content = None
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
        invoice_id = _log_invoice_to_history(
            type_formulaire, type_controle, 'unitaire',
            invoice_number=inv_num, filename=src_filename,
            stats=stats, results=results
        )
        archive_invoice_files(
            invoice_id,
            rdi_path=rdi_path, pdf_path=pdf_path, cii_path=cii_path,
            xml_content=xml_content,
        )
        purge_old_archive_files()

        # Nettoyage
        if rdi_path and os.path.exists(rdi_path):
            os.remove(rdi_path)
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        if cii_path and os.path.exists(cii_path):
            os.remove(cii_path)

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
    """Liste des dernières factures contrôlées (sans limite par défaut)."""
    try:
        where, params = _stats_build_filters(request.args)
        raw_limit = request.args.get('limit')
        sql_tail = ' ORDER BY id DESC'
        sql_params = list(params)
        if raw_limit not in (None, '', '0', 'all'):
            try:
                limit = max(1, int(raw_limit))
                sql_tail += ' LIMIT ?'
                sql_params.append(limit)
            except ValueError:
                pass
        conn = get_db()
        rows = conn.execute(
            f"SELECT id, timestamp, type_formulaire, type_controle, mode, "
            f"       invoice_number, filename, total, ok, erreur, "
            f"       ignore_count, ambigu, conformity_pct, error, "
            f"       archive_rdi, archive_pdf, archive_cii, archive_xml "
            f"FROM invoice_history{where}{sql_tail}",
            sql_params
        ).fetchall()
        conn.close()
        items = []
        for r in rows:
            d = dict(r)
            d['files'] = {
                k: bool(d.pop('archive_' + k))
                for k in ARCHIVE_KINDS
            }
            items.append(d)
        return jsonify({'items': items})
    except Exception as e:
        print(f"[STATS] history erreur : {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/file/<int:invoice_id>/<kind>', methods=['GET'])
def api_stats_file(invoice_id, kind):
    """Renvoie le fichier archivé associé à une ligne d'historique."""
    if kind not in ARCHIVE_KINDS:
        return jsonify({'error': 'kind invalide'}), 400
    col = 'archive_' + kind
    try:
        conn = get_db()
        row = conn.execute(
            f"SELECT {col} FROM invoice_history WHERE id = ?", (invoice_id,)
        ).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    if not row or not row[col]:
        return jsonify({'error': 'fichier indisponible'}), 404
    rel = row[col]
    full = os.path.normpath(os.path.join(ARCHIVE_FOLDER, rel))
    if not full.startswith(os.path.normpath(ARCHIVE_FOLDER) + os.sep):
        return jsonify({'error': 'chemin invalide'}), 400
    if not os.path.isfile(full):
        return jsonify({'error': 'fichier purgé'}), 404
    base = os.path.basename(full)
    download_name = base.split('__', 1)[-1] if '__' in base else base
    return send_file(full, as_attachment=True, download_name=download_name)


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
