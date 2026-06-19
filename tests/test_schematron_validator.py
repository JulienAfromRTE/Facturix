"""Tests unitaires pour validators.schematron_validator."""

import os
import sys
import unittest

from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from validators.schematron_validator import (  # noqa: E402
    rule_to_bts,
    validate_xml,
    detect_profile,
    classify_profile,
    rulesets_for_profile,
    RULESETS,
)

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


def _forge_profile(profile_uri):
    """Renvoie CII_example1 dont le BT-24 est remplacé par `profile_uri`."""
    doc = etree.parse(GOOD_SAMPLE)
    gid = doc.findall(
        ".//{*}GuidelineSpecifiedDocumentContextParameter/{*}ID")[0]
    gid.text = profile_uri
    return etree.tostring(doc, xml_declaration=True, encoding='UTF-8').decode('utf-8')


class ClassifyProfileTest(unittest.TestCase):

    def test_known_profiles(self):
        cases = {
            'urn:cen.eu:en16931:2017#conformant#urn.cpro.gouv.fr:1p0:extended-ctc-fr': 'extended-ctc-fr',
            'urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended': 'extended',
            'urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic': 'basic',
            'urn:factur-x.eu:1p0:basicwl': 'basicwl',
            'urn:factur-x.eu:1p0:minimum': 'minimum',
            'urn:cen.eu:en16931:2017': 'en16931',
            'urn:ferd:CrossIndustryDocument:invoice:1p0:comfort': 'en16931',
            '': 'unknown',
            None: 'unknown',
        }
        for uri, expected in cases.items():
            self.assertEqual(classify_profile(uri), expected, msg=f'profil={uri!r}')

    def test_ctc_fr_takes_priority_over_extended(self):
        # La chaîne contient « extended » ET « extended-ctc-fr » : c'est le FR qui gagne.
        uri = 'urn:cen.eu:en16931:2017#conformant#urn.cpro.gouv.fr:1p0:extended-ctc-fr'
        self.assertEqual(classify_profile(uri), 'extended-ctc-fr')


class RulesetRoutingTest(unittest.TestCase):

    def test_sub_en16931_profiles_get_no_ruleset(self):
        self.assertEqual(rulesets_for_profile('minimum'), ())
        self.assertEqual(rulesets_for_profile('basicwl'), ())

    def test_core_profiles_use_en16931(self):
        for cls in ('basic', 'en16931', 'extended', 'unknown'):
            self.assertEqual(rulesets_for_profile(cls), ('en16931',))

    def test_ctc_fr_uses_dedicated_plus_overlay(self):
        self.assertEqual(
            rulesets_for_profile('extended-ctc-fr'),
            ('extended-ctc-fr', 'br-fr-flux2'),
        )

    def test_routed_rulesets_exist_in_registry(self):
        for cls in ('minimum', 'basic', 'en16931', 'extended', 'extended-ctc-fr', 'unknown'):
            for key in rulesets_for_profile(cls):
                self.assertIn(key, RULESETS)


class DetectProfileTest(unittest.TestCase):

    def test_detect_on_official_example(self):
        self.assertEqual(detect_profile(GOOD_SAMPLE), 'urn:cen.eu:en16931:2017')

    def test_detect_on_forged_ctc_fr(self):
        uri = 'urn:cen.eu:en16931:2017#conformant#urn.cpro.gouv.fr:1p0:extended-ctc-fr'
        self.assertEqual(detect_profile(_forge_profile(uri)), uri)

    def test_detect_returns_empty_on_garbage(self):
        self.assertEqual(detect_profile('not xml at all'), '')


class CtcFrValidationTest(unittest.TestCase):
    """Le profil EXTENDED-CTC-FR route vers son schematron dédié + l'overlay France CTC."""

    def setUp(self):
        uri = 'urn:cen.eu:en16931:2017#conformant#urn.cpro.gouv.fr:1p0:extended-ctc-fr'
        self.xml = _forge_profile(uri)
        self.rulesets = rulesets_for_profile(classify_profile(detect_profile(self.xml)))

    def test_each_error_is_tagged_with_its_ruleset(self):
        errors = validate_xml(self.xml, rulesets=self.rulesets)
        self.assertTrue(errors, 'attendu au moins des avertissements France CTC')
        for e in errors:
            self.assertIn(e['ruleset'], self.rulesets)
            self.assertTrue(e['ruleset_label'])

    def test_br_fr_rules_are_warnings(self):
        errors = validate_xml(self.xml, rulesets=self.rulesets)
        br_fr = [e for e in errors if e['ruleset'] == 'br-fr-flux2']
        self.assertTrue(br_fr, 'le schematron BR-FR Flux 2 doit produire des findings')
        for e in br_fr:
            self.assertEqual(e['severity'], 'warning')
            self.assertTrue(e['rule_id'].startswith('BR-FR'))

    def test_ctc_fr_still_catches_fatal_break(self):
        # Sur ce profil, l'absence de numéro de facture (BT-1) reste fatale (BR-02).
        doc = etree.fromstring(self.xml.encode('utf-8'))
        for el in doc.findall('.//{*}ExchangedDocument/{*}ID'):
            el.getparent().remove(el)
        broken = etree.tostring(doc, xml_declaration=True, encoding='UTF-8').decode('utf-8')
        errors = validate_xml(broken, rulesets=('extended-ctc-fr',))
        br02 = [e for e in errors if e['rule_id'] == 'BR-02']
        self.assertTrue(br02)
        self.assertEqual(br02[0]['flag'], 'fatal')
        self.assertEqual(br02[0]['ruleset'], 'extended-ctc-fr')


if __name__ == '__main__':
    unittest.main()
