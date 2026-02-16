#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Factur-X V12.0"""
from flask import Flask, render_template_string, request, jsonify
import os, json, PyPDF2
import logging
from lxml import etree
from collections import defaultdict

app = Flask(__name__)

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

print(f"[FACTURX] Dossier de travail : {SCRIPT_DIR}")

def load_mapping(type_formulaire='CARTsimple'):
    filepath = os.path.join(SCRIPT_DIR, f'mapping_v5_{type_formulaire}.json')
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return None

def save_mapping(data, type_formulaire='simple'):
    filepath = os.path.join(SCRIPT_DIR, f'mapping_v5_{type_formulaire}.json')
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except:
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
    data = {}
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
                                if tag not in data:
                                    data[tag] = value
                        except:
                            pass
    except:
        pass
    return data

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
        if '.' in value_str and ',' in value_str:
            value_str = value_str.replace('.', '').replace(',', '.')
        elif ',' in value_str and '.' not in value_str:
            value_str = value_str.replace(',', '.')
        elif value_str.count('.') > 1:
            value_str = value_str.replace('.', '')
        try:
            num_value = float(value_str)
            if '.' in value_str:
                return f"{num_value:.10f}".rstrip('0').rstrip('.')
            else:
                return str(num_value)
        except ValueError:
            pass
    return value_str.upper()

def perform_controls(field, rdi_value, xml_value, type_controle):
    regles_testees = []
    details_erreurs = []
    status = 'OK'

    if field.get('obligatoire') == 'Oui':
        regles_testees.append('Presence obligatoire')
        if not rdi_value:
            status = 'ERREUR'
            details_erreurs.append('Champ obligatoire absent du RDI')

    # Ajouter les Règles de Gestion (RDG) dans les règles testées
    if field.get('rdg'):
        rdg_text = field['rdg']
        # Si trop long, tronquer intelligemment
        if len(rdg_text) > 100:
            regles_testees.append(f"{rdg_text[:100]}...")
        else:
            regles_testees.append(rdg_text)

    # Traiter les contrôles CEGEDIM mais ne PAS les ajouter aux regles_testees
    # (ils seront visibles dans le tableau dédié CEGEDIM)
    controles_cegedim = field.get('controles_cegedim', [])
    for controle in controles_cegedim:
        if controle.get('nature') == 'Presence':
            if not rdi_value:
                status = 'ERREUR'
                details_erreurs.append(f"{controle.get('ref')}: {controle.get('message', 'Controle CEGEDIM echoue')}")

    if type_controle == 'xml':
        regles_testees.append('Comparaison RDI vs XML')
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


