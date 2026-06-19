"""Validation EN16931 CII via le schematron officiel.

Source : https://github.com/ConnectingEurope/eInvoicing-EN16931/releases (v1.3.16)
Le XSLT pré-compilé en XSLT 2.0 est exécuté avec SaxonC-HE (saxonche),
puis le SVRL retourné est parsé pour produire une liste d'erreurs structurées.

Chaque erreur est associée aux Business Terms (BT-XX) auxquels la règle se réfère,
extraits depuis le texte des assertions du fichier .sch préprocessé.
"""

import os
import re
from functools import lru_cache
from threading import Lock

from lxml import etree
from saxonche import PySaxonProcessor

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMATRON_DIR = os.path.join(_BASE_DIR, 'schematron')

# ── Registre des jeux de règles schematron ──────────────────────────────────
# Chaque jeu = un XSLT compilé (exécuté par Saxon) + le .sch source (pour
# extraire les BT cités par chaque règle) + un libellé lisible.
# Sources (cf. schematron/PROVENANCE-fr-ctc.md) :
#   - en16931         : ConnectingEurope/eInvoicing-EN16931 v1.3.16
#   - extended-ctc-fr : FNFE-MPE, paquet « SCHEMATRONS_FR_CTC V1.3.1 » (2026-04-30)
#   - br-fr-flux2     : FNFE-MPE, même paquet (règles France CTC, flux 2 — en warning)
RULESETS = {
    'en16931': {
        'label': 'EN16931-CII v1.3.16',
        'xslt': os.path.join(SCHEMATRON_DIR, 'en16931-cii', 'xslt',
                             'EN16931-CII-validation.xslt'),
        'sch': os.path.join(SCHEMATRON_DIR, 'en16931-cii', 'schematron',
                            'preprocessed', 'EN16931-CII-validation-preprocessed.sch'),
    },
    'extended-ctc-fr': {
        'label': 'EXTENDED-CTC-FR (CII) v1.3.1 — FNFE-MPE',
        'xslt': os.path.join(SCHEMATRON_DIR, 'extended-ctc-fr', 'EXTENDED-CTC-FR-CII.xslt'),
        'sch': os.path.join(SCHEMATRON_DIR, 'extended-ctc-fr', 'EXTENDED-CTC-FR-CII.sch'),
    },
    'br-fr-flux2': {
        'label': 'France CTC — BR-FR Flux 2 (CII) v1.3.1 — FNFE-MPE',
        'xslt': os.path.join(SCHEMATRON_DIR, 'br-fr-flux2', 'BR-FR-Flux2-CII.xslt'),
        'sch': os.path.join(SCHEMATRON_DIR, 'br-fr-flux2', 'BR-FR-Flux2-CII.sch'),
    },
}

DEFAULT_RULESETS = ('en16931',)

# Compat : anciens noms publics (au cas où un module externe les importerait).
XSLT_PATH = RULESETS['en16931']['xslt']
SCH_PATH = RULESETS['en16931']['sch']

SVRL_NS = 'http://purl.oclc.org/dsdl/svrl'
SCH_NS = 'http://purl.oclc.org/dsdl/schematron'

_BT_RE = re.compile(r'BT-\d+(?:-\d+)?')

_saxon_lock = Lock()
_saxon_state = {'proc': None, 'executables': {}}


def _get_executable(ruleset_key):
    """Compile (une seule fois par jeu de règles) et renvoie l'exécutable XSLT Saxon."""
    with _saxon_lock:
        ex = _saxon_state['executables'].get(ruleset_key)
        if ex is None:
            if _saxon_state['proc'] is None:
                _saxon_state['proc'] = PySaxonProcessor(license=False)
            xslt = _saxon_state['proc'].new_xslt30_processor()
            ex = xslt.compile_stylesheet(stylesheet_file=RULESETS[ruleset_key]['xslt'])
            _saxon_state['executables'][ruleset_key] = ex
        return _saxon_state['proc'], ex


