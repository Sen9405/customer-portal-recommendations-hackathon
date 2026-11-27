import gzip
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.catalog_index import ingest_stream, query_candidates, create_index


class CatalogIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'index.sqlite'
        self.conn = create_index(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def ingest(self, xml):
        return ingest_stream(self.conn, io.BytesIO(gzip.compress(xml.encode())), '2026-01-01T00:00:00+00:00')

    def test_duplicates_availability_params_and_pagination(self):
        self.ingest('''<yml_catalog date="now"><shop><categories><category id="1">Cat</category></categories><offers>
        <offer id="x" available="true"><name>A</name><vendor>V</vendor><categoryId>1</categoryId><url>https://example.test/a</url><param name="Color" unit="x">red</param></offer>
        <offer id="x"><name>B</name><vendor>V</vendor><categoryId>1</categoryId></offer>
        <offer id="z" available="false"><vendor>Other</vendor><categoryId>1</categoryId></offer>
        </offers></shop></yml_catalog>''')
        a = query_candidates(self.conn, category_id='1', vendor='V', limit=1)
        b = query_candidates(self.conn, category_id='1', vendor='V', limit=1, after=a[-1]['row_id'])
        self.assertEqual((a[0]['offer_id'], b[0]['offer_id']), ('x', 'x'))
        self.assertEqual((a[0]['available'], b[0]['available']), ('true', None))
        self.assertEqual(a[0]['params'][0]['value'], 'red')
        self.assertEqual(query_candidates(self.conn, vendor="V' OR 1=1 --"), [])
        self.assertEqual(self.conn.execute('select count(*) from offers').fetchone()[0], 3)
        self.assertEqual(self.conn.execute('select name from categories where category_id=?', ('1',)).fetchone()[0], 'Cat')

    def test_xml_entity_forbidden(self):
        with self.assertRaises(ValueError):
            self.ingest('<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>')

    def test_size_and_existing_file(self):
        with self.assertRaises(FileExistsError):
            create_index(self.path)
        with self.assertRaises(ValueError):
            ingest_stream(self.conn, io.BytesIO(gzip.compress(b'<x>' + b'a' * 100 + b'</x>')), 'x', max_xml_bytes=20)

    def test_query_validation(self):
        with self.assertRaises(ValueError): query_candidates(self.conn)
        with self.assertRaises(ValueError): query_candidates(self.conn, vendor='x', limit=-1)


if __name__ == '__main__': unittest.main()
