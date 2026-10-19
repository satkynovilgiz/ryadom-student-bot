import unittest
from core import Session, chunks

class PrivacyTests(unittest.TestCase):
    def test_isolation(self):
        a, b = Session(), Session()
        a.remember('private', 'reply')
        self.assertEqual(b.history, [])

    def test_bound(self):
        s = Session()
        for i in range(20):
            s.remember(str(i), 'answer')
        self.assertEqual(len(s.history), 24)
        self.assertEqual(s.history[0]['content'], '8')

    def test_delete_invalidates_pending_reply(self):
        s = Session(consent=True)
        old = s.generation
        s.remember('private', 'reply')
        s.clear(revoke=True)
        self.assertFalse(s.consent)
        self.assertEqual(s.history, [])
        self.assertNotEqual(old, s.generation)

    def test_unicode_chunks(self):
        text = '🤗' * 5000
        parts = chunks(text)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p.encode('utf-16-le')) // 2 < 4096 for p in parts))

if __name__ == '__main__':
    unittest.main()
