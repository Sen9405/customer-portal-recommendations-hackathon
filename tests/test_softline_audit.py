import gzip
import io
import unittest

from catalog.softline_audit import audit_stream, normalize_sku, url_sku, FeedRedirectHandler
from urllib.request import Request


class SoftlineAuditTest(unittest.TestCase):
    def test_normalization_and_url_suffix(self):
        self.assertEqual(normalize_sku('Cr15-1S1W01'), 'CR151S1W01')
        self.assertEqual(url_sku('https://store.softline.ru/vendor/cr15-1s1w01-324027/'), 'CR151S1W01')
        self.assertEqual(url_sku('https://store.softline.ru/vendor/not-an-id/'), '')

    def test_channels_ambiguity_and_name(self):
        rows = [('I1', 'AB-1', 'unused'), ('I1', 'AB-1', 'unused'),
                ('I2', 'CD-2', 'unused'), ('N-1', '', 'Exact Name')]
        xml = b'''<yml_catalog date="2026-01-01"><shop><offers>
        <offer id="1"><vendorCode>AB1</vendorCode><url>https://store.softline.ru/v/cd-2-123/</url><name>Exact Name</name></offer>
        <offer id="2"><vendorCode>AB-1</vendorCode><name>Exact Name</name></offer>
        </offers></shop></yml_catalog>'''
        result = audit_stream(rows, io.BytesIO(gzip.compress(xml)))
        self.assertEqual(result['summary']['vendorCode']['ambiguous']['items'], 1)
        self.assertEqual(result['summary']['vendorCode']['ambiguous']['rows'], 2)
        self.assertEqual(result['summary']['url']['unique']['items'], 1)
        self.assertEqual(result['summary']['name_exact_N']['ambiguous']['items'], 1)
        self.assertEqual(result['feed']['offers'], 2)

    def test_external_entity_does_not_expand(self):
        xml = b'''<!DOCTYPE yml_catalog [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><yml_catalog><shop><offers><offer id="1"><name>&xxe;</name></offer></offers></shop></yml_catalog>'''
        with self.assertRaises(ValueError):
            audit_stream([('N-1', '', 'x')], io.BytesIO(gzip.compress(xml)))

    def test_size_limit(self):
        with self.assertRaises(ValueError):
            audit_stream([], io.BytesIO(gzip.compress(b'<root>' + b'x'*100 + b'</root>')), max_xml_bytes=30)

    def test_duplicate_offer_ids_are_not_silently_unique(self):
        xml = b'<root><offer id="same"><vendorCode>A1</vendorCode></offer><offer id="same"><vendorCode>A1</vendorCode></offer></root>'
        result = audit_stream([('I1', 'A1', '')], io.BytesIO(gzip.compress(xml)))
        self.assertEqual(result['summary']['vendorCode']['ambiguous']['items'], 1)

    def test_redirect_rejects_untrusted_destination_before_follow(self):
        handler = FeedRedirectHandler()
        with self.assertRaises(ValueError):
            handler.redirect_request(Request('https://store.softline.ru/feed'), None, 302, '', {}, 'https://example.org/feed')

if __name__ == '__main__':
    unittest.main()
