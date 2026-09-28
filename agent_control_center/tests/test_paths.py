"""Тесты изоляции домиков это ключевое требование безопасности.

Проверяем, что агент проекта не может выйти за пределы своего домика:
абсолютным путём, переходом вверх ('..'), символической ссылкой и обращением
в соседний домик.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc.paths import (  # noqa: E402
    PathEscapeError,
    ProjectSandbox,
    assert_isolated,
    safe_resolve,
)


def _symlinks_supported() -> bool:
    """На Windows без режима разработчика создание симлинков запрещено (WinError 1314)."""
    tmp = tempfile.mkdtemp()
    try:
        os.symlink(tmp, os.path.join(tmp, "probe"), target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


SYMLINKS = _symlinks_supported()
NO_SYMLINKS_REASON = "система не разрешает создавать символические ссылки"


class SafeResolveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "projectA")
        os.makedirs(self.home)

    def test_normal_relative_path_allowed(self):
        resolved = safe_resolve(self.home, "memory/notes.txt")
        self.assertTrue(resolved.startswith(os.path.realpath(self.home) + os.sep))

    def test_empty_path_is_root(self):
        resolved = safe_resolve(self.home, "")
        self.assertEqual(resolved, os.path.realpath(self.home))

    def test_absolute_path_rejected(self):
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "/etc/passwd")

    def test_parent_traversal_rejected(self):
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "../projectB/secrets.txt")

    def test_deep_traversal_rejected(self):
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "memory/../../projectB/x")

    def test_tilde_rejected(self):
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "~/.ssh/id_rsa")

    def test_null_byte_rejected(self):
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "memory/\x00evil")

    @unittest.skipUnless(SYMLINKS, NO_SYMLINKS_REASON)
    def test_symlink_escape_rejected(self):
        # Симлинк внутри домика, ведущий наружу (в чужую папку), должен быть отклонён.
        outside = tempfile.mkdtemp()
        with open(os.path.join(outside, "passwd"), "w") as fh:
            fh.write("x")
        link = os.path.join(self.home, "escape")
        os.symlink(outside, link, target_is_directory=True)
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "escape/passwd")

    @unittest.skipUnless(SYMLINKS, NO_SYMLINKS_REASON)
    def test_symlink_to_sibling_home_rejected(self):
        sibling = os.path.join(self.tmp, "projectB")
        os.makedirs(sibling)
        with open(os.path.join(sibling, "secret.txt"), "w") as fh:
            fh.write("top secret")
        link = os.path.join(self.home, "peek")
        os.symlink(sibling, link, target_is_directory=True)
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "peek/secret.txt")

    def test_windows_style_escapes_rejected(self):
        # Эти формы должны отклоняться на любой ОС и любой версии Python.
        for attempt in [
            "\\Windows\\win.ini",          # от корня диска (в Python 3.13 не isabs)
            "C:\\Windows\\win.ini",        # буква диска
            "C:foo",                        # путь относительно диска
            "\\\\server\\share\\x",        # сетевой путь UNC
            "memory/notes.txt:hidden",     # скрытый поток NTFS
            "..\\projectB\\secret.txt",    # переход вверх с обратной косой
            ".. /projectB/secret.txt",     # ".. " Windows превращает в ".."
            "memory/.../x",                 # "..." тоже нормализуется Windows
        ]:
            with self.assertRaises(PathEscapeError, msg=attempt):
                safe_resolve(self.home, attempt)

    def test_sibling_prefix_not_treated_as_inside(self):
        # projectA-extra не должен считаться внутри projectA.
        extra = os.path.join(self.tmp, "projectA-extra")
        os.makedirs(extra)
        with self.assertRaises(PathEscapeError):
            safe_resolve(self.home, "../projectA-extra/x")


class ProjectSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.homeA = os.path.join(self.tmp, "A")
        self.homeB = os.path.join(self.tmp, "B")
        self.sbA = ProjectSandbox(self.homeA)
        self.sbB = ProjectSandbox(self.homeB)

    def test_write_and_read_within_home(self):
        self.sbA.write_text("memory/x.txt", "hello")
        self.assertEqual(self.sbA.read_text("memory/x.txt"), "hello")

    def test_cross_home_access_blocked_every_way(self):
        # B кладёт секрет, A не должен достать его ничем.
        self.sbB.write_text("secrets/key.txt", "SECRET-B")
        for attempt in [
            "../B/secrets/key.txt",
            "/" + os.path.join(self.homeB, "secrets/key.txt").lstrip("/"),
            "memory/../../B/secrets/key.txt",
        ]:
            with self.assertRaises(PathEscapeError):
                self.sbA.read_text(attempt)

    @unittest.skipUnless(SYMLINKS, NO_SYMLINKS_REASON)
    def test_write_through_symlink_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.homeA, "out"), target_is_directory=True)
        with self.assertRaises(PathEscapeError):
            self.sbA.write_text("out/pwned.txt", "x")

    def test_contains_helper(self):
        self.assertTrue(self.sbA.contains("ok/file"))
        self.assertFalse(self.sbA.contains("../B/file"))

    def test_assert_isolated_detects_nesting(self):
        nested = ProjectSandbox(os.path.join(self.homeA, "inner"))
        with self.assertRaises(PathEscapeError):
            assert_isolated([self.sbA, nested])

    def test_assert_isolated_ok_for_siblings(self):
        assert_isolated([self.sbA, self.sbB])  # не бросает


if __name__ == "__main__":
    unittest.main()
