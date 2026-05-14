"""Construit un XML CII synthétique à partir des données RDI + mapping.

Permet d'appliquer le schematron officiel EN16931 même quand on n'a pas
de PDF Factur-X : les valeurs du RDI sont injectées aux xpath déclarés
dans le mapping pour produire un XML temporaire qui peut être validé.

Limites connues :
- Les attributs requis par CII (format, unitCode, currencyID, etc.) ne sont
  posés que si la clé `attribute` du mapping est renseignée. Un BT-2
  (date) au format texte tombera dans `udt:DateTimeString` sans `format` —
  ce qui peut provoquer un faux positif schematron sur la forme de la valeur.
- Le profil (BT-24) est forcé à `urn:cen.eu:en16931:2017` si BT-24 n'a pas
  de valeur RDI, sinon BR-01 échoue systématiquement.
"""

import re
from lxml import etree

NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
    'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100',
}

LINE_ITEM_TAG = 'ram:IncludedSupplyChainTradeLineItem'

_STEP_RE = re.compile(r'^([\w-]+:[\w-]+)(?:\[(.+)\])?$')
_PRED_RE = re.compile(r"([\w-]+:[\w-]+)\s*=\s*['\"]([^'\"]+)['\"]")


def _qname(prefixed):
    prefix, local = prefixed.split(':', 1)
    return f'{{{NS[prefix]}}}{local}'


def _parse_step(step):
    match = _STEP_RE.match(step)
    if not match:
        return None
    name = match.group(1)
    pred_text = match.group(2) or ''
    predicates = [(p.group(1), p.group(2)) for p in _PRED_RE.finditer(pred_text)]
    return name, predicates


def _find_or_create(parent, name, predicates):
    qname = _qname(name)
    for child in parent.findall(qname):
        ok = True
        for pname, pvalue in predicates:
            sub = child.find(_qname(pname))
            if sub is None or (sub.text or '').strip() != pvalue:
                ok = False
                break
        if ok:
            return child
    el = etree.SubElement(parent, qname)
    for pname, pvalue in predicates:
        sub = etree.SubElement(el, _qname(pname))
        sub.text = pvalue
    return el


def _find_or_create_at(parent, name, position):
    """Renvoie le N-ème enfant (1-based), en créant les manquants."""
    qname = _qname(name)
    children = parent.findall(qname)
    while len(children) < position:
        etree.SubElement(parent, qname)
        children = parent.findall(qname)
    return children[position - 1]


_DATETIME_LEAF_RE = re.compile(r':DateTimeString$')
_YYYYMMDD_RE = re.compile(r'^\d{8}$')


_ROOT_ELEM = 'rsm:CrossIndustryInvoice'


def _set_at(root, xpath, value, attribute=None, line_index=None):
    parts = [p.strip() for p in xpath.lstrip('/').split('/') if p.strip()]
    if not parts:
        return
    # Ne sauter parts[0] que s'il désigne la racine elle-même.
    # Les XPath courts comme "//rsm:ExchangedDocument/ram:ID" commencent
    # directement par un enfant de la racine et doivent tous être traités.
    start = 1 if parts[0] == _ROOT_ELEM else 0
    if start >= len(parts):
        return
    current = root
    last_step_name = None
    for step in parts[start:]:
        parsed = _parse_step(step)
        if not parsed:
            return
        name, predicates = parsed
        last_step_name = name
        if line_index is not None and name == LINE_ITEM_TAG:
            current = _find_or_create_at(current, name, line_index + 1)
        else:
            current = _find_or_create(current, name, predicates)
    if attribute:
        current.set(attribute, value)
    else:
        current.text = value
        # Les éléments DateTimeString exigent un attribut format (BR-03 / BR-DEC-XX) :
        # "102" pour YYYYMMDD est le seul format toléré par EN16931.
        if last_step_name and _DATETIME_LEAF_RE.search(last_step_name) and _YYYYMMDD_RE.match(value or ''):
            current.set('format', '102')


_DATE_RE = re.compile(r'^(\d{2})[./-](\d{2})[./-](\d{4})$')
# Décimal "à la française" strict : pas d'autres caractères que chiffres/espace/./, optionnel signe, optionnel suffixe SAP -
_DECIMAL_FR_RE = re.compile(r'^-?\d[\d. ]*(?:,\d+)?-?$')


def _resolve_rdi_value(source, key):
    if not key:
        return ''
    value = (source.get(key) or '').strip()
    if value:
        return value
    upper = key.upper()
    for k, v in source.items():
        if k.upper() == upper:
            return (v or '').strip()
    return ''


