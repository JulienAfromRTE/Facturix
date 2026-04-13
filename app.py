#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Factur-X V12.0 - Enhanced Mapping Management"""
from flask import Flask, request, jsonify, send_file
import os, json, PyPDF2, io
import logging
from lxml import etree
from collections import defaultdict

app = Flask(__name__)

# ════════════════════════════════════════════
# CONFIGURATION PROJECTIX — NE PAS SUPPRIMER
# ════════════════════════════════════════════
APP_NAME = "FacturixV2"
APP_SLUG = "facturixv2"
APP_RELEASE = "v2.0"
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

VERSIONS_FOLDER = os.path.join(SCRIPT_DIR, 'mapping_versions')
os.makedirs(VERSIONS_FOLDER, exist_ok=True)

MAPPINGS_FOLDER = os.path.join(SCRIPT_DIR, 'mappings')
os.makedirs(MAPPINGS_FOLDER, exist_ok=True)

RULES_FILE = os.path.join(SCRIPT_DIR, 'business_rules.json')
MAPPINGS_INDEX_FILE = os.path.join(SCRIPT_DIR, 'mappings_index.json')

print(f"[FACTURX] Dossier de travail : {SCRIPT_DIR}")

def load_business_rules():
    """Charge les règles métiers depuis le fichier JSON"""
    if not os.path.exists(RULES_FILE):
        # Créer le fichier avec les règles par défaut
        default_rules = {
            "rules": [
                {
                    "id": "rule_1",
                    "name": "Facture B2G Chorus",
                    "enabled": True,
                    "conditions": [
                        {"field": "BT-22", "operator": "equals", "value": "B2G"}
                    ],
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
                    "enabled": True,
                    "conditions": [
                        {"field": "BT-3", "operator": "equals", "value": "381"}
                    ],
                    "actions": [
                        {"type": "make_mandatory", "field": "BT-25"},
                        {"type": "make_mandatory", "field": "BT-26"}
                    ]
                },
                {
                    "id": "rule_3",
                    "name": "BT-8 doit valoir 5",
                    "enabled": True,
                    "conditions": [],
                    "actions": [
                        {"type": "must_equal", "field": "BT-8", "value": "5"}
                    ]
                },
                {
                    "id": "rule_4",
                    "name": "Client étranger",
                    "enabled": True,
                    "conditions": [
                        {"field": "BT-48", "operator": "not_starts_with", "value": "FR"}
                    ],
                    "actions": [
                        {"type": "make_mandatory", "field": "BT-58"}
                    ]
                },
                {
                    "id": "rule_5",
                    "name": "Facture négative - quantité",
                    "enabled": True,
                    "conditions": [
                        {"field": "BT-131", "operator": "less_than", "value": "0"}
                    ],
                    "actions": [
                        {"type": "must_be_negative", "field": "BT-129"}
                    ]
                }
            ]
        }
        save_business_rules(default_rules)
        return default_rules
    try:
        with open(RULES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"rules": []}

