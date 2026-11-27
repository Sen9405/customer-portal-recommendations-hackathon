import gzip
import io
import unittest

from catalog.softline_audit import audit_stream
from catalog.softline_features import feature_snapshot


class FeaturesTest(unittest.TestCase):
    def test_unique_union_only_and_provenance(self):
        rows = [('I1', 'A1', ''), ('I2', 'B2', ''), ('N-1', '', 'Exact')]
        xml = b'''<yml_catalog date="today"><shop><categories><category id="7" parentId="1">Category</category></categories><offers>
        <offer id="1" available="true"><vendorCode>A1</vendorCode><url>https://store.softline.ru/a/a1-123</url><categoryId>7</categoryId><vendor>Vendor</vendor><param name="Color" unit="x">Blue</param></offer>
        <offer id="2"><vendorCode>B2</vendorCode></offer><offer id="3"><vendorCode>B2</vendorCode></offer>
        <offer id="4" available="false"><name>Exact</name></offer>
        </offers></shop></yml_catalog>'''
        result = audit_stream(rows, io.BytesIO(gzip.compress(xml)), include_features=True)
        snap = feature_snapshot(result, {'source': 'test'})
        self.assertEqual(set(snap['items']), {'I1', 'N-1'})
        item = snap['items']['I1']
        self.assertEqual(item['candidate']['category']['name'], 'Category')
        self.assertEqual(item['candidate']['params'], [{'name': 'Color', 'unit': 'x', 'value': 'Blue'}])
        self.assertEqual(item['candidate']['available'], 'true')
        self.assertEqual(set(item['match_channels']), {'vendorCode', 'url'})
        self.assertEqual(snap['items']['N-1']['match_channels'], ['name_exact_N'])
        self.assertEqual(snap['provenance']['source'], 'test')
        self.assertIn('not', snap['limitations']['identity'])

    def test_resource_limits(self):
        xml = b'<root><offer><vendorCode>A</vendorCode>' + b'<param name="x">a</param>' * 5 + b'</offer></root>'
        with self.assertRaises(ValueError):
            audit_stream([('I', 'A', '')], io.BytesIO(gzip.compress(xml)), include_features=True, max_params=3)

    def test_doctype_entity_rejected(self):
        xml = b'<!DOCTYPE root [<!ENTITY x "y">]><root><offer><vendorCode>&x;</vendorCode></offer></root>'
        with self.assertRaises(ValueError):
            audit_stream([], io.BytesIO(gzip.compress(xml)), include_features=True)