def apply_contextual_controls(results):
    """
    Controles conditionnels en dur :
    1. BT-21-BAR = "Chorus"   -> BT-10, BT-13, BT-29, BT-29-1 obligatoires
    2. Avoir (BT-3 = "381")   -> BT-25, BT-26 obligatoires
    3. BT-8 doit toujours valoir "5"
    4. Client etranger (BT-48 ne commence pas par "FR") -> BT-58 obligatoire
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
    # Regle 1 : BT-21-BAR = "Chorus"
    # -------------------------------------------------------
    bt21 = by_balise.get('BT-21-BAR')
    if bt21 and bt21.get('rdi', '').strip().lower() == 'chorus':
        for balise in ['BT-10', 'BT-13', 'BT-29', 'BT-29-1']:
            force_obligatoire(balise, 'BT-21-BAR = Chorus')

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

    return results


HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link rel="icon" type="image/png" href="/img/AppLogo_V2.png">
<title>Facturix</title>
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
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:30px}
.stat-card{background:#fff;padding:20px;border-radius:10px;text-align:center}
.stat-value{font-size:2em;font-weight:bold}
.ok .stat-value{color:#70ad47}
.erreur .stat-value{color:#c00000}
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
table.main-table th{background:#366092;color:#fff;padding:12px;text-align:left;font-weight:600;font-size:1em}
table.main-table td{padding:12px;border-bottom:1px solid #eee;vertical-align:top}
table.main-table tr.data-row:hover{background:#f0f4ff}
.col-status{width:32px;text-align:center;font-size:1.4em;padding:6px!important}
.col-oblig{width:38px;text-align:center;font-size:1.2em;padding:6px!important}
.col-bt{width:85px}
.col-libelle{width:200px}
.col-regles{width:330px}
.col-erreurs{width:230px}
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
.modal-content{background:#fff;margin:3% auto;padding:25px;border-radius:12px;max-width:700px;max-height:92vh;overflow-y:auto}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
.version-item{padding:12px;margin:8px 0;border-radius:6px;background:#f5f5f5;display:flex;justify-content:space-between;align-items:center}
.version-item:hover{background:#e8f5e9}
.version-info{flex:1}
.version-timestamp{font-weight:600;color:#366092}
.version-details{font-size:0.85em;color:#666;margin-top:4px}
.btn-group{display:flex;gap:10px;margin-bottom:15px}
.modal-close{font-size:2em;cursor:pointer;color:#999;line-height:1}
.modal .form-group{margin-bottom:12px}
.modal .form-group label{font-weight:600;margin-bottom:5px;font-size:0.9em}
.modal .form-group input,.modal .form-group select{padding:8px;border:2px solid #366092;border-radius:6px;font-size:0.95em}
.modal .form-group textarea{padding:8px;border:2px solid #366092;border-radius:6px;font-size:0.85em;min-height:55px;font-family:monospace}

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
    <img src="/img/BigPicture.png" alt="Easter egg !">
  </div>
  <div class="konami-close" onclick="document.getElementById('konamiOverlay').classList.remove('visible')">
    ↑↑↓↓←→←→ B A — Cliquez pour fermer
  </div>
</div>
<div class="gaulois-overlay" id="gauloisOverlay">
<div class="gaulois-card">
<img id="gauloisImg" src="/img/0-25.jpg" alt="Gaulois">
</div>
</div>
<div class="sidebar">
</div>
<div class="main-wrap">
<div class="container">
<div class="header">
<div class="header-left">
<img class="header-logo" src="/img/AppLogo_V2.png" alt="Logo"><div class="header-text"><h1>Facturix — Controle Factur-X</h1>
<div class="version">V12.0 — Made with love by Julien ❤️</div></div>
</div>
<div class="header-banner" onclick="document.getElementById('konamiOverlay').classList.add('visible')">
<img src="/img/TopLogo.png" alt="On va vérifier tes factures, par Bélénos !">
</div>
</div>
<div class="tabs">
<button class="tab active" id="tabControle">Controle</button>
<button class="tab" id="tabParam">Parametrage</button>
<button class="tab" id="tabAide">Aide</button>
</div>

<!-- ONGLET CONTROLE -->
<div id="contentControle" class="tab-content active">
<div class="section">
<h2>Configuration</h2>
<div class="form-row">
<div class="form-group">
<label>Type de Formulaire SAP :</label>
<select id="typeFormulaire">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupe</option>
</select>
</div>
<div class="form-group">
<label>Type de Controle :</label>
<select id="typeControle">
<option value="xml">XML - Sortie Exstream (complet)</option>
<option value="rdi">RDI - Sortie SAP</option>
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
</div>
<div class="form-group">
<label>Fichier RDI :</label>
<input type="file" id="rdiFile" accept=".txt,.rdi">
</div>
</div>
<button class="btn" id="btnControle">LANCER LE CONTROLE</button>
</div>
<div class="loading" id="loading"><div class="spinner"></div><p>Controle en cours...</p></div>
<div class="results" id="results">
<div class="progress-section">
<div class="progress-label-row">
<h3>Taux de conformite</h3>
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
</div>
</div>
<div class="section">
<div class="search-box">
<label for="searchBT">🔍 Rechercher un BT :</label>
<input type="text" id="searchBT" placeholder="Tapez un numéro de BT (ex: 48)">
<button class="btn-clear" id="btnClearSearch" style="display:none">✕ Effacer</button>
</div>
</div>
<div class="section"><div id="categoriesContainer"></div></div>
</div>
</div>

<!-- ONGLET PARAMETRAGE -->
<div id="contentParam" class="tab-content">
<div class="section">
<h2>Gestion des Mappings</h2>
<div class="form-row">
<div class="form-group">
<label>Type de formulaire :</label>
<select id="typeFormulaireParam">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupe</option>
</select>
</div>
</div>
<div class="btn-group">
<button class="btn-secondary" id="btnReload">Actualiser</button>
<button class="btn-add" id="btnAdd">+ Ajouter un champ</button>
<button class="btn-download" id="btnDownload">📥 Télécharger JSON</button>
<button class="btn-save-version" id="btnSaveVersion">💾 Sauvegarder version</button>
<button class="btn-restore" id="btnRestore">🕐 Restaurer version</button>
</div>
</div>
<div class="section">
<ul class="mapping-list" id="mappingList"></ul>
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
<li>Case a cocher "Valide" dans le parametrage, fond vert</li>
<li>Tableau CEGEDIM detaille par BT dans les resultats</li>
<li>XPath visible dans le parametrage</li>
<li>Stats simplifiees : Total / OK / Erreurs</li>
<li>Upload PDF masque en mode RDI</li>
</ul>
<h3>Mode RDI - Sortie SAP</h3>
<ol><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ol>
<h3>Mode XML - Sortie Exstream</h3>
<ol><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li><li>Comparaison RDI vs XML</li></ol>
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
<div class="form-group"><label>Type :</label>
<select id="editType"><option value="String">String</option><option value="Decimal">Decimal</option><option value="Date">Date</option></select>
</div>
<div class="form-group"><label>Obligatoire :</label>
<select id="editObligatoire"><option value="Oui">Oui</option><option value="Non">Non</option><option value="Dependant">Dependant</option></select>
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

<div id="tooltip" class="tooltip"></div>
<script>
var currentMapping=null;
var currentIndex=null;
var tooltip=document.getElementById('tooltip');

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
});
document.getElementById('tabAide').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentAide').classList.add('active');
});

/* ---- AIDE CONTEXTUELLE + MASQUAGE PDF ---- */
function updateHelp(){
var type=document.getElementById('typeControle').value;
var help=document.getElementById('helpControle');
var groupePdf=document.getElementById('groupePdf');
if(type==='rdi'){
help.innerHTML='<strong>Mode RDI</strong><ul><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ul>';
groupePdf.style.display='none';
}else{
help.innerHTML='<strong>Mode XML</strong><ul><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li><li>Comparaison RDI vs XML</li></ul>';
groupePdf.style.display='flex';
}
}
document.getElementById('typeControle').addEventListener('change',updateHelp);
updateHelp();

/* ---- LANCER CONTROLE ---- */
document.getElementById('btnControle').addEventListener('click',async function(){
var typeControle=document.getElementById('typeControle').value;
var pdf=document.getElementById('pdfFile').files[0];
var rdi=document.getElementById('rdiFile').files[0];
if(typeControle==='xml'&&!pdf){alert('Selectionnez le fichier PDF ou XML');return}
if(!rdi){alert('Selectionnez le fichier RDI');return}
document.getElementById('loading').style.display='block';
document.getElementById('results').style.display='none';
var fd=new FormData();
if(pdf)fd.append('pdf',pdf);
fd.append('rdi',rdi);
fd.append('type_formulaire',document.getElementById('typeFormulaire').value);
fd.append('type_controle',typeControle);
try{
var resp=await fetch('/controle',{method:'POST',body:fd});
var data=await resp.json();
if(data.error){alert('Erreur: '+data.error);return}
document.getElementById('statTotal').textContent=data.stats.total;
document.getElementById('statOk').textContent=data.stats.ok;
document.getElementById('statErreur').textContent=data.stats.erreur;
var pct=data.stats.total>0?Math.round(data.stats.ok/data.stats.total*100):0;
var fill=document.getElementById('progressFill');
document.getElementById('progressPct').textContent=pct+'%';
fill.style.width=pct+'%';
fill.className='progress-fill';
var gSrc,gMsg;
if(pct<25){gSrc='/img/0-25.jpg';fill.classList.add('pct-0');}
else if(pct<50){gSrc='/img/25-50.jpg';fill.classList.add('pct-25');}
else if(pct<75){gSrc='/img/50-75.jpg';fill.classList.add('pct-50');}
else{gSrc='/img/75-100.jpg';fill.classList.add('pct-75');}
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
for(var bgId in data.categories_results){
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
html+='<table class="main-table"><thead><tr>'+
'<th class="col-status"></th>'+
'<th class="col-oblig">Oblig.</th>'+
'<th class="col-bt">BT</th>'+
'<th class="col-libelle">Libelle</th>'+
'<th class="col-regles">Regles testees</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
cat.champs.forEach(function(r){
// AMÉLIORATION : Construction simplifiée de la tooltip
var tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+(r.rdi||'(vide)');
if(data.type_controle==='xml'){
tooltipContent+='<br><strong>XML:</strong> '+r.xml_tag_name+' = '+(r.xml||'(vide)');
}
var statusIcon=r.status==='OK'?'✅':'❌';
var obligIcon=r.obligatoire==='Oui'?'⚠️':'';
var rowBg=r.status==='ERREUR'?'background:#fff5f5':'';
/* Ligne principale */
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-oblig">'+obligIcon+'</td>'+
'<td><strong>'+r.balise+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td><ul>';
r.details_erreurs.forEach(function(err){html+='<li>'+err+'</li>'});
html+='</ul></td></tr>';
/* Sous-ligne CEGEDIM si des controles existent */
if(r.controles_cegedim&&r.controles_cegedim.length>0){
html+='<tr><td colspan="6" style="padding:0 12px 12px 40px;background:#faf8ff">'+
'<table class="ceg-table">'+
'<thead><tr><th>Ref</th><th>Categorie</th><th>Nature</th><th>Controle</th><th>Message</th></tr></thead><tbody>';
r.controles_cegedim.forEach(function(c){
html+='<tr>'+
'<td>'+( c.ref||'')+'</td>'+
'<td>'+(c.categorie||'')+'</td>'+
'<td>'+(c.nature||'')+'</td>'+
'<td>'+(c.description||c.controle||'')+'</td>'+
'<td>'+(c.message||'')+'</td>'+
'</tr>';
});
html+='</tbody></table></td></tr>';
}
});
html+='</tbody></table></div>';
div.innerHTML=html;
div.querySelector('.category-header').addEventListener('click',function(){
document.getElementById('cat-'+this.getAttribute('data-cat')).classList.toggle('open');
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

// Filtrage par BT
var searchInput=document.getElementById('searchBT');
var clearBtn=document.getElementById('btnClearSearch');
searchInput.addEventListener('input',function(){
var searchTerm=this.value.toLowerCase().trim();
if(searchTerm){
clearBtn.style.display='inline-block';
}else{
clearBtn.style.display='none';
}
filterResults(searchTerm);
});
clearBtn.addEventListener('click',function(){
searchInput.value='';
clearBtn.style.display='none';
filterResults('');
});

function filterResults(term){
var categories=document.querySelectorAll('.category');
var visibleCount=0;
categories.forEach(function(cat){
var hasMatch=false;
var rows=cat.querySelectorAll('.data-row');
rows.forEach(function(row){
var btText=row.querySelector('td:nth-child(3) strong').textContent.toLowerCase();
// Trouver la ligne CEGEDIM suivante (si elle existe)
var nextRow=row.nextElementSibling;
var isCegedimRow=nextRow && nextRow.querySelector('.ceg-table');
if(!term||btText.includes(term)){
row.style.display='';
// Afficher aussi la ligne CEGEDIM associée si elle existe
if(isCegedimRow){
nextRow.style.display='';
}
hasMatch=true;
}else{
row.style.display='none';
// Cacher aussi la ligne CEGEDIM associée si elle existe
if(isCegedimRow){
nextRow.style.display='none';
}
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
var resp=await fetch('/api/mapping/'+type);
currentMapping=await resp.json();
var list=document.getElementById('mappingList');
list.innerHTML='';
if(!currentMapping||!currentMapping.champs){list.innerHTML='<li>Aucun mapping</li>';return}
currentMapping.champs.forEach(function(champ,index){
var li=document.createElement('li');
var isValide=champ.valide===true;
li.className='mapping-item'+(isValide?' valide':'');
li.draggable=true;
li.setAttribute('data-index',index);
li.innerHTML=
'<div class="mapping-item-info">'+
'<div class="item-main"><strong>'+champ.balise+'</strong> — '+champ.libelle+'</div>'+
'<div class="item-sub">RDI: <code>'+champ.rdi+'</code> | Type: '+champ.type+' | Oblig.: '+champ.obligatoire+'</div>'+
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
document.getElementById('editType').value=champ.type;
document.getElementById('editObligatoire').value=champ.obligatoire;
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
document.getElementById('editType').value='String';
document.getElementById('editObligatoire').value='Non';
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
type:document.getElementById('editType').value,
obligatoire:document.getElementById('editObligatoire').value,
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
var resp=await fetch('/api/mapping/'+type+'/version',{
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
var resp=await fetch('/api/mapping/'+type+'/versions');
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
var resp=await fetch('/api/mapping/'+type+'/restore',{
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
await fetch('/api/mapping/'+type,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentMapping)
});
}
document.getElementById('btnReload').addEventListener('click',loadMappings);
document.getElementById('typeFormulaireParam').addEventListener('change',loadMappings);
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
    return render_template_string(HTML)

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

@app.route('/controle', methods=['POST'])
def controle():
    try:
        pdf_file = request.files.get('pdf')
        rdi_file = request.files.get('rdi')
        type_formulaire = request.form.get('type_formulaire', 'simple')
        type_controle = request.form.get('type_controle', 'xml')

        print(f"Controle: {type_formulaire}, {type_controle}")

        if not rdi_file:
            return jsonify({'error': 'Fichier RDI manquant'}), 400
        if type_controle == 'xml' and not pdf_file:
            return jsonify({'error': 'Fichier PDF/XML manquant pour le mode XML'}), 400

        rdi_path = os.path.join(UPLOAD_FOLDER, rdi_file.filename)
        rdi_file.save(rdi_path)

        xml_doc = None
        pdf_path = None
        if type_controle == 'xml' and pdf_file:
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

        rdi_data = parse_rdi(rdi_path)
        print("==== rdi_data ====")
        print(rdi_data)

        mapping_data = load_mapping(type_formulaire)
        if not mapping_data:
            return jsonify({'error': 'Mapping introuvable'}), 500

        mapping = mapping_data.get('champs', [])
        namespaces = {
            'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
            'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
            'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100'
        }

        results = []
        for field in mapping:
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
                    _xpath = _xpath_raw if _xpath_raw.startswith('/') else ('//' + _xpath_raw) if _xpath_raw else '//none'
                    elements = xml_doc.xpath(_xpath, namespaces=namespaces)
                    xml_value = elements[0].text.strip() if elements and hasattr(elements[0], 'text') and elements[0].text else ''
                except:
                    pass

            status, regles_testees, details_erreurs = perform_controls(field, rdi_value, xml_value, type_controle)
            xml_short_name = get_xml_short_name(field.get('xpath', ''))
            xml_tag_name = get_xml_tag_name(field.get('xpath', ''))

            # Construire la liste CEGEDIM détaillée pour le tableau dédié
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
                'categorie_bg': field.get('categorie_bg', 'BG-OTHER'),
                'categorie_titre': field.get('categorie_titre', 'Autres'),
                'obligatoire': field.get('obligatoire', 'Non')
            })

        # Appliquer les controles conditionnels en dur
        results = apply_contextual_controls(results)

        stats = {
            'total': len(results),
            'ok': sum(1 for r in results if r['status'] == 'OK'),
            'erreur': sum(1 for r in results if r['status'] == 'ERREUR'),
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
            categories_results[bg_id]['champs'].sort(key=lambda x: (0 if x['obligatoire'] == 'Oui' else 1, x['balise']))

        # Nettoyage
        os.remove(rdi_path)
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)

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

if __name__ == '__main__':
    print("="*60)
    print("APPLICATION FACTUR-X V12.0")
    print("Ouvrez ce lien dans votre navigateur : http://localhost:5000")
    print("="*60)
    app.run(debug=True, host='0.0.0.0', port=5000)
