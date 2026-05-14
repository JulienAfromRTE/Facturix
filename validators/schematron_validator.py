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
SCHEMATRON_DIR = os.path.join(_BASE_DIR, 'schematron', 'en16931-cii')
XSLT_PATH = os.path.join(SCHEMATRON_DIR, 'xslt', 'EN16931-CII-validation.xslt')
SCH_PATH = os.path.join(
    SCHEMATRON_DIR, 'schematron', 'preprocessed',
    'EN16931-CII-validation-preprocessed.sch',
)

SVRL_NS = 'http://purl.oclc.org/dsdl/svrl'
SCH_NS = 'http://purl.oclc.org/dsdl/schematron'

_BT_RE = re.compile(r'BT-\d+(?:-\d+)?')

_saxon_lock = Lock()
_saxon_state = {'proc': None, 'executable': None}


def _get_saxon():
    # SaxonC garde un processeur global; on compile le XSLT une seule fois.
    with _saxon_lock:
        if _saxon_state['executable'] is None:
            proc = PySaxonProcessor(license=False)
            xslt = proc.new_xslt30_processor()
            _saxon_state['proc'] = proc
            _saxon_state['executable'] = xslt.compile_stylesheet(
                stylesheet_file=XSLT_PATH
            )
        return _saxon_state['proc'], _saxon_state['executable']


@lru_cache(maxsize=1)
def rule_to_bts():
    """Map id de règle (ex: 'BR-CO-10') -> liste ordonnée des BT cités dans le texte."""
    tree = etree.parse(SCH_PATH)
    mapping = {}
    for assertion in tree.findall(f'.//{{{SCH_NS}}}assert'):
        rid = assertion.get('id')
        if not rid:
            continue
        text = ''.join(assertion.itertext())
        bts = list(dict.fromkeys(_BT_RE.findall(text)))
        mapping[rid] = bts
    return mapping


def validate_xml(xml_source):
    """Valide un XML CII contre le schematron EN16931 et retourne la liste d'erreurs.

    xml_source : chemin de fichier (str), bytes, ou str contenant du XML.

    Chaque erreur est un dict :
        rule_id  : 'BR-CO-10'
        flag     : 'fatal' | 'warning'
        severity : 'error'  | 'warning'
        location : XPath du noeud en faute
        message  : texte de l'assertion
        bts      : ['BT-106', 'BT-131']
    """
    proc, executable = _get_saxon()

    if isinstance(xml_source, str) and os.path.exists(xml_source):
        svrl_str = executable.transform_to_string(source_file=xml_source)
    else:
        if isinstance(xml_source, (bytes, bytearray)):
            xml_text = xml_source.decode('utf-8')
        else:
            xml_text = xml_source
        node = proc.parse_xml(xml_text=xml_text)
        svrl_str = executable.transform_to_string(xdm_node=node)

    return _parse_svrl(svrl_str)


def _parse_svrl(svrl_str):
    bt_map = rule_to_bts()
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
        })
    return errors


# Saxon émet des locations comme :
#   /*:IncludedSupplyChainTradeLineItem[namespace-uri()='...'][1]/...
# Le `[\d+]` final est le numéro de ligne (1-based). On match le marker puis le
# premier prédicat numérique de la même étape (pas après le `/` suivant).
_LINE_ITEM_RE = re.compile(r'IncludedSupplyChainTradeLineItem[^/]*?\[(\d+)\]')


def line_index_from_location(location):
    """Renvoie l'index 0-based de l'IncludedSupplyChainTradeLineItem cité, ou None."""
    if not location:
        return None
    match = _LINE_ITEM_RE.search(location)
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
