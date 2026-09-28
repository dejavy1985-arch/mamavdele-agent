import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import router  # noqa: E402
from acc.registry import Registry  # noqa: E402


class RouterTests(unittest.TestCase):
    def setUp(self):
        config, _ = _fixtures.build_center()
        self.projects = Registry(config.homes_dir).load().all()

    def test_clear_match_moderation(self):
        r = router.route("проверь новый комментарий в инстаграме", self.projects)
        self.assertEqual(r.outcome, router.MATCH)
        self.assertEqual(r.best.project_id, "mamavdele-agent")

    def test_clear_match_video(self):
        r = router.route("собери промпт для сцены видео в seedance", self.projects)
        self.assertEqual(r.outcome, router.MATCH)
        self.assertEqual(r.best.project_id, "content-studio")

    def test_alias_wins(self):
        r = router.route("мамавделе: что там по очереди", self.projects)
        self.assertEqual(r.outcome, router.MATCH)
        self.assertEqual(r.best.project_id, "mamavdele-agent")

    def test_unknown_when_no_signal(self):
        r = router.route("привет, как дела", self.projects)
        self.assertEqual(r.outcome, router.UNKNOWN)

    def test_wordform_matched_by_stem(self):
        # "комментарии" (форма) должно находить ключевое слово "комментарий".
        r = router.route("посмотри комментарии в инстаграме", self.projects)
        self.assertEqual(r.outcome, router.MATCH)
        self.assertEqual(r.best.project_id, "mamavdele-agent")

    def test_ambiguous_when_both_mentioned_equally(self):
        # По одному ключевому слову на каждый проект -> равный счёт -> уточнение.
        r = router.route("комментарий и видео", self.projects)
        self.assertEqual(r.outcome, router.AMBIGUOUS)
        ids = {c.project_id for c in r.candidates}
        self.assertEqual(ids, {"mamavdele-agent", "content-studio"})


if __name__ == "__main__":
    unittest.main()
