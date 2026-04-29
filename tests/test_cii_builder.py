"""Tests unitaires pour validators.cii_builder."""

import json
import os
import sys
import unittest

from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from validators.cii_builder import build_cii_xml  # noqa: E402

NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
}

MAPPING_PATH = os.path.join(ROOT, 'mapping_archive', 'mapping_v5_simple.json')
SAMPLE_RDI = os.path.join(ROOT, 'jdd', '4300225657.txt')


def _mapping():
    with open(MAPPING_PATH) as f:
        return json.load(f)['champs']


class BuildCiiXmlBasicTest(unittest.TestCase):

    def test_returns_none_when_empty(self):
        self.assertIsNone(build_cii_xml({}, [], _mapping()))

    def test_minimal_doc_has_root_and_default_profile(self):
        xml = build_cii_xml({'WNUM_FACT': 'INV-1'}, [], _mapping())
        self.assertIsNotNone(xml)
        doc = etree.fromstring(xml.encode('utf-8'))
        self.assertEqual(doc.tag, f'{{{NS["rsm"]}}}CrossIndustryInvoice')
        # BT-24 forcé par défaut quand le RDI n'en a pas
        profile = doc.find(
            'rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID',
            namespaces=NS,
        )
        self.assertIsNotNone(profile)
        self.assertTrue(profile.text.startswith('urn:cen.eu:en16931:2017'))
        # BT-1 injecté à son xpath
        bt1 = doc.find('rsm:ExchangedDocument/ram:ID', namespaces=NS)
        self.assertIsNotNone(bt1)
        self.assertEqual(bt1.text, 'INV-1')


class NormalizationTest(unittest.TestCase):
    """Les valeurs RDI françaises (virgule, JJ.MM.AAAA, suffixe SAP -) doivent être
    converties au format CII (point, YYYYMMDD, signe préfixe), même si le mapping
    déclare le champ comme 'String'."""

    def test_french_decimal_in_string_typed_field_is_normalized(self):
        # BT-131 est déclaré 'String' dans le mapping mais porte une décimale
        rdi_articles = [{
            'GS_FECT_EINV-BG25-BT126_ID_LIGNE': '1',
            'GS_FECT_EINV-BG25-BT131_MNT_NET': '487,500000',
        }]
        xml = build_cii_xml({}, rdi_articles, _mapping())
        doc = etree.fromstring(xml.encode('utf-8'))
        amount = doc.find(
            'rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem'
            '/ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation'
            '/ram:LineTotalAmount',
            namespaces=NS,
        )
        self.assertIsNotNone(amount)
        # Virgule → point, zéros traînants supprimés (BR-DEC-XX exige ≤ 2 décimales)
        self.assertEqual(amount.text, '487.5')

    def test_sap_negative_suffix(self):
        rdi = {'WMNT_HT': '1234,56-'}
        xml = build_cii_xml(rdi, [], _mapping())
        doc = etree.fromstring(xml.encode('utf-8'))
        amount = doc.find(
            'rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement'
            '/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:LineTotalAmount',
            namespaces=NS,
        )
        self.assertIsNotNone(amount)
        self.assertEqual(amount.text, '-1234.56')

    def test_french_date_to_yyyymmdd_with_format_attr(self):
        rdi = {'DATE_FACT': '10.04.2026'}
        xml = build_cii_xml(rdi, [], _mapping())
        doc = etree.fromstring(xml.encode('utf-8'))
        date_el = doc.find(
            'rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString',
            namespaces=NS,
        )
        self.assertIsNotNone(date_el)
        self.assertEqual(date_el.text, '20260410')
        # BR-03 exige format='102'
        self.assertEqual(date_el.get('format'), '102')


