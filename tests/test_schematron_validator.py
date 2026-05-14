"""Tests unitaires pour validators.schematron_validator."""

import os
import sys
import unittest

from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from validators.schematron_validator import rule_to_bts, validate_xml  # noqa: E402

JDD_DIR = os.path.join(ROOT, 'jdd')
EXAMPLES_DIR = os.path.join(ROOT, 'schematron', 'en16931-cii', 'examples')
SAMPLE_PDF = os.path.join(JDD_DIR, '4300225657.pdf')
GOOD_SAMPLE = os.path.join(EXAMPLES_DIR, 'CII_example1.xml')

NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
}


def _xml_without_invoice_number():
    """Renvoie l'exemple officiel CII_example1 amputé de son ExchangedDocument/ID (BT-1)."""
    doc = etree.parse(GOOD_SAMPLE)
    for el in doc.findall('.//rsm:ExchangedDocument/ram:ID', NS):
        el.getparent().remove(el)
    return etree.tostring(doc, xml_declaration=True, encoding='UTF-8').decode('utf-8')


class RuleToBtsTest(unittest.TestCase):

    def test_mapping_loaded(self):
        mapping = rule_to_bts()
        self.assertGreater(len(mapping), 700)

    def test_mapping_known_rules(self):
        mapping = rule_to_bts()
        # BR-01 : Specification identifier (BT-24)
        self.assertIn('BR-01', mapping)
        self.assertIn('BT-24', mapping['BR-01'])
        # BR-02 : Invoice number (BT-1)
        self.assertIn('BR-02', mapping)
        self.assertIn('BT-1', mapping['BR-02'])
        # BR-CO-10 : somme des montants nets (BT-106 = Σ BT-131)
        self.assertIn('BR-CO-10', mapping)
        self.assertIn('BT-106', mapping['BR-CO-10'])
        self.assertIn('BT-131', mapping['BR-CO-10'])


class ValidateOfficialExampleTest(unittest.TestCase):
    """Les exemples livrés avec le schematron sont conformes par construction."""

    def test_clean_sample_has_no_errors(self):
        errors = validate_xml(GOOD_SAMPLE)
        self.assertEqual(
            errors, [],
            msg=f'Exemple officiel attendu conforme, erreurs : {errors}',
        )


class ValidateBrokenXmlTest(unittest.TestCase):
    """On dérive un XML cassé d'un exemple officiel pour vérifier la détection."""

    def test_missing_invoice_number_triggers_br_02(self):
        errors = validate_xml(_xml_without_invoice_number())
        self.assertGreater(len(errors), 0)

        rule_ids = {e['rule_id'] for e in errors}
        self.assertIn('BR-02', rule_ids)

        br_02 = next(e for e in errors if e['rule_id'] == 'BR-02')
        self.assertEqual(br_02['flag'], 'fatal')
        self.assertEqual(br_02['severity'], 'error')
        self.assertIn('BT-1', br_02['bts'])
        self.assertTrue(br_02['message'])
        self.assertTrue(br_02['location'])

    def test_error_shape(self):
        for e in validate_xml(_xml_without_invoice_number()):
            self.assertIn('rule_id', e)
            self.assertIn('flag', e)
            self.assertIn('severity', e)
            self.assertIn('location', e)
            self.assertIn('message', e)
            self.assertIn('bts', e)
            self.assertTrue(e['rule_id'].startswith('BR-'))


@unittest.skipUnless(os.path.exists(SAMPLE_PDF), 'PDF de test absent')
class ValidatePdfSampleTest(unittest.TestCase):

    def test_sample_pdf_runs_through(self):
        from app import extract_xml_from_pdf

        xml = extract_xml_from_pdf(SAMPLE_PDF)
        self.assertTrue(xml, 'XML extractable du PDF')

        errors = validate_xml(xml)
        self.assertIsInstance(errors, list)
        for e in errors:
            self.assertTrue(e['rule_id'].startswith('BR-'))
            self.assertIn(e['severity'], {'error', 'warning'})


if __name__ == '__main__':
    unittest.main()
