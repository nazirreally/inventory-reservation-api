import sqlite3
import unittest
from app.audit import reconcile


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript("""
          CREATE TABLE inventory (sku TEXT PRIMARY KEY, stock INTEGER);
          CREATE TABLE reservations (sku TEXT, quantity INTEGER, status TEXT, expires_at INTEGER);
          INSERT INTO inventory VALUES ('A',10),('B',2),('C',0);
          INSERT INTO reservations VALUES ('A',3,'active',110),('A',2,'active',90),
             ('A',2,'confirmed',120),('B',3,'active',110);
        """)

    def test_live_holds_only(self):
        result = reconcile(self.db, now=100)
        self.assertEqual(result['items'][0]['held'], 3)
        self.assertEqual(result['items'][0]['available'], 7)

    def test_oversubscription_detected(self):
        result = reconcile(self.db, now=100)
        self.assertEqual(result['oversubscribed_count'], 1)
        self.assertTrue(result['items'][1]['oversubscribed'])

    def test_no_reservations(self):
        result = reconcile(self.db, now=100)
        self.assertEqual(result['items'][2]['available'], 0)
        self.assertEqual(result['sku_count'], 3)

    def test_expired_holds_not_counted(self):
        result = reconcile(self.db, now=111)
        self.assertEqual(result['oversubscribed_count'], 0)
        self.assertEqual(result['items'][0]['held'], 0)

    def test_read_only(self):
        before = self.db.total_changes
        reconcile(self.db, now=100)
        self.assertEqual(self.db.total_changes, before)


if __name__ == '__main__':
    unittest.main()
