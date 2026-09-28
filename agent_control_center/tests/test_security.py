import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc.security import Allowlist  # noqa: E402


class AllowlistTests(unittest.TestCase):
    def test_allows_only_listed_ids(self):
        al = Allowlist([111, 222])
        self.assertTrue(al.is_allowed(111))
        self.assertTrue(al.is_allowed(222))
        self.assertFalse(al.is_allowed(333))

    def test_empty_list_denies_everyone(self):
        al = Allowlist([])
        self.assertFalse(al.is_allowed(111))
        self.assertEqual(len(al), 0)

    def test_string_ids_coerced(self):
        al = Allowlist(["111"])
        self.assertTrue(al.is_allowed(111))
        self.assertTrue(al.is_allowed("111"))

    def test_garbage_id_denied(self):
        al = Allowlist([111])
        self.assertFalse(al.is_allowed(None))
        self.assertFalse(al.is_allowed("abc"))


if __name__ == "__main__":
    unittest.main()
