"""Тесты диспетчера: доступ, границы, маршрут, очередь, подтверждение, команды."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import dispatcher as disp  # noqa: E402
from acc import store_db  # noqa: E402
from acc.dispatcher import Dispatcher  # noqa: E402


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.config, self.base = _fixtures.build_center(allowed_user_ids=(111,))
        self.d = Dispatcher(self.config)

    def tearDown(self):
        self.d.store.close()

    # -- доступ и границы ---------------------------------------------------
    def test_stranger_denied_and_silent(self):
        r = self.d.handle(999, "проверь комментарии")
        self.assertEqual(r.type, disp.DENIED)
        self.assertTrue(r.silent)

    def test_forbidden_action_not_executed(self):
        r = self.d.handle(111, "опубликуй пост в инстаграме")
        self.assertEqual(r.type, disp.BOUNDARY_BLOCKED)
        self.assertEqual(self.d.store.list_tasks(), [])  # в очередь не попало

    # -- маршрутизация и очередь -------------------------------------------
    def test_task_accepted_into_queue_with_reply_address(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме", chat_id=111, reply_to=7)
        self.assertEqual(r.type, disp.ACCEPTED)
        self.assertEqual(r.project_id, "mamavdele-agent")
        task = self.d.store.get_task(r.data["task_id"])
        self.assertEqual(task["status"], store_db.QUEUED)
        self.assertEqual((task["chat_id"], task["reply_to"]), (111, 7))

    def test_manual_adapter_warned_in_acceptance(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        self.assertIn("только записано", r.message)

    def test_queue_position_reported(self):
        self.d.handle(111, "проверь комментарий в инстаграме")
        r2 = self.d.handle(111, "ещё комментарий в инстаграме")
        self.assertEqual(r2.data["ahead"], 1)
        self.assertIn("Перед ней в очереди: 1", r2.message)

    def test_clarify_when_ambiguous(self):
        r = self.d.handle(111, "комментарий и видео")
        self.assertEqual(r.type, disp.CLARIFY)
        self.assertEqual(set(r.data["candidates"]), {"mamavdele-agent", "content-studio"})
        self.assertIn("/to", r.message)

    def test_explicit_project_with_to(self):
        r = self.d.handle(111, "/to content-studio комментарий и видео")
        self.assertEqual(r.type, disp.ACCEPTED)
        self.assertEqual(r.project_id, "content-studio")

    def test_unknown_project(self):
        self.assertEqual(self.d.handle(111, "какая сегодня погода").type, disp.UNKNOWN)

    def test_not_connected_reported_honestly(self):
        manifests = _fixtures.default_manifests()
        manifests[1]["status"] = "not_connected"
        config, _ = _fixtures.build_center(manifests=manifests, allowed_user_ids=(111,))
        d = Dispatcher(config)
        r = d.handle(111, "сделай промпт для сцены видео seedance")
        self.assertEqual(r.type, disp.NOT_CONNECTED)
        self.assertEqual(d.store.list_tasks(), [])
        d.store.close()

    # -- подтверждение опасных действий ------------------------------------
    def test_risky_action_waits_for_confirmation(self):
        r = self.d.handle(111, "удали комментарий в инстаграме", chat_id=111, reply_to=3)
        self.assertEqual(r.type, disp.NEEDS_CONFIRMATION)
        tid = r.data["task_id"]
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.AWAITING_CONFIRMATION)
        self.assertIsNone(self.d.store.next_queued())  # до подтверждения не выполняется
        r2 = self.d.handle(111, f"/confirm {tid}")
        self.assertEqual(r2.type, disp.ACCEPTED)
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.QUEUED)

    def test_risky_action_can_be_rejected(self):
        r = self.d.handle(111, "снеси ветку в инстаграм-проекте комментарии")
        tid = r.data["task_id"]
        self.d.handle(111, f"/reject {tid}")
        self.assertEqual(self.d.store.get_task(tid)["status"], store_db.REJECTED)

    def test_reject_finished_task_refused(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        self.d.store.set_task_status(r.data["task_id"], store_db.DONE, result="ок")
        r2 = self.d.handle(111, f"/reject {r.data['task_id']}")
        self.assertIn("Отклонить нельзя", r2.message)

    # -- команды ------------------------------------------------------------
    def test_projects_command(self):
        r = self.d.handle(111, "/projects")
        self.assertEqual(r.type, disp.COMMAND)
        self.assertIn("Мамавделе", r.message)

    def test_status_command(self):
        r = self.d.handle(111, "/status")
        self.assertIn("подключено", r.message)
        self.assertIn("Исполнитель не запущен", r.message)

    def test_cancel_queued_without_worker(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        r2 = self.d.handle(111, f"/cancel {r.data['task_id']}")
        self.assertIn("отменена", r2.message)
        self.assertEqual(self.d.store.get_task(r.data["task_id"])["status"], store_db.CANCELLED)

    def test_result_command_paginates(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        self.d.store.set_task_status(r.data["task_id"], store_db.DONE, result="я" * 8000)
        r1 = self.d.handle(111, f"/result {r.data['task_id']}")
        self.assertIn("страница 1 из 3", r1.message)
        r3 = self.d.handle(111, f"/result {r.data['task_id']} 3")
        self.assertIn("страница 3 из 3", r3.message)

    def test_task_status_command(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        r2 = self.d.handle(111, f"/status {r.data['task_id']}")
        self.assertIn("в очереди", r2.message)

    def test_queue_and_history_commands(self):
        self.d.handle(111, "проверь комментарий в инстаграме")
        self.assertIn("Очередь", self.d.handle(111, "/queue").message)
        self.assertIn("История", self.d.handle(111, "/history").message)

    def test_actions_are_logged(self):
        self.d.handle(111, "проверь комментарий в инстаграме")
        self.assertTrue(any(e.get("event") == "task_accepted" for e in self.d.audit.tail(20)))

    # -- восстановление после перезапуска ----------------------------------
    def test_recovery_marks_interrupted_and_keeps_queue(self):
        running = self.d.store.enqueue_task("mamavdele-agent", "шло", chat_id=111, reply_to=1)
        self.d.store.claim_next()  # стала processing, как будто агент работал
        waiting = self.d.store.enqueue_task("mamavdele-agent", "ждёт")
        self.d.store.close()

        d2 = Dispatcher(self.config)  # перезапуск: новый экземпляр, те же файлы
        counts = d2.recover_after_restart()
        self.assertEqual(counts["interrupted"], 1)
        self.assertEqual(counts["resumed"], 1)
        self.assertEqual(d2.store.get_task(running["id"])["status"], store_db.FAILED)
        self.assertEqual(d2.interrupted_tasks[0]["chat_id"], 111)  # знаем, кому сообщить
        self.assertEqual(d2.store.get_task(waiting["id"])["status"], store_db.QUEUED)
        self.d = d2

    def test_confirmation_survives_restart(self):
        r = self.d.handle(111, "удали комментарий в инстаграме")
        self.d.store.close()
        d2 = Dispatcher(self.config)
        d2.recover_after_restart()
        self.assertEqual(d2.handle(111, f"/confirm {r.data['task_id']}").type, disp.ACCEPTED)
        self.d = d2


if __name__ == "__main__":
    unittest.main()