def save_business_rules(rules_data):
    """Sauvegarde les règles métiers"""
    try:
        with open(RULES_FILE, 'w', encoding='utf-8') as f:
            json.dump(rules_data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def load_mappings_index():
    """Charge l'index des mappings"""
    if not os.path.exists(MAPPINGS_INDEX_FILE):
        default_index = {
            "mappings": [
                {
                    "id": "default_simple",
                    "name": "Mapping CART Simple",
                    "type": "CART Simple",
                    "filename": "mapping_v5_simple.json",
                    "created_date": "2024-01-15",
                    "is_default": True
                },
                {
                    "id": "default_groupee",
                    "name": "Mapping CART Groupée",
                    "type": "CART Groupée",
                    "filename": "mapping_v5_groupee.json",
                    "created_date": "2024-01-15",
                    "is_default": True
                },
                {
                    "id": "default_ventesdiverses",
                    "name": "Mapping Ventes Diverses",
                    "type": "Ventes Diverses",
                    "filename": "mapping_v5_ventesdiverses.json",
                    "created_date": "2024-01-15",
                    "is_default": True
                }
            ]
        }
        save_mappings_index(default_index)
        return default_index
    try:
        with open(MAPPINGS_INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"mappings": []}

def save_mappings_index(index_data):
    """Sauvegarde l'index des mappings"""
    try:
        with open(MAPPINGS_INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def load_mapping(type_formulaire='CARTsimple'):
    # Si c'est un mapping custom (format: custom_type_id)
    if type_formulaire.startswith('custom_'):
        # Chercher dans l'index
        index = load_mappings_index()
        # Reconstruire le filename
        filename = f'mapping_{type_formulaire}.json'
        filepath = os.path.join(SCRIPT_DIR, filename)
    else:
        # Mapping par défaut
        filepath = os.path.join(SCRIPT_DIR, f'mapping_v5_{type_formulaire}.json')
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur chargement mapping {filepath}: {e}")
        return None

def save_mapping(data, type_formulaire='simple'):
    # Si c'est un mapping custom
    if type_formulaire.startswith('custom_'):
        filename = f'mapping_{type_formulaire}.json'
        filepath = os.path.join(SCRIPT_DIR, filename)
    else:
        filepath = os.path.join(SCRIPT_DIR, f'mapping_v5_{type_formulaire}.json')
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Erreur sauvegarde mapping {filepath}: {e}")
        return False

def save_mapping_version(data, type_formulaire='simple'):
    """Sauvegarde une version horodatée du mapping"""
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'mapping_v5_{type_formulaire}_{timestamp}.json'
    filepath = os.path.join(VERSIONS_FOLDER, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {'success': True, 'filename': filename, 'timestamp': timestamp}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def list_mapping_versions(type_formulaire='simple'):
    """Liste toutes les versions horodatées d'un mapping"""
    try:
        pattern = f'mapping_v5_{type_formulaire}_'
        versions = []
        for filename in os.listdir(VERSIONS_FOLDER):
            if filename.startswith(pattern) and filename.endswith('.json'):
                filepath = os.path.join(VERSIONS_FOLDER, filename)
                stat = os.stat(filepath)
                # Extraire le timestamp du nom de fichier
                timestamp_str = filename.replace(pattern, '').replace('.json', '')
                versions.append({
                    'filename': filename,
                    'timestamp': timestamp_str,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime
                })
        # Trier par timestamp décroissant (plus récent en premier)
        versions.sort(key=lambda x: x['timestamp'], reverse=True)
        return versions
    except:
        return []

def restore_mapping_version(filename, type_formulaire='simple'):
    """Restaure une version horodatée comme version active"""
    try:
        version_path = os.path.join(VERSIONS_FOLDER, filename)
        with open(version_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Sauvegarder comme version active
        success = save_mapping(data, type_formulaire)
        return {'success': success}
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
    text_blocks = {}  # Pour accumuler les blocs de texte référencés (PENALITE-TEXT, TTAUX-TEXT, etc.)

    # Tags qui appartiennent aux blocs articles (lignes de facture)
    ARTICLE_TAG_PREFIXES = ('GS_FECT_EINV-BG25-', 'GS_FECT_EINV-BG26-',
                            'GS_FECT_EINV-BG29-', 'GS_FECT_EINV-BG30-',
                            'GS_FECT_EINV-BG31-',
                            'MAIN_GS_FECT_EINV-BG25-', 'MAIN_GS_FECT_EINV-BG26-',
                            'MAIN_GS_FECT_EINV-BG29-', 'MAIN_GS_FECT_EINV-BG30-',
                            'MAIN_GS_FECT_EINV-BG31-')

    try:
        with open(rdi_path, 'r', encoding='cp1252') as f:
            for line in f:
                if line.startswith('DHEADER') or line.startswith('DMAIN'):
                    if len(line) >= 176:
                        try:
                            length_str = line[172:175]
                            length = int(length_str)
                            value = line[175:175+length] if len(line) > 175 else ''
                            tag_section = line[41:172].strip()
                            tag_parts = tag_section.split()
                            if tag_parts:
                                tag = tag_parts[-1]

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

                                # Gestion des blocs articles (BG25/BG26/BG29/BG30/BG31)
                                elif any(tag.startswith(p) or tag.upper().startswith(p) for p in ARTICLE_TAG_PREFIXES):
                                    # BT126 = début d'un nouveau bloc article
                                    if 'BT126' in tag:
                                        current_article = {}
                                        articles.append(current_article)
                                    if current_article is not None:
                                        current_article[tag] = value
                                # Accumulation des blocs de texte multi-lignes (PENALITE-TEXT, TTAUX-TEXT, etc.)
                                elif not tag.startswith('GS_FECT_EINV-') and not tag.startswith('MAIN_GS_FECT_EINV-'):
                                    if tag not in text_blocks:
                                        text_blocks[tag] = []
                                    text_blocks[tag].append(value)
                                elif tag not in data:
                                    data[tag] = value
                        except:
                            pass
    except:
        pass

    # Résolution des références BT-22 vers les blocs de texte concaténés
    for key in list(data.keys()):
        if 'BT22' in key:
            val = data[key].strip()
            if val in text_blocks:
                data[key] = ' '.join(text_blocks[val])

    return data, articles

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
        'VENDEUR': ('BG-VENDEUR', '🏢 INFORMATIONS VENDEUR'),
        'ACHETEUR': ('BG-ACHETEUR', '🛒 INFORMATIONS ACHETEUR'),
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


def apply_business_rules(results, type_formulaire='simple'):
    """
    Applique les règles métiers configurables.
    Remplace l'ancienne fonction apply_contextual_controls hardcodée.
    """
    rules_data = load_business_rules()
    by_balise = {r['balise']: r for r in results}
    
    def evaluate_condition(cond, by_balise):
        """Évalue une condition"""
        field = cond.get('field')
        operator = cond.get('operator')
        value = cond.get('value', '')
        
        result_obj = by_balise.get(field)
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
                return float(field_value.replace(',', '.')) < float(value)
            except:
                return False
        elif operator == 'greater_than':
            try:
                return float(field_value.replace(',', '.')) > float(value)
            except:
                return False
        elif operator == 'is_empty':
            return not field_value
        elif operator == 'is_not_empty':
            return bool(field_value)
        
        return False
    
    def apply_action(action, by_balise):
        """Applique une action"""
        action_type = action.get('type')
        target_field = action.get('field')
        
        target = by_balise.get(target_field)
        if not target:
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
            
            if actual != expected:
                target['status'] = 'ERREUR'
                if 'RAS' in target['details_erreurs']:
                    target['details_erreurs'].remove('RAS')
                msg = f'Règle métier "{rule_name}" non respectée : attendu "{expected}", trouvé "{actual}"'
                if msg not in target['details_erreurs']:
                    target['details_erreurs'].append(msg)
        
        elif action_type == 'must_be_negative':
            try:
                value_str = target.get('rdi', '0').strip() or target.get('xml', '0').strip()
                value = float(value_str.replace(',', '.').replace(' ', ''))
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
    
    # Parcourir toutes les règles actives
    for rule in rules_data.get('rules', []):
        if not rule.get('enabled', True):
            continue
        
        # Vérifier si la règle s'applique à ce type de formulaire
        applicable_forms = rule.get('applicable_forms', [])
        if applicable_forms and type_formulaire not in applicable_forms:
            continue  # Règle non applicable à ce formulaire
        
        # Évaluer toutes les conditions (AND logique)
        conditions_met = True
        for cond in rule.get('conditions', []):
            if not evaluate_condition(cond, by_balise):
                conditions_met = False
                break
        
        # Si conditions remplies, appliquer les actions
        if conditions_met or len(rule.get('conditions', [])) == 0:
            for action in rule.get('actions', []):
                action['reason'] = rule.get('name', 'Règle métier')
                apply_action(action, by_balise)

    # -------------------------------------------------------
    # Règle BT-21-SUR / BT-22-SUR obligatoire avec valeur ISU
    # Toutes les factures doivent avoir un BT-21-SUR avec BT-22 = ISU
    # -------------------------------------------------------
    bt21_sur = by_balise.get('BT-21-SUR')
    if bt21_sur:
        regle_label = 'Présence obligatoire de BT-21-SUR'
        if regle_label not in bt21_sur['regles_testees']:
            bt21_sur['regles_testees'].insert(0, regle_label)
        if not bt21_sur.get('rdi', '').strip() and not bt21_sur.get('xml', '').strip():
            bt21_sur['status'] = 'ERREUR'
            if 'RAS' in bt21_sur['details_erreurs']:
                bt21_sur['details_erreurs'].remove('RAS')
            bt21_sur['details_erreurs'].insert(0, 'BT-21-SUR obligatoire : valeur SUR attendue')

    bt22_sur = by_balise.get('BT-22-SUR')
    if bt22_sur:
        regle_label = 'BT-22-SUR doit valoir ISU'
        if regle_label not in bt22_sur['regles_testees']:
            bt22_sur['regles_testees'].insert(0, regle_label)
        val = bt22_sur.get('rdi', '').strip() or bt22_sur.get('xml', '').strip()
        if val.upper() != 'ISU':
            bt22_sur['status'] = 'ERREUR'
            if 'RAS' in bt22_sur['details_erreurs']:
                bt22_sur['details_erreurs'].remove('RAS')
            msg = f'BT-22-SUR doit valoir "ISU", trouvé : "{val}"'
            if msg not in bt22_sur['details_erreurs']:
                bt22_sur['details_erreurs'].insert(0, msg)

    # -------------------------------------------------------
    # Règle BT-22-BAR B2G (Chorus) -> champs obligatoires
    # Si BT-22-BAR = "B2G", BT-10, BT-13, BT-29, BT-29-1 obligatoires
    # -------------------------------------------------------
    bt22_bar = by_balise.get('BT-22-BAR')
    if bt22_bar and bt22_bar.get('rdi', '').strip().upper() == 'B2G':
        def force_obligatoire_bg1(balise, raison):
            r = by_balise.get(balise)
            if r is None:
                return
            r['obligatoire'] = 'Oui'
            regle_label = f'Regle specifique : {raison}'
            if regle_label not in r['regles_testees']:
                r['regles_testees'].insert(0, regle_label)
            if not r.get('rdi', '').strip() and not r.get('xml', '').strip():
                r['status'] = 'ERREUR'
                if 'RAS' in r['details_erreurs']:
                    r['details_erreurs'].remove('RAS')
                r['details_erreurs'].insert(0, f'Champ obligatoire selon regle : {raison}')
        for balise in ['BT-10', 'BT-13', 'BT-29', 'BT-29-1']:
            force_obligatoire_bg1(balise, 'Facture B2G (Chorus)')

    return results
    """
    Contrôles conditionnels en dur :
    1. BT-22 = "B2G" (Chorus) -> BT-10, BT-13, BT-29, BT-29-1 obligatoires
    2. Avoir (BT-3 = "381")   -> BT-25, BT-26 obligatoires
    3. BT-8 doit toujours valoir "5"
    4. Client etranger (BT-48 ne commence pas par "FR") -> BT-58 obligatoire
    5. BT-131 negatif -> BT-129 doit etre negatif
    """
    # Index balise -> result pour acces rapide
    by_balise = {r['balise']: r for r in results}

    def force_obligatoire(balise, raison):
        """Rend un champ obligatoire et leve une erreur s'il est vide."""
        r = by_balise.get(balise)
        if r is None:
            return  # champ absent du mapping, on ignore
        # Marquer comme obligatoire visuellement
        r['obligatoire'] = 'Oui'
        # Ajouter la regle dans la liste si pas deja presente
        regle_label = f'Regle specifique : {raison}'
        if regle_label not in r['regles_testees']:
            r['regles_testees'].insert(0, regle_label)
        # Lever une erreur si la valeur est absente (RDI et XML vides)
        if not r.get('rdi', '').strip() and not r.get('xml', '').strip():
            r['status'] = 'ERREUR'
            if 'RAS' in r['details_erreurs']:
                r['details_erreurs'].remove('RAS')
            r['details_erreurs'].insert(0, f'Champ obligatoire selon regle : {raison}')

    # -------------------------------------------------------
    # Regle 1 : BT-22 = "B2G" (Chorus)
    # -------------------------------------------------------
    bt22 = by_balise.get('BT-22')
    if bt22 and bt22.get('rdi', '').strip().upper() == 'B2G':
        for balise in ['BT-10', 'BT-13', 'BT-29', 'BT-29-1']:
            force_obligatoire(balise, 'Facture B2G (Chorus)')

    # -------------------------------------------------------
    # Regle 2 : Avoir → BT-25 et BT-26 obligatoires
    # BT-3 = code type de facture ; 381 = note de credit / avoir
    # -------------------------------------------------------
    bt3 = by_balise.get('BT-3')
    if bt3 and bt3.get('rdi', '').strip() in ('381', 'avoir', 'Avoir', 'AVOIR'):
        for balise in ['BT-25', 'BT-26']:
            force_obligatoire(balise, 'Facture avoir (BT-3 = 381)')

    # -------------------------------------------------------
    # Regle 3 : BT-8 doit toujours valoir "5"
    # -------------------------------------------------------
    bt8 = by_balise.get('BT-8')
    if bt8:
        val = bt8.get('rdi', '').strip()
        regle_label = 'Valeur imposee = "5"'
        if regle_label not in bt8['regles_testees']:
            bt8['regles_testees'].append(regle_label)
        if val != '5':
            bt8['status'] = 'ERREUR'
            if 'RAS' in bt8['details_erreurs']:
                bt8['details_erreurs'].remove('RAS')
            msg = f'Valeur attendue "5", valeur trouvee : "{val}"'
            if msg not in bt8['details_erreurs']:
                bt8['details_erreurs'].append(msg)

    # -------------------------------------------------------
    # Regle 4 : Client etranger → BT-58 obligatoire
    # BT-48 = numero TVA intracommunautaire de l'acheteur
    # -------------------------------------------------------
    bt48 = by_balise.get('BT-48')
    if bt48:
        tva = bt48.get('rdi', '').strip().upper()
        if tva and not tva.startswith('FR'):
            force_obligatoire('BT-58', 'Client etranger : TVA = ' + tva)

    # -------------------------------------------------------
    # Regle 5 : BT-131 negatif → BT-129 doit etre negatif
    # BT-131 = montant net de la ligne (facture negative si negatif)
    # BT-129 = quantite facturee (doit etre negative, pas le prix unitaire)
    # -------------------------------------------------------
    bt131 = by_balise.get('BT-131')
    bt129 = by_balise.get('BT-129')
    if bt131 and bt129:
        try:
            montant_net = float(bt131.get('rdi', '0').replace(',', '.').replace(' ', ''))
            quantite = float(bt129.get('rdi', '0').replace(',', '.').replace(' ', ''))
            if montant_net < 0:
                regle_label = 'Facture negative : quantite doit etre negative'
                if regle_label not in bt129['regles_testees']:
                    bt129['regles_testees'].append(regle_label)
                if quantite >= 0:
                    bt129['status'] = 'ERREUR'
                    if 'RAS' in bt129['details_erreurs']:
                        bt129['details_erreurs'].remove('RAS')
                    msg = f'BT-131 est negatif ({montant_net}), BT-129 doit etre negatif (trouve: {quantite})'
                    if msg not in bt129['details_erreurs']:
                        bt129['details_erreurs'].append(msg)
        except (ValueError, AttributeError):
            pass  # Si conversion impossible, on ignore

    return results


HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link rel="icon" type="image/x-icon" href="__URL_PREFIX__/img/IcoSite.ico">
<link rel="icon" type="image/png" href="__URL_PREFIX__/img/AppLogo_V2.png">
<title>Facturix - La potion magique pour des factures certifiées !</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Arial,sans-serif;background:#667eea;min-height:100vh;display:flex;align-items:stretch;gap:0}
.sidebar{width:18px;min-width:18px;background:linear-gradient(180deg,#1a3a5c 0%,#366092 60%,#667eea 100%);position:sticky;top:0;height:100vh;flex-shrink:0}
.main-wrap{flex:1;padding:20px;min-width:0;overflow-y:auto}
.container{max-width:1400px;margin:0 auto;background:#fff;border-radius:20px;overflow:hidden}
.header{background:#366092;color:#fff;padding:12px 30px 20px 30px;display:flex;align-items:flex-end;gap:18px;justify-content:space-between}
.header-left{display:flex;align-items:center;gap:18px}
.header-logo{height:80px;width:auto;object-fit:contain;flex-shrink:0;display:block}
.header-banner{flex-shrink:0;cursor:pointer;margin-bottom:-20px;margin-top:-15px;transition:transform 0.2s}
.header-banner:hover{transform:scale(1.05)}
.header-banner img{height:119px;width:auto;display:block}
.header-text h1{font-size:1.5em;margin:0}
.version{font-size:0.85em;opacity:0.8;margin-top:3px}
/* Progress bar */
.progress-section{background:#fff;border-radius:12px;padding:20px 25px;margin-bottom:20px}
.progress-label-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px}
.progress-label-row h3{margin:0;color:#366092;font-size:1em}
.progress-pct{font-size:1.5em;font-weight:bold;color:#366092}
/* perso inline supprime */
.progress-track{background:#e0e0e0;border-radius:10px;height:20px;position:relative;cursor:pointer}
.gaulois-overlay{display:none;position:fixed;z-index:9999;pointer-events:none}
.gaulois-overlay.visible{display:flex;flex-direction:column;align-items:center}
.gaulois-card{background:#fff;border:3px solid #366092;border-radius:18px;padding:12px 18px;box-shadow:0 8px 32px rgba(0,0,0,0.35);display:flex;flex-direction:column;align-items:center;gap:10px;max-width:420px}
.gaulois-card img{width:338px;height:338px;object-fit:contain;border-radius:12px}
.progress-fill{height:100%;border-radius:10px;transition:width 0.9s ease;min-width:2px}
.pct-0{background:linear-gradient(90deg,#b71c1c,#e53935)}
.pct-25{background:linear-gradient(90deg,#e53935,#ff7043)}
.pct-50{background:linear-gradient(90deg,#ffa000,#ffc107)}
.pct-75{background:linear-gradient(90deg,#70ad47,#43a047)}
@media(max-width:900px){.sidebar{display:none}.main-wrap{padding:10px}}
.tabs{display:flex;background:#f0f0f0}
.tab{padding:15px 30px;cursor:pointer;border:none;background:transparent;font-weight:600}
.tab.active{background:#fff;color:#366092}
.tab-content{display:none;padding:40px}
.tab-content.active{display:block}
.section{background:#f8f9fa;border-radius:12px;padding:25px;margin-bottom:25px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
.form-group{display:flex;flex-direction:column}
.form-group label{font-weight:600;margin-bottom:8px}
.form-group select,.form-group input,.form-group textarea{padding:12px;border:2px solid #366092;border-radius:8px;font-size:1em}
.form-group textarea{min-height:80px;font-family:monospace;font-size:0.9em}
.help-box{background:#e7f3ff;border-left:4px solid #2196F3;padding:15px;margin:15px 0}
.btn{background:#70ad47;color:#fff;padding:18px;border:none;border-radius:10px;font-size:1.2em;cursor:pointer;width:100%}
.btn:hover{background:#5a8c39}
.btn-secondary{background:#366092;color:#fff;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;margin-right:10px}
.btn-add{background:#28a745;color:#fff;padding:12px 24px;border:none;border-radius:8px;cursor:pointer}
.loading{display:none;text-align:center;padding:20px}
.spinner{border:4px solid #f3f3f3;border-top:4px solid #366092;border-radius:50%;width:50px;height:50px;animation:spin 1s linear infinite;margin:0 auto}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.results{display:none}
/* Stats : 3 colonnes */
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:20px;margin-bottom:30px}
.stat-card{background:#fff;padding:20px;border-radius:10px;text-align:center}
.stat-value{font-size:2em;font-weight:bold}
.ok .stat-value{color:#70ad47}
.erreur .stat-value{color:#c00000}
.ignore .stat-value{color:#9e9e9e}
.search-box{display:flex;align-items:center;gap:15px;padding:20px;background:#f0f4ff;border-radius:10px;border-left:4px solid #366092}
.search-box label{font-weight:600;color:#366092;white-space:nowrap}
.search-box input{flex:1;padding:12px;border:2px solid #366092;border-radius:8px;font-size:1em}
.search-box input:focus{outline:none;border-color:#2196F3;box-shadow:0 0 0 3px rgba(33,150,243,0.1)}
.btn-clear{background:#f44336;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.9em;white-space:nowrap}
.btn-clear:hover{background:#d32f2f}
.category.hidden{display:none}
.category{background:#fff;border-radius:10px;margin-bottom:15px}
.category-header{background:#366092;color:#fff;padding:20px;cursor:pointer;display:flex;justify-content:space-between}
.category-content{max-height:0;overflow:hidden;transition:max-height 0.3s}
.category-content.open{max-height:50000px}
table.main-table{width:100%;border-collapse:collapse;margin-top:10px}
table.main-table{font-size:0.88em}
table.main-table th{background:#366092;color:#fff;padding:6px 8px;text-align:left;font-weight:600;font-size:0.95em}
table.main-table td{padding:5px 8px;border-bottom:1px solid #eee;vertical-align:middle;line-height:1.35}
table.main-table tr.data-row:hover{background:#f0f4ff}
table.main-table ul{margin:0;padding-left:14px}
table.main-table li{margin:1px 0}
.col-status{width:28px;text-align:center;font-size:1.2em;padding:4px!important}
.col-bt{width:70px}
.col-bt .bt-oblig{border:1.5px solid #c0392b;border-radius:6px;padding:2px 5px;color:#c0392b;display:inline-block;text-align:center;font-size:0.85em;line-height:1.3}
.col-libelle{width:190px}
.col-valeurs{width:180px}
.col-valeurs .val-line{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:175px;line-height:1.4}
.col-valeurs .val-line .val-label{color:#888;font-weight:600;font-size:0.9em}
.col-regles{max-width:200px}
.col-erreurs{max-width:200px}
.col-erreurs-hidden{display:none}
/* Sous-tableau CEGEDIM */
table.ceg-table{width:100%;border-collapse:collapse;margin:6px 0 0 0;font-size:0.85em}
table.ceg-table th{background:#5b3fa0;color:#fff;padding:6px 10px;text-align:left}
table.ceg-table td{padding:6px 10px;border-bottom:1px solid #e0d0ff;background:#f8f4ff}
.ceg-row-header td{background:#f0e8ff;font-style:italic;font-size:0.8em;color:#5b3fa0;padding:4px 10px;border-bottom:1px dashed #ccc}
.tooltip{position:absolute;background:#333;color:#fff;padding:12px;border-radius:6px;font-size:0.9em;z-index:1000;display:none;max-width:500px;box-shadow:0 4px 8px rgba(0,0,0,0.3);pointer-events:none}
.tooltip strong{color:#ffc107;display:block;margin-bottom:4px}
.tooltip-separator{border-top:1px solid #666;margin:8px 0;padding-top:6px}
.tooltip-controls{font-size:0.85em;color:#ccc}
/* Paramétrage */
.mapping-list{list-style:none}
.mapping-item{padding:14px 18px;margin:8px 0;border-radius:8px;border-left:4px solid #366092;display:flex;justify-content:space-between;align-items:center;background:#fff;cursor:move;transition:all 0.2s}
.mapping-item.valide{background:#e8f5e9;border-left-color:#388e3c}
.mapping-item.dragging{opacity:0.5;transform:scale(0.98)}
.mapping-item.drag-over{border-top:3px solid #2196F3;margin-top:12px}
.mapping-item-info{flex:1}
.mapping-item-info .item-main{font-weight:600}
.mapping-item-info .item-sub{font-size:0.82em;color:#555;margin-top:3px}
.mapping-item-info .item-xpath{font-size:0.78em;color:#888;font-family:monospace;margin-top:2px;word-break:break-all}
.mapping-actions{display:flex;align-items:center;gap:8px;flex-shrink:0}
.mapping-actions button{padding:7px 14px;border:none;border-radius:4px;cursor:pointer;font-weight:600}
.btn-edit{background:#2196F3;color:#fff}
.btn-delete{background:#f44336;color:#fff}
.btn-download{background:#FF9800;color:#fff}
.btn-save-version{background:#9C27B0;color:#fff}
.btn-restore{background:#607D8B;color:#fff}
.valide-toggle{display:flex;align-items:center;gap:5px;font-size:0.85em;color:#388e3c;font-weight:600;cursor:pointer}
.valide-toggle input{width:16px;height:16px;cursor:pointer;accent-color:#388e3c}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1000}
.modal-content{background:#fff;margin:3% auto;padding:30px;border-radius:12px;max-width:900px;max-height:92vh;overflow-y:auto}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
.version-item{padding:12px;margin:8px 0;border-radius:6px;background:#f5f5f5;display:flex;justify-content:space-between;align-items:center}
.version-item:hover{background:#e8f5e9}
.version-info{flex:1}
.version-timestamp{font-weight:600;color:#366092}
.version-details{font-size:0.85em;color:#666;margin-top:4px}
.btn-group{display:flex;gap:10px;margin-bottom:15px}
.modal-close{font-size:2em;cursor:pointer;color:#999;line-height:1}
.modal .form-group{margin-bottom:18px}
.modal .form-group label{font-weight:600;margin-bottom:8px;font-size:0.95em;display:block}
.modal .form-group input,.modal .form-group select{padding:10px 12px;border:2px solid #366092;border-radius:6px;font-size:0.95em;width:100%}
.modal .form-group textarea{padding:10px 12px;border:2px solid #366092;border-radius:6px;font-size:0.9em;min-height:80px;font-family:monospace;width:100%}
.modal .form-group small{display:block;margin-top:6px;color:#666;font-size:0.85em;line-height:1.4}
/* Règles métiers */
.rule-card{background:#fff;border-radius:10px;margin-bottom:15px;overflow:hidden;border:2px solid #ddd}
.rule-header{padding:15px;display:flex;justify-content:space-between;align-items:center;background:#f5f5f5}
.rule-header.enabled{background:#e8f5e9;border-left:4px solid #4caf50}
.rule-header.disabled{background:#ffebee;border-left:4px solid #f44336;opacity:0.7}
.rule-title{flex:1}
.rule-title strong{font-size:1.1em;color:#366092}
.rule-status{margin-left:12px;padding:4px 10px;border-radius:4px;font-size:0.85em;font-weight:600}
.rule-header.enabled .rule-status{background:#4caf50;color:#fff}
.rule-header.disabled .rule-status{background:#f44336;color:#fff}
.rule-actions-btn{display:flex;gap:8px}
.rule-actions-btn button{padding:6px 12px;border:none;border-radius:4px;cursor:pointer;font-weight:600;background:#2196F3;color:#fff}
.rule-actions-btn button:hover{background:#1976D2}
.rule-actions-btn .btn-edit{background:#FF9800}
.rule-actions-btn .btn-edit:hover{background:#F57C00}
.rule-actions-btn .btn-delete{background:#f44336}
.rule-actions-btn .btn-delete:hover{background:#d32f2f}
.rule-body{padding:15px;border-top:1px solid #eee}
.rule-description{color:#666;font-size:0.9em;margin-bottom:12px;font-style:italic}
.rule-logic{background:#f9f9f9;padding:12px;border-radius:6px;font-family:monospace;font-size:0.9em}
.rule-logic div{margin:6px 0}
.condition-item,.action-item{background:#f0f4ff;padding:12px;border-radius:6px;margin-bottom:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.condition-item select,.action-item select,.condition-item input,.action-item input{padding:8px;border:2px solid #366092;border-radius:4px;font-size:0.9em;max-width:280px}
.condition-item .cond-field,.action-item .action-field{min-width:200px;flex:1}
.condition-item .cond-op,.action-item .action-type{min-width:150px}
.condition-item .cond-value,.action-item .action-value{min-width:120px;flex:0.5}
.condition-item .btn-remove,.action-item .btn-remove{background:#f44336;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:600;white-space:nowrap}
.condition-item .btn-remove:hover,.action-item .btn-remove:hover{background:#d32f2f}

/* ENHANCED MAPPING MANAGEMENT STYLES */
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
.mapping-header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:2rem;border-radius:1rem;color:white;margin-bottom:2rem;box-shadow:0 10px 25px rgba(0,0,0,0.15)}
.mapping-header h2{font-size:1.8rem;font-weight:700;margin-bottom:0.5rem}
.mapping-header p{opacity:0.9;font-size:1rem}
.mapping-type-select{width:100%;padding:12px 16px;border:2px solid #e2e8f0;border-radius:8px;font-family:'Outfit',Arial,sans-serif;font-size:1rem;background:#fff;transition:all 0.2s}
.mapping-type-select:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.1)}
.btn-create{background:linear-gradient(135deg,#10b981 0%,#059669 100%);color:#fff;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;font-weight:600;transition:all 0.2s;display:flex;align-items:center;gap:8px}
.btn-create:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(16,185,129,0.3)}
.btn-download{background:linear-gradient(135deg,#f59e0b 0%,#d97706 100%)}
.btn-save-version{background:linear-gradient(135deg,#8b5cf6 0%,#7c3aed 100%)}
.btn-restore{background:linear-gradient(135deg,#3b82f6 0%,#2563eb 100%)}
.mappings-list{margin-top:2rem}
.mapping-card{background:linear-gradient(135deg,#f1f5f9 0%,#e2e8f0 100%);padding:1.25rem;border-radius:8px;margin-bottom:1rem;display:flex;justify-content:space-between;align-items:center;transition:all 0.3s;border-left:4px solid #667eea}
.mapping-card:hover{transform:translateX(5px);box-shadow:0 4px 12px rgba(0,0,0,0.1)}
.mapping-info{flex:1}
.mapping-name{font-weight:600;font-size:1.1rem;color:#1e293b;margin-bottom:0.25rem}
.mapping-type{font-family:'JetBrains Mono',monospace;font-size:0.85rem;color:#64748b;background:rgba(255,255,255,0.7);padding:0.25rem 0.5rem;border-radius:0.25rem;display:inline-block}
.btn-delete{background:#ef4444;color:#fff;padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-weight:600;display:flex;align-items:center;gap:6px;font-size:0.85rem}
.btn-delete:hover{background:#dc2626}
.modal-header.create{background:linear-gradient(135deg,#10b981 0%,#059669 100%);color:white;border-top-left-radius:1rem;border-top-right-radius:1rem}
.modal-header.delete{background:linear-gradient(135deg,#ef4444 0%,#dc2626 100%);color:white;border-top-left-radius:1rem;border-top-right-radius:1rem}
.modal-header h2{margin:0;font-size:1.5rem;flex:1}
.warning-icon{font-size:2rem}
.warning-text{background:#fef3c7;border-left:4px solid #f59e0b;padding:1rem;border-radius:0.5rem;margin:1rem 0;color:#92400e}
.warning-text strong{display:block;margin-bottom:0.5rem}
.empty-state{text-align:center;padding:3rem 1rem;color:#64748b}
.empty-state-icon{font-size:4rem;margin-bottom:1rem;opacity:0.3}
.modal-content{background:#fff;margin:5% auto;padding: 20px;border-radius:1rem;width:90%;max-width: 700px;box-shadow:0 20px 60px rgba(0,0,0,0.3);animation:slideUp 0.3s ease}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes slideUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
.modal-body{padding:1.5rem}
.modal-footer{padding:1.5rem;border-top:2px solid #e2e8f0;display:flex;gap:0.75rem;justify-content:flex-end}

/* Easter egg Konami */
.konami-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:99999;align-items:center;justify-content:center;flex-direction:column}
.konami-overlay.visible{display:flex}
.konami-box{position:relative;animation:konami-pop 0.4s cubic-bezier(.34,1.56,.64,1)}
.konami-box img{max-width:70vw;max-height:70vh;border-radius:20px;box-shadow:0 0 80px rgba(255,215,0,0.6),0 0 20px rgba(0,0,0,0.8)}
.konami-stars{position:absolute;inset:0;pointer-events:none}
.konami-close{margin-top:20px;color:#fff;font-size:0.9em;opacity:0.7;cursor:pointer}
@keyframes konami-pop{0%{transform:scale(0) rotate(-10deg);opacity:0}100%{transform:scale(1) rotate(0deg);opacity:1}}
</style>
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
<img class="header-logo" src="__URL_PREFIX__/img/AppLogo_V2.png" alt="Logo"><div class="header-text"><h1>Facturix - La potion magique pour des factures certifiées !</h1>
<div class="version">V13.0 — Made with love by Julien ❤️</div></div>
</div>
<div class="header-banner" onclick="document.getElementById('konamiOverlay').classList.add('visible')">
<img src="__URL_PREFIX__/img/TopLogo.png" alt="On va vérifier tes factures, par Bélénos !">
</div>
</div>
<div class="tabs">
<button class="tab active" id="tabControle">Contrôle</button>
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
<div class="stat-card" style="background:#1a3a5a;color:#fff"><div>📦 Articles</div><div class="stat-value" id="statArticles" style="color:#fff">—</div></div>
</div>
</div>
<div class="section">
<div class="search-box">
<label for="searchBT">🔍 Rechercher un BT :</label>
<input type="text" id="searchBT" placeholder="Tapez un numéro de BT (ex: 48)">
<button class="btn-clear" id="btnClearSearch" style="display:none">✕ Effacer</button>
<label style="margin-left:20px;display:flex;align-items:center;gap:6px;font-weight:normal">
<input type="checkbox" id="filterErrors" style="width:18px;height:18px">
<span>Afficher uniquement les erreurs</span>
</label>
<label style="margin-left:20px;display:flex;align-items:center;gap:6px;font-weight:normal">
<input type="checkbox" id="showCegedim" style="width:18px;height:18px">
<span>Afficher contrôles CEGEDIM</span>
</label>
<div style="margin-left:auto;display:flex;gap:8px">
<button class="btn-clear" id="btnExpandAll" style="display:inline-block;font-size:12px;padding:4px 10px">▼ Tout déplier</button>
<button class="btn-clear" id="btnCollapseAll" style="display:inline-block;font-size:12px;padding:4px 10px">▲ Tout replier</button>
</div>
</div>
</div>
<div class="section"><div id="categoriesContainer"></div></div>
</div>
</div>

<!-- ONGLET PARAMETRAGE - ENHANCED -->
<div id="contentParam" class="tab-content">
<div class="section">
<h2>Gestion des Mappings</h2>
<div class="form-group" style="margin-bottom:20px">
<label>Type de formulaire :</label>
<select id="typeFormulaireParam" class="mapping-type-select">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupée</option>
<option value="ventesdiverses">Ventes Diverses</option>
</select>
</div>
<div class="btn-group">
<button class="btn-secondary" id="btnReload"><span>🔄</span> Actualiser</button>
<button class="btn-create" id="btnCreateMapping"><span>➕</span> Créer un mapping</button>
<button class="btn-add" id="btnAdd"><span>➕</span> Ajouter un champ</button>
<button class="btn-download" id="btnDownload"><span>📥</span> Télécharger JSON</button>
<button class="btn-save-version" id="btnSaveVersion"><span>💾</span> Sauvegarder version</button>
<button class="btn-restore" id="btnRestore"><span>🕐</span> Restaurer version</button>
</div>
<div class="search-box">
<label for="searchBTParam">🔍 Rechercher un BT :</label>
<input type="text" id="searchBTParam" placeholder="Tapez un numéro de BT (ex: 48)">
<button class="btn-clear" id="btnClearSearchParam" style="display:none">✕ Effacer</button>
</div>
</div>

<div class="section">
<h3 style="margin-bottom:1.5rem;font-size:1.3rem">📋 Mappings existants</h3>
<div id="mappingsListContainer" class="mappings-list"></div>
</div>

<div class="section">
<h3 style="margin-bottom:1rem">Champs du mapping actuel</h3>
<ul class="mapping-list" id="mappingList"></ul>
</div>
</div>

<!-- ONGLET RÈGLES MÉTIERS -->
<div id="contentRules" class="tab-content">
<div class="section">
<h2>Règles Métiers Configurables</h2>
<p>Gérez les règles de validation conditionnelles qui s'appliquent aux champs de la facture.</p>
<div class="form-row" style="margin-bottom:15px">
<div class="form-group">
<label>Filtrer par type de factures :</label>
<select id="filterFormType">
<option value="all">Toutes les factures</option>
<option value="simple">CART Simple uniquement</option>
<option value="groupee">CART Groupée uniquement</option>
<option value="ventesdiverses">Ventes Diverses uniquement</option>
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
<h2>Guide V12.0</h2>
<h3>Nouveautes V12</h3>
<ul>
<li>Pop-up améliórée : affichage des contrôles de cohérence et du tag XML complet</li>
<li>Image Gaulois 30% plus grande au survol de la barre de progression</li>
<li>Meilleure extraction XML pour les champs avec attributs (ex: format="102")</li>
</ul>
<h3>Nouveautes V11</h3>
<ul>
<li>Case a cocher "Valide" dans le paramétrage, fond vert</li>
<li>Tableau CEGEDIM detaille par BT dans les resultats</li>
<li>XPath visible dans le paramétrage</li>
<li>Stats simplifiees : Total / OK / Erreurs</li>
<li>Upload PDF masque en mode RDI</li>
</ul>
<h3>Mode RDI - Sortie SAP</h3>
<ol><li>Présence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ol>
<h3>Mode XML - Sortie Exstream</h3>
<ol><li>Présence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li><li>Comparaison RDI vs XML</li></ol>
<h3>Extraction XML avec attributs</h3>
<p>Pour les champs comme <code>&lt;udt:DateTimeString format="102"&gt;20250103&lt;/udt:DateTimeString&gt;</code>, 
utilisez le XPath complet incluant le tag final : <code>//udt:DateTimeString</code></p>
</div>
</div>
</div>

<!-- MODAL EDITION -->
<div id="editModal" class="modal">
<div class="modal-content">
<div class="modal-header">
<h2 id="modalTitle">Editer le Champ</h2>
<span class="modal-close" id="modalClose">&times;</span>
</div>
<div class="form-group"><label>Balise BT :</label><input type="text" id="editBalise"></div>
<div class="form-group"><label>Libelle :</label><input type="text" id="editLibelle"></div>
<div class="form-group">
<label>Catégorie :</label>
<select id="editCategorie">
<option value="BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE">INFORMATIONS GÉNÉRALES DE LA FACTURE</option>
<option value="BG-TOTAUX|TOTAUX DE LA FACTURE">TOTAUX DE LA FACTURE</option>
<option value="BG-TVA|DÉTAIL DE LA TVA">DÉTAIL DE LA TVA</option>
<option value="BG-LIGNES|LIGNES DE FACTURE">LIGNES DE FACTURE</option>
<option value="BG-VENDEUR|INFORMATIONS VENDEUR">INFORMATIONS VENDEUR</option>
<option value="BG-ACHETEUR|INFORMATIONS ACHETEUR">INFORMATIONS ACHETEUR</option>
</select>
</div>
<div class="form-group"><label>Champ RDI :</label><input type="text" id="editRdi"></div>
<div class="form-group"><label>XPath :</label><input type="text" id="editXpath"></div>
<div class="form-group">
<label>Attribut (optionnel) :</label>
<input type="text" id="editAttribute" placeholder="Ex: schemeID (pour extraire un attribut XML)">
<small style="display:block;color:#666;font-size:0.85em;margin-top:4px">
Laissez vide pour extraire le texte de la balise. Indiquez le nom d'attribut (ex: schemeID, format) pour extraire sa valeur.
</small>
</div>
<div class="form-group"><label>Type :</label>
<select id="editType"><option value="String">String</option><option value="Decimal">Decimal</option><option value="Date">Date</option></select>
</div>
<div class="form-group"><label>Obligatoire :</label>
<select id="editObligatoire"><option value="Oui">Oui</option><option value="Non">Non</option><option value="Dependant">Dependant</option></select>
</div>
<div class="form-group"><label>Ignorer les erreurs de ce BT :</label>
<select id="editIgnore"><option value="Non">Non</option><option value="Oui">Oui</option></select>
<small style="color:#666;font-size:0.85em;margin-top:5px;display:block">Si "Oui", ce champ sera ignoré lors des contrôles et marqué "Ignoré" dans la liste</small>
</div>
<div class="form-group"><label>Regle de Gestion (RDG) :</label><textarea id="editRdg"></textarea></div>
<button class="btn" id="btnSave">Sauvegarder</button>
</div>
</div>

<!-- MODAL RESTAURATION -->
<div id="restoreModal" class="modal">
<div class="modal-content">
<div class="modal-header">
<h2>Restaurer une version</h2>
<span class="modal-close" id="restoreModalClose">&times;</span>
</div>
<div id="versionsList"></div>
</div>
</div>

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
<label style="display:flex;align-items:center;gap:8px">
<input type="checkbox" id="ruleEnabled" checked style="width:20px;height:20px">
<span>Règle activée</span>
</label>
</div>
<div class="form-group">
<label>Applicable aux types de factures :</label>
<div style="display:flex;flex-direction:column;gap:8px;padding:10px;background:#f9f9f9;border-radius:6px">
<label style="display:flex;align-items:center;gap:8px;font-weight:normal">
<input type="checkbox" id="ruleFormSimple" checked style="width:18px;height:18px">
<span>CART Simple</span>
</label>
<label style="display:flex;align-items:center;gap:8px;font-weight:normal">
<input type="checkbox" id="ruleFormGroupee" checked style="width:18px;height:18px">
<span>CART Groupée</span>
</label>
<label style="display:flex;align-items:center;gap:8px;font-weight:normal">
<input type="checkbox" id="ruleFormVentes" checked style="width:18px;height:18px">
<span>Ventes Diverses</span>
</label>
</div>
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
<div class="form-group" style="margin-bottom:15px">
<label>Type de formulaire :</label>
<select id="newMappingType" class="mapping-type-select">
<option value="CART Simple">CART Simple</option>
<option value="CART Groupée">CART Groupée</option>
<option value="Ventes Diverses">Ventes Diverses</option>
</select>
</div>
<div class="form-group">
<label>Créer à partir de :</label>
<select id="copyFromMapping" class="mapping-type-select">
<option value="">Mapping vide</option>
</select>
<small style="display:block;color:#666;font-size:0.85em;margin-top:4px">
Choisissez un mapping existant pour copier sa configuration, ou créez un mapping vide
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
<p><strong>Type :</strong> <span id="deleteMappingType"></span></p>
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

<div id="tooltip" class="tooltip"></div>
<script>
var BASE='__URL_PREFIX__';
var currentMapping=null;
var currentIndex=null;
var tooltip=document.getElementById('tooltip');
var mappingsIndex = { mappings: [] };
var mappingToDelete = null;

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
loadMappingsIndex();
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

/* ---- MAPPING MANAGEMENT FUNCTIONS ---- */
function loadMappingsIndex() {
    fetch(BASE+'/api/mappings/index')
        .then(r => r.json())
        .then(data => {
            mappingsIndex = data;
            displayMappingsIndex();
        })
        .catch(err => console.error('Erreur chargement index:', err));
}

function displayMappingsIndex() {
    const container = document.getElementById('mappingsListContainer');
    const mappings = mappingsIndex.mappings || [];
    
    if (mappings.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <p>Aucun mapping disponible</p>
                <p style="font-size:0.9rem;margin-top:0.5rem">Cliquez sur "Créer un mapping" pour commencer</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = mappings.map(mapping => `
        <div class="mapping-card">
            <div class="mapping-info">
                <div class="mapping-name">${mapping.name}</div>
                <span class="mapping-type">${mapping.type}</span>
            </div>
            ${mapping.is_default ? '<span style="color:#10b981;font-weight:600;font-size:0.85rem">✓ Défaut</span>' : `
            <button class="btn-delete" onclick="openDeleteMappingModal('${mapping.id}')">
                <span>🗑️</span>
                Supprimer
            </button>
            `}
        </div>
    `).join('');
}

function openCreateMappingModal() {
    document.getElementById('createMappingModal').style.display = 'block';
    document.getElementById('newMappingName').value = '';
    
    // Peupler la liste des mappings à copier
    const copySelect = document.getElementById('copyFromMapping');
    const selectedType = document.getElementById('newMappingType').value;
    
    copySelect.innerHTML = '<option value="">Mapping vide</option>';
    
    if (mappingsIndex.mappings) {
        mappingsIndex.mappings
            .filter(m => m.type === selectedType)
            .forEach(mapping => {
                const option = document.createElement('option');
                option.value = mapping.id;
                option.textContent = mapping.name + (mapping.is_default ? ' (Défaut)' : '');
                copySelect.appendChild(option);
            });
    }
}

// Mettre à jour les options de copie quand le type change
document.addEventListener('DOMContentLoaded', function() {
    const typeSelect = document.getElementById('newMappingType');
    if (typeSelect) {
        typeSelect.addEventListener('change', function() {
            const copySelect = document.getElementById('copyFromMapping');
            const selectedType = this.value;
            
            copySelect.innerHTML = '<option value="">Mapping vide</option>';
            
            if (mappingsIndex.mappings) {
                mappingsIndex.mappings
                    .filter(m => m.type === selectedType)
                    .forEach(mapping => {
                        const option = document.createElement('option');
                        option.value = mapping.id;
                        option.textContent = mapping.name + (mapping.is_default ? ' (Défaut)' : '');
                        copySelect.appendChild(option);
                    });
            }
        });
    }
});

function closeCreateMappingModal() {
    document.getElementById('createMappingModal').style.display = 'none';
}

function confirmCreateMapping() {
    const name = document.getElementById('newMappingName').value.trim();
    const type = document.getElementById('newMappingType').value;
    const copyFrom = document.getElementById('copyFromMapping').value;
    
    if (!name) {
        alert('Veuillez entrer un nom pour le mapping');
        return;
    }
    
    const payload = { name, type };
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
            loadMappingsIndex();
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
    document.getElementById('deleteMappingType').textContent = mapping.type;
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
            loadMappingsIndex();
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
            const allMappings = data.mappings || [];
            
            // Mettre à jour le dropdown dans l'onglet Contrôle
            const controleSelect = document.getElementById('typeFormulaire');
            if (controleSelect) {
                updateSingleDropdown(controleSelect, allMappings);
            }
            
            // Mettre à jour le dropdown dans l'onglet Paramétrage
            const paramSelect = document.getElementById('typeFormulaireParam');
            if (paramSelect) {
                updateSingleDropdown(paramSelect, allMappings);
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
        
        // Convertir filename en value compatible
        let value = 'simple';
        if (mapping.filename.includes('groupee')) value = 'groupee';
        else if (mapping.filename.includes('ventesdiverses')) value = 'ventesdiverses';
        else if (mapping.filename.includes('custom')) {
            const match = mapping.filename.match(/custom_(\w+)_([a-f0-9]+)/);
            if (match) {
                value = `custom_${match[1]}_${match[2]}`;
            }
        }
        
        option.value = value;
        option.textContent = mapping.name + (mapping.is_default ? '' : ' ✨');
        option.dataset.filename = mapping.filename;
        option.dataset.type = mapping.type;
        
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
window.onclick = function(event) {
    const createModal = document.getElementById('createMappingModal');
    const deleteModal = document.getElementById('deleteMappingModal');
    const editModal = document.getElementById('editModal');
    const restoreModal = document.getElementById('restoreModal');
    const ruleModal = document.getElementById('editRuleModal');
    
    if (event.target === createModal) {
        closeCreateMappingModal();
    }
    if (event.target === deleteModal) {
        closeDeleteMappingModal();
    }
    if (event.target === editModal) {
        editModal.style.display = 'none';
    }
    if (event.target === restoreModal) {
        restoreModal.style.display = 'none';
    }
    if (event.target === ruleModal) {
        ruleModal.style.display = 'none';
    }
}

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
help.innerHTML='<strong>Mode RDI</strong><ul><li>Présence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ul>';
groupePdf.style.display='none';
groupeCii.style.display='none';
groupeRdi.style.display='flex';
}else if(type==='cii'){
help.innerHTML='<strong>Mode CII - GCP</strong><ul><li>Controle du XML CII (Cross Industry Invoice) directement</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ul>';
groupePdf.style.display='none';
groupeCii.style.display='flex';
groupeRdi.style.display='none';
}else if(type==='xmlonly'){
help.innerHTML='<strong>Mode XML - Vérif facture uniquement</strong><ul><li>Controle du XML encapsule dans le PDF</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Regles metiers</li></ul>';
groupePdf.style.display='flex';
groupeCii.style.display='none';
groupeRdi.style.display='none';
}else{
help.innerHTML='<strong>Mode RDI vs XML</strong><ul><li>Comparaison sortie SAP vs sortie Exstream</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li><li>Comparaison RDI vs XML</li></ul>';
groupePdf.style.display='flex';
groupeCii.style.display='none';
groupeRdi.style.display='flex';
}
}
document.getElementById('typeControle').addEventListener('change',updateHelp);
updateHelp();

/* ---- AFFICHER/MASQUER BOUTON TELECHARGER XML ---- */
document.getElementById('pdfFile').addEventListener('change',function(){
var btn=document.getElementById('btnDownloadXml');
var file=this.files[0];
btn.style.display=(file && file.name.toLowerCase().endsWith('.pdf'))?'inline-block':'none';
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
'<th class="col-valeurs">Valeurs</th>'+
'<th class="col-regles">Règles testées</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
nonArticleChamps.forEach(function(r){
var isXmlOnly=(data.type_controle==='cii'||data.type_controle==='xmlonly');
var tooltipContent='';
var valHtml='';
if(!isXmlOnly){
var rdiVal=r.rdi||'(vide)';
tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+rdiVal;
valHtml+='<div class="val-line" title="RDI: '+rdiVal.replace(/"/g,'&quot;')+'"><span class="val-label">RDI:</span> '+rdiVal+'</div>';
}
if(data.type_controle==='xml'||isXmlOnly){
var xmlVal=r.xml||'(vide)';
if(tooltipContent)tooltipContent+='<br>';
tooltipContent+='<strong>XML:</strong> '+r.xml_tag_name+' = '+xmlVal;
valHtml+='<div class="val-line" title="XML: '+xmlVal.replace(/"/g,'&quot;')+'"><span class="val-label">XML:</span> '+xmlVal+'</div>';
}
var statusIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':'❌');
var btLabel=r.obligatoire==='Oui'?'<span class="bt-oblig">'+r.balise+'</span>':r.balise;
var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':'');
var hasErrors=r.details_erreurs&&r.details_erreurs.length>0;
var errClass=hasErrors?'col-erreurs':'col-erreurs-hidden';
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-bt"><strong>'+btLabel+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td class="col-valeurs">'+valHtml+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td class="'+errClass+'"><ul>';
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
'<th class="col-libelle">Libellé</th>'+
'<th class="col-valeurs">Valeurs</th>'+
'<th class="col-regles">Règles testées</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
artChamps.forEach(function(r){
var isXmlOnly=(data.type_controle==='cii'||data.type_controle==='xmlonly');
var tooltipContent='';
var valHtml='';
if(!isXmlOnly){
var rdiVal=r.rdi||'(vide)';
tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+rdiVal;
valHtml+='<div class="val-line" title="RDI: '+rdiVal.replace(/"/g,'&quot;')+'"><span class="val-label">RDI:</span> '+rdiVal+'</div>';
}
if(data.type_controle==='xml'||isXmlOnly){
var xmlVal=r.xml||'(vide)';
if(tooltipContent)tooltipContent+='<br>';
tooltipContent+='<strong>XML:</strong> '+r.xml_tag_name+' = '+xmlVal;
valHtml+='<div class="val-line" title="XML: '+xmlVal.replace(/"/g,'&quot;')+'"><span class="val-label">XML:</span> '+xmlVal+'</div>';
}
var statusIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':'❌');
var btLabel=r.obligatoire==='Oui'?'<span class="bt-oblig">'+r.balise+'</span>':r.balise;
var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':'');
var hasErrors=r.details_erreurs&&r.details_erreurs.length>0;
var errClass=hasErrors?'col-erreurs':'col-erreurs-hidden';
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-bt"><strong>'+btLabel+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td class="col-valeurs">'+valHtml+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td class="'+errClass+'"><ul>';
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
tooltip.style.left=(e.pageX+14)+'px';
tooltip.style.top=(e.pageY+14)+'px';
});
row.addEventListener('mousemove',function(e){
tooltip.style.left=(e.pageX+14)+'px';
tooltip.style.top=(e.pageY+14)+'px';
});
row.addEventListener('mouseleave',function(){tooltip.style.display='none'});
});
cont.appendChild(div);
}
document.getElementById('results').style.display='block';

// Filtrage par BT et par erreurs
var searchInput=document.getElementById('searchBT');
var clearBtn=document.getElementById('btnClearSearch');
var filterErrorsCheckbox=document.getElementById('filterErrors');

function applyAllFilters(){
var searchTerm=searchInput.value.toLowerCase().trim();
var showErrorsOnly=filterErrorsCheckbox.checked;
if(searchTerm){
clearBtn.style.display='inline-block';
}else{
clearBtn.style.display='none';
}
filterResults(searchTerm,showErrorsOnly);
}

searchInput.addEventListener('input',applyAllFilters);
filterErrorsCheckbox.addEventListener('change',applyAllFilters);
clearBtn.addEventListener('click',function(){
searchInput.value='';
clearBtn.style.display='none';
applyAllFilters();
});

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

function filterResults(term,errorsOnly){
var categories=document.querySelectorAll('.category');
var visibleCount=0;
categories.forEach(function(cat){
var hasMatch=false;
// Filtrer les lignes standard (hors articles)
var rows=cat.querySelectorAll('.main-table > tbody > .data-row, table.main-table > tbody > .data-row');
rows.forEach(function(row){
var btStrong=row.querySelector('td:nth-child(2) strong');
if(!btStrong) return;
var btText=btStrong.textContent.toLowerCase();
var statusIcon=row.querySelector('.col-status').textContent.trim();
var isError=(statusIcon==='❌');
var nextRow=row.nextElementSibling;
var isCegedimRow=nextRow && nextRow.querySelector('.ceg-table');
var matchesSearch=!term||btText.includes(term);
var matchesErrorFilter=!errorsOnly||isError;
if(matchesSearch&&matchesErrorFilter){
row.style.display='';
if(isCegedimRow){nextRow.style.display='';}
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
var statusIcon=row.querySelector('.col-status').textContent.trim();
var isError=(statusIcon==='❌');
var matchesSearch=!term||btText.includes(term);
var matchesErrorFilter=!errorsOnly||isError;
if(matchesSearch&&matchesErrorFilter){
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
block.style.display=errorsOnly||term?'none':'';
}
});
if(hasMatch){
cat.classList.remove('hidden');
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
async function loadMappings(){
var type=document.getElementById('typeFormulaireParam').value;
var resp=await fetch(BASE+'/api/mapping/'+type);
currentMapping=await resp.json();
var list=document.getElementById('mappingList');
list.innerHTML='';
if(!currentMapping||!currentMapping.champs){list.innerHTML='<li>Aucun mapping</li>';return}
currentMapping.champs.forEach(function(champ,index){
var li=document.createElement('li');
var isValide=champ.valide===true;
var isIgnored=(champ.ignore==='Oui');
li.className='mapping-item'+(isValide?' valide':'');
li.draggable=true;
li.setAttribute('data-index',index);
li.innerHTML=
'<div class="mapping-item-info">'+
'<div class="item-main"><strong>'+champ.balise+'</strong> — '+champ.libelle+'</div>'+
'<div class="item-sub">RDI: <code>'+champ.rdi+'</code> | Type: '+champ.type+' | Oblig.: '+champ.obligatoire+' | Ignoré : '+(isIgnored?'Oui':'Non')+'</div>'+
'<div class="item-xpath">XPath: '+(champ.xpath||'—')+'</div>'+
'</div>'+
'<div class="mapping-actions">'+
'<label class="valide-toggle">'+
'<input type="checkbox" class="chk-valide" data-index="'+index+'"'+(isValide?' checked':'')+'> Valide'+
'</label>'+
'<button class="btn-edit" data-index="'+index+'">Editer</button>'+
'<button class="btn-delete" data-index="'+index+'">Supprimer</button>'+
'</div>';
list.appendChild(li);

// Drag and drop events
li.addEventListener('dragstart',function(e){
this.classList.add('dragging');
e.dataTransfer.effectAllowed='move';
e.dataTransfer.setData('text/html',this.innerHTML);
});
li.addEventListener('dragend',function(e){
this.classList.remove('dragging');
document.querySelectorAll('.mapping-item').forEach(function(item){
item.classList.remove('drag-over');
});
});
li.addEventListener('dragover',function(e){
e.preventDefault();
e.dataTransfer.dropEffect='move';
var dragging=document.querySelector('.dragging');
if(dragging&&dragging!==this){
this.classList.add('drag-over');
}
});
li.addEventListener('dragleave',function(e){
this.classList.remove('drag-over');
});
li.addEventListener('drop',async function(e){
e.preventDefault();
this.classList.remove('drag-over');
var dragging=document.querySelector('.dragging');
if(dragging&&dragging!==this){
var fromIndex=parseInt(dragging.getAttribute('data-index'));
var toIndex=parseInt(this.getAttribute('data-index'));
// Réorganiser le tableau
var item=currentMapping.champs.splice(fromIndex,1)[0];
currentMapping.champs.splice(toIndex,0,item);
await saveMapping();
loadMappings();
}
});
});
document.querySelectorAll('.chk-valide').forEach(function(chk){
chk.addEventListener('change',async function(){
var idx=parseInt(this.getAttribute('data-index'));
currentMapping.champs[idx].valide=this.checked;
await saveMapping();
loadMappings();
});
});
document.querySelectorAll('.btn-edit').forEach(function(btn){
btn.addEventListener('click',function(){editMapping(this.getAttribute('data-index'))});
});
document.querySelectorAll('.btn-delete').forEach(function(btn){
btn.addEventListener('click',function(){deleteMapping(this.getAttribute('data-index'))});
});
}

function editMapping(index){
currentIndex=parseInt(index);
var champ=currentMapping.champs[currentIndex];
document.getElementById('modalTitle').textContent='Editer le Champ';
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
document.getElementById('editXpath').value=(champ.xpath||'').replace(/^\/\//,'');
document.getElementById('editAttribute').value=champ.attribute||'';
document.getElementById('editType').value=champ.type;
document.getElementById('editObligatoire').value=champ.obligatoire;
document.getElementById('editIgnore').value=champ.ignore||'Non';
document.getElementById('editRdg').value=champ.rdg||'';
document.getElementById('editModal').style.display='block';
}
async function deleteMapping(index){
if(!confirm('Supprimer ce champ?'))return;
currentMapping.champs.splice(parseInt(index),1);
await saveMapping();
loadMappings();
}
document.getElementById('btnAdd').addEventListener('click',function(){
currentIndex=null;
document.getElementById('modalTitle').textContent='Ajouter un Champ';
document.getElementById('editBalise').value='';
document.getElementById('editLibelle').value='';
document.getElementById('editCategorie').value='BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE';
document.getElementById('editRdi').value='';
document.getElementById('editXpath').value='';
document.getElementById('editAttribute').value='';
document.getElementById('editType').value='String';
document.getElementById('editObligatoire').value='Non';
document.getElementById('editIgnore').value='Non';
document.getElementById('editRdg').value='';
document.getElementById('editModal').style.display='block';
});
document.getElementById('modalClose').addEventListener('click',function(){
document.getElementById('editModal').style.display='none';
});
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
xpath:document.getElementById('editXpath').value,
attribute:document.getElementById('editAttribute').value||undefined,
type:document.getElementById('editType').value,
obligatoire:document.getElementById('editObligatoire').value,
ignore:document.getElementById('editIgnore').value,
rdg:document.getElementById('editRdg').value,
categorie_bg:categorieBg,
categorie_titre:categorieTitre,
controles_cegedim:base.controles_cegedim||[],
valide:base.valide||false
};
if(currentIndex!==null){
currentMapping.champs[currentIndex]=newChamp;
}else{
currentMapping.champs.push(newChamp);
}
await saveMapping();
document.getElementById('editModal').style.display='none';
loadMappings();
});

// Télécharger le JSON
document.getElementById('btnDownload').addEventListener('click',function(){
var type=document.getElementById('typeFormulaireParam').value;
var dataStr=JSON.stringify(currentMapping,null,2);
var dataUri='data:application/json;charset=utf-8,'+encodeURIComponent(dataStr);
var exportFileDefaultName='mapping_v5_'+type+'.json';
var linkElement=document.createElement('a');
linkElement.setAttribute('href',dataUri);
linkElement.setAttribute('download',exportFileDefaultName);
linkElement.click();
});

// Sauvegarder une version horodatée
document.getElementById('btnSaveVersion').addEventListener('click',async function(){
var type=document.getElementById('typeFormulaireParam').value;
var resp=await fetch(BASE+'/api/mapping/'+type+'/version',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentMapping)
});
var result=await resp.json();
if(result.success){
alert('Version sauvegardée : '+result.filename);
}else{
alert('Erreur : '+(result.error||'Impossible de sauvegarder'));
}
});

// Restaurer une version
document.getElementById('btnRestore').addEventListener('click',async function(){
var type=document.getElementById('typeFormulaireParam').value;
var resp=await fetch(BASE+'/api/mapping/'+type+'/versions');
var versions=await resp.json();
var list=document.getElementById('versionsList');
list.innerHTML='';
if(versions.length===0){
list.innerHTML='<p>Aucune version sauvegardée</p>';
}else{
versions.forEach(function(v){
var div=document.createElement('div');
div.className='version-item';
var date=v.timestamp.substring(0,8);
var time=v.timestamp.substring(9);
var displayDate=date.substring(6,8)+'/'+date.substring(4,6)+'/'+date.substring(0,4);
var displayTime=time.substring(0,2)+':'+time.substring(2,4)+':'+time.substring(4,6);
div.innerHTML='<div class="version-info">'+
'<div class="version-timestamp">'+displayDate+' '+displayTime+'</div>'+
'<div class="version-details">'+v.filename+' ('+Math.round(v.size/1024)+' Ko)</div>'+
'</div>'+
'<button class="btn-secondary btn-restore-version" data-filename="'+v.filename+'">Restaurer</button>';
list.appendChild(div);
});
document.querySelectorAll('.btn-restore-version').forEach(function(btn){
btn.addEventListener('click',async function(){
if(!confirm('Restaurer cette version ? La version actuelle sera remplacée.')){
return;
}
var filename=this.getAttribute('data-filename');
var resp=await fetch(BASE+'/api/mapping/'+type+'/restore',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({filename:filename})
});
var result=await resp.json();
if(result.success){
alert('Version restaurée avec succès');
document.getElementById('restoreModal').style.display='none';
loadMappings();
}else{
alert('Erreur : '+(result.error||'Impossible de restaurer'));
}
});
});
}
document.getElementById('restoreModal').style.display='block';
});
document.getElementById('restoreModalClose').addEventListener('click',function(){
document.getElementById('restoreModal').style.display='none';
});

async function saveMapping(){
var type=document.getElementById('typeFormulaireParam').value;
await fetch(BASE+'/api/mapping/'+type,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentMapping)
});
}
document.getElementById('btnReload').addEventListener('click',loadMappings);
document.getElementById('typeFormulaireParam').addEventListener('change',loadMappings);

/* ---- RECHERCHE BT PARAMETRAGE ---- */
document.getElementById('searchBTParam').addEventListener('input',function(){
var query=this.value.toLowerCase().trim();
var btn=document.getElementById('btnClearSearchParam');
if(query){
btn.style.display='block';
var items=document.querySelectorAll('.mapping-item');
items.forEach(function(item){
var baliseEl=item.querySelector('.item-main strong');
var balise=baliseEl?baliseEl.textContent.toLowerCase():'';
if(balise.includes(query)){
item.style.display='flex';
}else{
item.style.display='none';
}
});
}else{
btn.style.display='none';
var items=document.querySelectorAll('.mapping-item');
items.forEach(function(item){
item.style.display='flex';
});
}
});
document.getElementById('btnClearSearchParam').addEventListener('click',function(){
document.getElementById('searchBTParam').value='';
this.style.display='none';
var items=document.querySelectorAll('.mapping-item');
items.forEach(function(item){
item.style.display='flex';
});
});

/* ---- RÈGLES MÉTIERS ---- */
var currentRules={rules:[]};
var availableBTs=[];

async function loadAvailableBTs(){
// Charger tous les BT depuis tous les mappings
var types=['simple','groupee','ventesdiverses'];
var allBTs={};
for(var i=0;i<types.length;i++){
try{
var resp=await fetch(BASE+'/api/mapping/'+types[i]);
var mapping=await resp.json();
if(mapping&&mapping.champs){
mapping.champs.forEach(function(champ){
if(champ.balise){
allBTs[champ.balise]=champ.libelle||champ.balise;
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
}

async function loadRules(){
await loadAvailableBTs();
var resp=await fetch(BASE+'/api/rules');
currentRules=await resp.json();
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
filteredRules.forEach(function(rule){
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
var formLabels={'simple':'CART Simple','groupee':'CART Groupée','ventesdiverses':'Ventes Diverses'};
formsText='<span style="color:#666;font-size:0.85em">'+forms.map(function(f){return formLabels[f]||f}).join(', ')+'</span>';
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
}else if(a.type==='must_equal'){
actionsText+=a.field+' doit égaler "'+a.value+'"';
}else if(a.type==='must_be_negative'){
actionsText+=a.field+' doit être négatif';
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
document.getElementById('ruleEnabled').checked=true;
document.getElementById('ruleFormSimple').checked=true;
document.getElementById('ruleFormGroupee').checked=true;
document.getElementById('ruleFormVentes').checked=true;
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
document.getElementById('ruleEnabled').checked=rule.enabled!==false;
var forms=rule.applicable_forms||[];
document.getElementById('ruleFormSimple').checked=forms.length===0||forms.includes('simple');
document.getElementById('ruleFormGroupee').checked=forms.length===0||forms.includes('groupee');
document.getElementById('ruleFormVentes').checked=forms.length===0||forms.includes('ventesdiverses');
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
// Construire les options dynamiquement
var fieldOptions='<option value="">Champ...</option>';
availableBTs.forEach(function(bt){
fieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
div.innerHTML='<select class="cond-field" data-index="'+i+'">'+
fieldOptions+
'</select>'+
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
// Construire les options dynamiquement avec libellés complets
var fieldOptions='<option value="">Champ...</option>';
availableBTs.forEach(function(bt){
fieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
var needsValue=(action.type==='must_equal');
// ORDRE: Champ, Type d'action, Valeur (si nécessaire), Supprimer
div.innerHTML='<select class="action-field" data-index="'+i+'">'+fieldOptions+'</select>'+
'<select class="action-type" data-index="'+i+'">'+
'<option value="make_mandatory">Rendre obligatoire</option>'+
'<option value="must_equal">Doit égaler</option>'+
'<option value="must_be_negative">Doit être négatif</option>'+
'</select>'+
(needsValue?'<input type="text" class="action-value" data-index="'+i+'" placeholder="Valeur" value="'+(action.value||'')+'">':'')+
'<button class="btn-remove" data-index="'+i+'">Supprimer</button>';
container.appendChild(div);
div.querySelector('.action-field').value=action.field;
div.querySelector('.action-type').value=action.type;
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
document.querySelectorAll('.action-item .btn-remove').forEach(function(btn){
btn.addEventListener('click',function(){
editingActions.splice(parseInt(this.getAttribute('data-index')),1);
renderActions();
});
});
}

document.getElementById('btnAddCondition').addEventListener('click',function(){
editingConditions.push({field:'',operator:'equals',value:''});
renderConditions();
});

document.getElementById('btnAddAction').addEventListener('click',function(){
editingActions.push({type:'make_mandatory',field:''});
renderActions();
});

document.getElementById('ruleModalClose').addEventListener('click',function(){
document.getElementById('editRuleModal').style.display='none';
});

document.getElementById('btnSaveRule').addEventListener('click',function(){
var applicableForms=[];
if(document.getElementById('ruleFormSimple').checked)applicableForms.push('simple');
if(document.getElementById('ruleFormGroupee').checked)applicableForms.push('groupee');
if(document.getElementById('ruleFormVentes').checked)applicableForms.push('ventesdiverses');
var rule={
id:currentRuleIndex!==null?currentRules.rules[currentRuleIndex].id:'rule_'+Date.now(),
name:document.getElementById('ruleName').value,
description:document.getElementById('ruleDescription').value,
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
    return send_from_directory(SCRIPT_DIR, filename)

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

@app.route('/api/rules', methods=['GET'])
def get_rules():
    rules = load_business_rules()
    return jsonify(rules)

@app.route('/api/rules', methods=['POST'])
def save_rules():
    rules_data = request.json
    success = save_business_rules(rules_data)
    return jsonify({'success': success})

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
        rdi_path = None
        if rdi_file:
            rdi_path = os.path.join(UPLOAD_FOLDER, rdi_file.filename)
            rdi_file.save(rdi_path)
            rdi_data, rdi_articles = parse_rdi(rdi_path)
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
        namespaces = {
            'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
            'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
            'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100'
        }

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
            rdi_value = rdi_data.get(rdi_field_name, '').strip()
            if not rdi_value and rdi_field_name:
                for key in rdi_data.keys():
                    if key.upper() == rdi_field_name.upper():
                        rdi_value = rdi_data[key].strip()
                        break

            xml_value = ''
            if xml_doc is not None:
                try:
                    _xpath_raw = field.get('xpath', '') or ''
                    if _xpath_raw:
                        compiled = xpath_cache.get(_xpath_raw)
                        if compiled is not None:
                            elements = compiled(xml_doc)
                            if elements:
                                attribute = field.get('attribute')
                                if attribute and hasattr(elements[0], 'get'):
                                    xml_value = elements[0].get(attribute, '').strip()
                                elif hasattr(elements[0], 'text') and elements[0].text:
                                    xml_value = elements[0].text.strip()
                except:
                    pass

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
                'rdi_field': rdi_field_name,
                'xml_short_name': xml_short_name,
                'xml_tag_name': xml_tag_name,
                'status': status,
                'regles_testees': regles_testees,
                'details_erreurs': details_erreurs,
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

        stats = {
            'total': len(results),
            'ok': sum(1 for r in results if r['status'] == 'OK'),
            'erreur': sum(1 for r in results if r['status'] == 'ERREUR'),
            'ignore': sum(1 for r in results if r['status'] == 'IGNORE'),
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

        return jsonify({
            'results': results,
            'stats': stats,
            'categories_results': dict(categories_results),
            'type_controle': type_controle
        })
    except Exception as e:
        print(f"ERREUR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

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
        data = request.json
        name = data.get('name')
        mapping_type = data.get('type', 'CART Simple')
        copy_from = data.get('copy_from', None)  # ID du mapping à copier
        
        if not name:
            return jsonify({'success': False, 'error': 'Nom requis'})
        
        # Charger l'index
        index = load_mappings_index()
        
        # Créer un nouvel ID
        from datetime import datetime
        import uuid
        new_id = str(uuid.uuid4())[:8]
        
        # Déterminer le filename basé sur le type
        type_map = {
            'CART Simple': 'simple',
            'CART Groupée': 'groupee',
            'Ventes Diverses': 'ventesdiverses'
        }
        type_key = type_map.get(mapping_type, 'simple')
        
        # Créer le mapping
        new_mapping = {
            "id": new_id,
            "name": name,
            "type": mapping_type,
            "filename": f"mapping_custom_{type_key}_{new_id}.json",
            "created_date": datetime.now().strftime('%Y-%m-%d'),
            "is_default": False
        }
        
        # Ajouter à l'index
        index['mappings'].append(new_mapping)
        save_mappings_index(index)
        
        # Créer le fichier de mapping
        mapping_data = {"champs": []}
        
        if copy_from:
            # Copier depuis un mapping existant
            source_mapping = next((m for m in index['mappings'] if m['id'] == copy_from), None)
            if source_mapping:
                source_path = os.path.join(SCRIPT_DIR, source_mapping['filename'])
                if os.path.exists(source_path):
                    try:
                        with open(source_path, 'r', encoding='utf-8') as f:
                            mapping_data = json.load(f)
                    except:
                        pass
        else:
            # Créer vide ou copier le défaut du type
            default_mapping_file = f'mapping_v5_{type_key}.json'
            default_mapping_path = os.path.join(SCRIPT_DIR, default_mapping_file)
            
            if os.path.exists(default_mapping_path):
                try:
                    with open(default_mapping_path, 'r', encoding='utf-8') as f:
                        mapping_data = json.load(f)
                except:
                    pass
        
        mapping_path = os.path.join(SCRIPT_DIR, new_mapping['filename'])
        with open(mapping_path, 'w', encoding='utf-8') as f:
            json.dump(mapping_data, f, ensure_ascii=False, indent=2)
        
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
        
        # Charger l'index
        index = load_mappings_index()
        
        # Trouver le mapping
        mapping = next((m for m in index['mappings'] if m['id'] == mapping_id), None)
        if not mapping:
            return jsonify({'success': False, 'error': 'Mapping non trouvé'})
        
        # Interdire suppression du mapping par défaut
        if mapping.get('is_default'):
            return jsonify({'success': False, 'error': 'Impossible de supprimer un mapping par défaut'})
        
        # Supprimer le fichier
        mapping_path = os.path.join(SCRIPT_DIR, mapping['filename'])
        if os.path.exists(mapping_path):
            os.remove(mapping_path)
        
        # Retirer de l'index
        index['mappings'] = [m for m in index['mappings'] if m['id'] != mapping_id]
        save_mappings_index(index)
        
        return jsonify({'success': True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

# ===== FIN NOUVELLES ROUTES API =====

if __name__ == '__main__':
    print("="*60)
    print("APPLICATION FACTUR-X V12.0 - Enhanced Mapping Management")
    print("Ouvrez ce lien dans votre navigateur : http://localhost:5000")
    print("="*60)
    app.run(debug=True, host='0.0.0.0', port=5000)