# ── Détection et classification du profil (BT-24) ───────────────────────────
# BT-24 = ExchangedDocumentContext/GuidelineSpecifiedDocumentContextParameter/ID
def detect_profile(xml_source):
    """Renvoie l'URI de profil (BT-24) déclarée dans le XML, ou '' si absente/illisible."""
    try:
        if isinstance(xml_source, (bytes, bytearray)):
            root = etree.fromstring(xml_source)
        elif isinstance(xml_source, str) and os.path.exists(xml_source):
            root = etree.parse(xml_source).getroot()
        elif isinstance(xml_source, str):
            root = etree.fromstring(xml_source.encode('utf-8'))
        else:
            return ''
    except Exception:
        return ''
    els = root.xpath(
        "//*[local-name()='GuidelineSpecifiedDocumentContextParameter']"
        "/*[local-name()='ID']"
    )
    for el in els:
        if el.text and el.text.strip():
            return el.text.strip()
    return ''


def classify_profile(profile_uri):
    """Classe l'URI BT-24 en une famille de profil connue.

    Renvoie l'une de : 'minimum', 'basicwl', 'basic', 'en16931', 'extended',
    'extended-ctc-fr', ou 'unknown' (BT-24 absent ou non reconnu).
    L'ordre des tests est important : 'extended-ctc-fr' avant 'extended',
    'basicwl' avant 'basic'.
    """
    s = (profile_uri or '').strip().lower()
    if not s:
        return 'unknown'
    if 'extended-ctc-fr' in s or ('cpro.gouv' in s and 'extended' in s):
        return 'extended-ctc-fr'
    if 'extended' in s:
        return 'extended'
    if 'basicwl' in s or 'basic wl' in s or 'basic-wl' in s:
        return 'basicwl'
    if 'basic' in s:
        return 'basic'
    if 'minimum' in s:
        return 'minimum'
    if 'en16931' in s or 'comfort' in s:
        return 'en16931'
    return 'unknown'


# Profils non conformes EN16931 : appliquer le schematron EN16931 dessus
# produirait de nombreux faux positifs (ces profils ne portent pas les lignes
# ni la ventilation de TVA exigées par EN16931).
_SUB_EN16931 = {'minimum', 'basicwl'}


def rulesets_for_profile(profile_class):
    """Renvoie le tuple ordonné des jeux de règles à appliquer pour ce profil."""
    if profile_class in _SUB_EN16931:
        return ()
    if profile_class == 'extended-ctc-fr':
        # Étape 2 (règles EN16931 adaptées au profil FR) + étape 3 (overlay France CTC).
        return ('extended-ctc-fr', 'br-fr-flux2')
    # basic, en16931, extended, unknown → cœur EN16931 (comportement historique).
    return ('en16931',)


@lru_cache(maxsize=None)
def _rule_to_bts(sch_path):
    """Map id de règle -> liste ordonnée des BT cités (dans l'id et le texte), pour un .sch."""
    tree = etree.parse(sch_path)
    mapping = {}
    for assertion in tree.findall(f'.//{{{SCH_NS}}}assert'):
        rid = assertion.get('id')
        if not rid:
            continue
        # Les règles BR-FR encodent souvent le BT dans l'id (ex: 'BR-FR-16_BT-152') :
        # on scanne id + texte pour maximiser le rattachement aux champs du mapping.
        text = rid + ' ' + ''.join(assertion.itertext())
        bts = list(dict.fromkeys(_BT_RE.findall(text)))
        mapping[rid] = bts
    return mapping


def rule_to_bts():
    """Compat : map des BT pour le schematron EN16931 (jeu de règles par défaut)."""
    return _rule_to_bts(RULESETS['en16931']['sch'])


