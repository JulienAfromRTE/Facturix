"""Test d'intégration : apply_schematron() dans app.py."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import apply_schematron, extract_xml_from_pdf  # noqa: E402
from validators.schematron_validator import line_index_from_location  # noqa: E402

SAMPLE_PDF = os.path.join(ROOT, 'jdd', '4300225657.pdf')


def _result_stub(balise, **extra):
    base = {
        'balise': balise,
        'libelle': '',
        'rdi': '',
        'xml': '',
        'rdi_field': '',
        'xml_short_name': '',
        'xml_tag_name': '',
        'status': 'OK',
        'regles_testees': [],
        'details_erreurs': ['RAS'],
        'rule_details': {},
        'controles_cegedim': [],
        'categorie_bg': 'BG-INFOS-GENERALES',
        'categorie_titre': '',
        'obligatoire': 'Non',
        'order_index': 0,
    }
    base.update(extra)
    return base


@unittest.skipUnless(os.path.exists(SAMPLE_PDF), 'PDF de test absent')
class ApplySchematronTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.xml = extract_xml_from_pdf(SAMPLE_PDF)
        cls.assertIsNotNone(cls.xml, 'XML attendu dans le PDF de test')

    def test_returns_summary_with_expected_shape(self):
        results = [
            _result_stub('BT-106'),
            _result_stub('BT-118'),
            _result_stub('BT-152', is_article=True, article_index=0,
                         categorie_bg='BG-LIGNES'),
        ]
        summary = apply_schematron(self.xml, results)

        self.assertIsNotNone(summary)
        for k in ('total', 'fatal', 'warning', 'matched', 'rules', 'errors', 'orphans'):
            self.assertIn(k, summary)
        self.assertGreater(summary['total'], 0)
        self.assertGreater(summary['fatal'], 0)

    def test_attaches_br_co_10_to_bt_106(self):
        bt106 = _result_stub('BT-106')
        results = [bt106]
        apply_schematron(self.xml, results)

        rule_ids = {e['rule_id'] for e in bt106.get('schematron_errors', [])}
        self.assertIn('BR-CO-10', rule_ids)
        self.assertEqual(bt106['status'], 'ERREUR')
        self.assertTrue(any('BR-CO-10' in r for r in bt106['regles_testees']))
        self.assertTrue(any('BR-CO-10' in d for d in bt106['details_erreurs']))
        self.assertNotIn('RAS', bt106['details_erreurs'])

    def test_article_index_filters_by_line(self):
        # BR-S-05 est émis sur chaque ligne (IncludedSupplyChainTradeLineItem[N]).
        # Le filtre par article_index ne doit pas attacher la même erreur à toutes.
        line0 = _result_stub('BT-152', is_article=True, article_index=0,
                             categorie_bg='BG-LIGNES')
        line1 = _result_stub('BT-152', is_article=True, article_index=1,
                             categorie_bg='BG-LIGNES')
        line8 = _result_stub('BT-152', is_article=True, article_index=8,
                             categorie_bg='BG-LIGNES')
        apply_schematron(self.xml, [line0, line1, line8])

        for r in (line0, line1, line8):
            errs = r.get('schematron_errors', [])
            self.assertGreater(len(errs), 0, f"ligne {r['article_index']} sans erreur")
            for err in errs:
                self.assertEqual(
                    line_index_from_location(err['location']),
                    r['article_index'],
                    msg=f"erreur attachée à la mauvaise ligne: {err}",
                )

    def test_orphans_are_errors_without_mapped_bt(self):
        # Aucun BT mappé → toutes les erreurs schematron tombent en orphelines
        summary = apply_schematron(self.xml, [])
        self.assertEqual(summary['matched'], 0)
        self.assertEqual(len(summary['orphans']), summary['total'])

    def test_handles_invalid_xml_gracefully(self):
        summary = apply_schematron('<not really xml', [])
        self.assertIn('error', summary)


@unittest.skipUnless(os.path.exists(SAMPLE_PDF), 'PDF de test absent')
class SchematronBusinessRuleBridgeTest(unittest.TestCase):
    """Pont schematron ↔ règles métier : `schematron_id` sur une rule la
    promeut en règle éditable. Désactiver la rule filtre l'erreur correspondante."""

    @classmethod
    def setUpClass(cls):
        from app import extract_xml_from_pdf
        cls.xml = extract_xml_from_pdf(SAMPLE_PDF)
        assert cls.xml, 'XML attendu dans le PDF de test'

    def _set_rule_enabled(self, schematron_id, enabled):
        from app import load_business_rules, save_business_rules
        data = load_business_rules()
        for r in data.get('rules', []):
            if r.get('schematron_id') == schematron_id:
                r['enabled'] = enabled
        save_business_rules(data)

    def test_business_rule_name_replaces_generic_label(self):
        from app import apply_schematron
        bt106 = _result_stub('BT-106')
        self._set_rule_enabled('BR-CO-10', True)
        apply_schematron(self.xml, [bt106])

        # La règle métier "📜 BR-CO-10 — ..." doit apparaître dans regles_testees,
        # à la place du label générique "📜 Schematron BR-CO-10".
        self.assertTrue(
            any('BR-CO-10' in r and 'Σ BT-131 = BT-106' in r for r in bt106['regles_testees']),
            f"Règle métier attendue dans regles_testees, vu: {bt106['regles_testees']}",
        )
        self.assertFalse(
            any(r == '📜 Schematron BR-CO-10' for r in bt106['regles_testees']),
            'Label générique ne doit pas coexister avec la règle métier nommée',
        )

    def test_disabling_rule_filters_schematron_error(self):
        from app import apply_schematron
        try:
            self._set_rule_enabled('BR-CO-10', False)
            bt106 = _result_stub('BT-106')
            summary = apply_schematron(self.xml, [bt106])

            self.assertNotIn('BR-CO-10', summary['rules'])
            self.assertEqual(bt106.get('schematron_errors', []), [])
            self.assertNotIn('ERREUR', bt106['status'])  # OK puisque seule erreur sch était BR-CO-10
        finally:
            self._set_rule_enabled('BR-CO-10', True)


if __name__ == '__main__':
    unittest.main()