def _normalize_for_xml(value, field_type):
    """Convertit les formats français (virgule, suffixe SAP -, JJ.MM.AAAA) vers les formats CII.

    Sans .upper() (contrairement à app.normalize_value, prévue pour la comparaison),
    afin de préserver les chaînes destinées au XML.
    """
    if not value:
        return value
    ftype = (field_type or '').strip().lower()
    # type 'Date' explicite OU valeur qui ressemble à une date française —
    # le mapping déclare parfois 'String' pour des dates (BT-134/135).
    date_match = _DATE_RE.match(value)
    if ftype == 'date' or date_match:
        if date_match:
            return f'{date_match.group(3)}{date_match.group(2)}{date_match.group(1)}'  # YYYYMMDD
        return value
    # type 'Decimal' explicite OU valeur qui ressemble à une décimale française
    # (le mapping déclare parfois 'String' pour des montants — ne pas s'y fier).
    if ftype == 'decimal' or _DECIMAL_FR_RE.match(value):
        v = value.replace(' ', '')
        if v.endswith('-'):  # suffixe SAP : "1234,56-" → "-1234,56"
            v = '-' + v[:-1]
        if '.' in v and ',' in v:
            v = v.replace('.', '').replace(',', '.')
        elif ',' in v:
            v = v.replace(',', '.')
        # Le RDI pad les montants avec des zéros (487,500000) — les BR-DEC-XX du
        # schematron n'acceptent que 2 décimales : on supprime les zéros traînants.
        if '.' in v:
            v = v.rstrip('0').rstrip('.')
            if not v or v == '-':
                v = '0'
        return v
    return value


def build_cii_xml(rdi_data, rdi_articles, mapping_champs):
    """Construit un XML CII (string UTF-8) à partir du RDI et du mapping.

    Renvoie ``None`` si on n'a ni données d'en-tête ni articles.
    """
    if not rdi_data and not rdi_articles:
        return None

    root = etree.Element(_qname('rsm:CrossIndustryInvoice'), nsmap=dict(NS))

    # Profil par défaut (BR-01) : on ne le met que si BT-24 n'a pas déjà de
    # mapping résolvable côté RDI ; sinon on laisse le mapping s'en charger.
    bt24_field = next(
        (f for f in mapping_champs
         if not f.get('is_article')
         and (f.get('balise') or '').strip() == 'BT-24'
         and f.get('xpath')),
        None,
    )
    bt24_value = (
        _resolve_rdi_value(rdi_data or {}, bt24_field.get('rdi'))
        if bt24_field else ''
    )
    if not bt24_value:
        ctx = etree.SubElement(root, _qname('rsm:ExchangedDocumentContext'))
        guideline = etree.SubElement(ctx, _qname('ram:GuidelineSpecifiedDocumentContextParameter'))
        gid = etree.SubElement(guideline, _qname('ram:ID'))
        gid.text = 'urn:cen.eu:en16931:2017'

    header_fields = [f for f in mapping_champs if not f.get('is_article')]
    article_fields = [f for f in mapping_champs if f.get('is_article')]

    for field in header_fields:
        if (field.get('ignore') or '').strip().lower() == 'oui':
            continue
        value = _resolve_rdi_value(rdi_data or {}, (field.get('rdi') or '').strip())
        if not value:
            continue
        xpath = (field.get('xpath') or '').strip()
        if not xpath:
            continue
        value = _normalize_for_xml(value, field.get('type'))
        _set_at(root, xpath, value, field.get('attribute'))

    for art_idx, rdi_art in enumerate(rdi_articles or []):
        for field in article_fields:
            if (field.get('ignore') or '').strip().lower() == 'oui':
                continue
            value = _resolve_rdi_value(rdi_art, (field.get('rdi') or '').strip())
            if not value:
                continue
            xpath = (field.get('xpath') or '').strip()
            if not xpath or LINE_ITEM_TAG not in xpath:
                continue
            value = _normalize_for_xml(value, field.get('type'))
            _set_at(root, xpath, value, field.get('attribute'), line_index=art_idx)

    _inject_implicit_cii_attributes(root)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8').decode('utf-8')


def _inject_implicit_cii_attributes(root):
    """Comble les éléments CII obligatoires que le RDI ne porte pas explicitement.

    EN16931 n'autorise que ``TypeCode='VAT'`` pour les taxes : on l'injecte sur
    chaque ``ApplicableTradeTax`` qui n'en a pas, sinon BR-CO-04 / BR-S-08
    échouent systématiquement même quand le RDI est correct.
    """
    type_code_qn = _qname('ram:TypeCode')
    cat_code_qn = _qname('ram:CategoryCode')
    for tax in root.iter(_qname('ram:ApplicableTradeTax')):
        if tax.find(type_code_qn) is None:
            tc = etree.Element(type_code_qn)
            tc.text = 'VAT'
            # TypeCode doit précéder CategoryCode dans le schéma CII.
            cat = tax.find(cat_code_qn)
            if cat is not None:
                tax.insert(list(tax).index(cat), tc)
            else:
                tax.insert(0, tc)