def validate_xml(xml_source, rulesets=None):
    """Valide un XML CII contre un ou plusieurs jeux de règles schematron.

    xml_source : chemin de fichier (str), bytes, ou str contenant du XML.
    rulesets   : itérable de clés de RULESETS ; défaut ('en16931',).

    Chaque erreur est un dict :
        rule_id       : 'BR-CO-10'
        flag          : 'fatal' | 'warning'
        severity      : 'error'  | 'warning'
        location      : XPath du noeud en faute
        message       : texte de l'assertion
        bts           : ['BT-106', 'BT-131']
        ruleset       : 'en16931' | 'extended-ctc-fr' | 'br-fr-flux2'
        ruleset_label : libellé lisible du jeu de règles
    """
    keys = [k for k in (tuple(rulesets) if rulesets is not None else DEFAULT_RULESETS)
            if k in RULESETS]
    if not keys:
        return []

    is_file = isinstance(xml_source, str) and os.path.exists(xml_source)
    node = None
    if not is_file:
        proc, _ = _get_executable(keys[0])  # garantit proc initialisé
        if isinstance(xml_source, (bytes, bytearray)):
            xml_text = xml_source.decode('utf-8')
        else:
            xml_text = xml_source
        node = proc.parse_xml(xml_text=xml_text)

    errors = []
    for key in keys:
        _, executable = _get_executable(key)
        if is_file:
            svrl_str = executable.transform_to_string(source_file=xml_source)
        else:
            svrl_str = executable.transform_to_string(xdm_node=node)
        errors.extend(_parse_svrl(svrl_str, key))
    return errors


def _parse_svrl(svrl_str, ruleset_key='en16931'):
    bt_map = _rule_to_bts(RULESETS[ruleset_key]['sch'])
    label = RULESETS[ruleset_key]['label']
    root = etree.fromstring(svrl_str.encode('utf-8') if isinstance(svrl_str, str) else svrl_str)
    errors = []
    for fa in root.findall(f'.//{{{SVRL_NS}}}failed-assert'):
        rid = fa.get('id') or ''
        flag = fa.get('flag', '')
        location = fa.get('location', '')
        text_el = fa.find(f'{{{SVRL_NS}}}text')
        text = ''.join(text_el.itertext()).strip() if text_el is not None else ''
        errors.append({
            'rule_id': rid,
            'flag': flag,
            'severity': 'error' if flag == 'fatal' else 'warning',
            'location': location,
            'message': text,
            'bts': bt_map.get(rid, []),
            'ruleset': ruleset_key,
            'ruleset_label': label,
        })
    return errors


# Saxon émet des locations comme :
#   /*:IncludedSupplyChainTradeLineItem[namespace-uri()='...'][1]/...
# Le `[\d+]` final est le numéro de ligne (1-based). On match le marker puis le
# premier prédicat numérique de la même étape (pas après le `/` suivant).
_LINE_ITEM_RE = re.compile(r'IncludedSupplyChainTradeLineItem[^/]*?\[(\d+)\]')
_APPLICABLE_TRADE_TAX_RE = re.compile(r'ApplicableTradeTax[^/]*?\[(\d+)\]')


def line_index_from_location(location):
    """Renvoie l'index 0-based de l'IncludedSupplyChainTradeLineItem cité, ou None."""
    if not location:
        return None
    match = _LINE_ITEM_RE.search(location)
    return int(match.group(1)) - 1 if match else None


def bg23_index_from_location(location):
    """Renvoie l'index 0-based de l'ApplicableTradeTax cité dans la location schématron, ou None."""
    if not location:
        return None
    match = _APPLICABLE_TRADE_TAX_RE.search(location)
    return int(match.group(1)) - 1 if match else None


def index_errors_by_bt(errors):
    """Indexe la liste d'erreurs par BT cité (un même erreur peut apparaître sous plusieurs BT)."""
    by_bt = {}
    for err in errors:
        for bt in err.get('bts', []):
            by_bt.setdefault(bt, []).append(err)
    return by_bt


def candidates_for_balise(balise):
    """Pour une balise comme 'BT-21-BAR', renvoie {'BT-21-BAR', 'BT-21'} pour les lookups.

    Le schematron officiel ne connaît pas les suffixes spécifiques de l'app
    (BAR, SUR, ADN, AAB, PMT, PMD), il emet uniquement 'BT-21' / 'BT-22'.
    On matche donc aussi sur le préfixe court.
    """
    if not balise:
        return set()
    candidates = {balise}
    parts = balise.split('-')
    if len(parts) >= 3 and parts[0] == 'BT':
        candidates.add('-'.join(parts[:2]))
    return candidates