class ArticlesTest(unittest.TestCase):

    def test_one_line_item_per_rdi_article(self):
        rdi_articles = [
            {'GS_FECT_EINV-BG25-BT126_ID_LIGNE': str(i + 1),
             'GS_FECT_EINV-BG25-BT131_MNT_NET': f'{(i + 1) * 100},00'}
            for i in range(3)
        ]
        xml = build_cii_xml({}, rdi_articles, _mapping())
        doc = etree.fromstring(xml.encode('utf-8'))
        items = doc.findall(
            'rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem',
            namespaces=NS,
        )
        self.assertEqual(len(items), 3)
        # Chaque ligne porte ses propres BT-126 et BT-131
        for i, item in enumerate(items):
            line_id = item.find(
                'ram:AssociatedDocumentLineDocument/ram:LineID', namespaces=NS,
            )
            amount = item.find(
                'ram:SpecifiedLineTradeSettlement/'
                'ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount',
                namespaces=NS,
            )
            self.assertEqual(line_id.text, str(i + 1))
            self.assertEqual(amount.text, str((i + 1) * 100))


class PredicatesTest(unittest.TestCase):
    """BT-21/BT-22 utilisent des prédicats SubjectCode='BAR'/'SUR'/... Le builder
    doit créer un IncludedNote distinct par valeur de prédicat."""

    def test_distinct_included_notes_per_subject_code(self):
        rdi = {
            'GS_FECT_EINV-BG1-BT21-BAR': 'BAR',
            'GS_FECT_EINV-BG1-BT22-BAR': 'B2B',
            'GS_FECT_EINV-BG1-BT21-SUR': 'SUR',
            'GS_FECT_EINV-BG1-BT22-SUR': 'ISU',
        }
        xml = build_cii_xml(rdi, [], _mapping())
        doc = etree.fromstring(xml.encode('utf-8'))
        notes = doc.findall(
            'rsm:ExchangedDocument/ram:IncludedNote', namespaces=NS,
        )
        codes = {n.findtext('ram:SubjectCode', namespaces=NS) for n in notes}
        contents = {
            n.findtext('ram:SubjectCode', namespaces=NS):
                n.findtext('ram:Content', namespaces=NS)
            for n in notes
        }
        self.assertIn('BAR', codes)
        self.assertIn('SUR', codes)
        self.assertEqual(contents['BAR'], 'B2B')
        self.assertEqual(contents['SUR'], 'ISU')


class ImplicitAttributesTest(unittest.TestCase):

    def test_typecode_vat_injected_when_missing(self):
        # CategoryCode set via BT-151 mais pas de TypeCode dans la mapping/RDI
        rdi_articles = [{
            'GS_FECT_EINV-BG30-BT151_CODE_TVA_ARTICLE': 'S',
        }]
        xml = build_cii_xml({}, rdi_articles, _mapping())
        doc = etree.fromstring(xml.encode('utf-8'))
        tax = doc.find(
            'rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem'
            '/ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax',
            namespaces=NS,
        )
        self.assertIsNotNone(tax)
        self.assertEqual(tax.findtext('ram:TypeCode', namespaces=NS), 'VAT')
        self.assertEqual(tax.findtext('ram:CategoryCode', namespaces=NS), 'S')


@unittest.skipUnless(os.path.exists(SAMPLE_RDI), 'RDI de test absent')
class FullRdiToSchematronTest(unittest.TestCase):
    """Test bout-en-bout : RDI → XML synthétique → schematron."""

    def test_real_rdi_runs_through(self):
        from app import parse_rdi
        from validators.schematron_validator import validate_xml

        rdi_data, rdi_articles, _ = parse_rdi(SAMPLE_RDI)
        xml = build_cii_xml(rdi_data, rdi_articles, _mapping())
        self.assertTrue(xml)
        errors = validate_xml(xml)
        # Le RDI 4300225657 contient des incohérences réelles : on doit retrouver
        # les mêmes BR que sur le PDF correspondant
        rule_ids = {e['rule_id'] for e in errors}
        self.assertIn('BR-CO-10', rule_ids)
        self.assertIn('BR-S-08', rule_ids)


if __name__ == '__main__':
    unittest.main()
