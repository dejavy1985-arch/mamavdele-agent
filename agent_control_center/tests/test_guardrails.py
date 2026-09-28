import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acc.guardrails import check_boundaries, check_risky  # noqa: E402


class GuardrailTests(unittest.TestCase):
    def test_publish_blocked(self):
        self.assertTrue(check_boundaries("опубликуй этот пост").blocked)
        self.assertIn("publish", check_boundaries("выложи в инсту").categories)

    def test_message_clients_blocked(self):
        self.assertTrue(check_boundaries("напиши клиенту про заказ").blocked)

    def test_payments_blocked(self):
        self.assertTrue(check_boundaries("оплати счёт поставщику").blocked)

    def test_change_secrets_blocked(self):
        self.assertTrue(check_boundaries("смени токен бота").blocked)
        self.assertTrue(check_boundaries("rotate the key").blocked)

    def test_normal_task_not_blocked(self):
        self.assertFalse(check_boundaries("проверь комментарии в инстаграме").blocked)
        self.assertFalse(check_boundaries("сделай раскадровку сцены").blocked)


class RiskyActionTests(unittest.TestCase):
    def test_delete_requires_confirmation(self):
        self.assertTrue(check_risky("удали старую ветку").risky)
        self.assertIn("delete_files", check_risky("rm -rf build").categories)

    def test_overwrite_requires_confirmation(self):
        self.assertTrue(check_risky("сделай git reset --hard").risky)
        self.assertTrue(check_risky("force push в main").risky)

    def test_deploy_requires_confirmation(self):
        self.assertTrue(check_risky("выкати на прод").risky)

    def test_normal_task_not_risky(self):
        self.assertFalse(check_risky("проверь комментарии").risky)
        self.assertFalse(check_risky("покажи статус проекта").risky)


if __name__ == "__main__":
    unittest.main()
