import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc import dispatcher as disp  # noqa: E402
from acc.dispatcher import Dispatcher  # noqa: E402


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.config, self.base = _fixtures.build_center(allowed_user_ids=(111,))
        self.d = Dispatcher(self.config)

    # -- allowlist ----------------------------------------------------------
    def test_stranger_denied_and_silent(self):
        r = self.d.handle(999, "проверь комментарии")
        self.assertEqual(r.type, disp.DENIED)
        self.assertTrue(r.silent)

    # -- границы ------------------------------------------------------------
    def test_forbidden_action_not_executed(self):
        r = self.d.handle(111, "опубликуй пост в инстаграме")
        self.assertEqual(r.type, disp.BOUNDARY_BLOCKED)

    # -- маршрутизация ------------------------------------------------------
    def test_routes_to_moderation_and_queues(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")
        self.assertEqual(r.type, disp.ROUTED)
        self.assertEqual(r.project_id, "mamavdele-agent")
        self.assertTrue(r.data["delivered"])
        inbox = os.path.join(self.config.homes_dir, "mamavdele-agent", "inbox", "tasks.jsonl")
        self.assertTrue(os.path.exists(inbox))

    def test_clarify_when_ambiguous(self):
        r = self.d.handle(111, "комментарий и видео")
        self.assertEqual(r.type, disp.CLARIFY)
        self.assertEqual(set(r.data["candidates"]), {"mamavdele-agent", "content-studio"})

    def test_unknown_project(self):
        r = self.d.handle(111, "какая сегодня погода")
        self.assertEqual(r.type, disp.UNKNOWN)

    def test_not_connected_reported_honestly(self):
        manifests = _fixtures.default_manifests()
        manifests[1]["status"] = "not_connected"
        config, _ = _fixtures.build_center(manifests=manifests, allowed_user_ids=(111,))
        d = Dispatcher(config)
        r = d.handle(111, "сделай промпт для сцены видео seedance")
        self.assertEqual(r.type, disp.NOT_CONNECTED)
        self.assertEqual(r.project_id, "content-studio")

    # -- команды ------------------------------------------------------------
    def test_projects_command(self):
        r = self.d.handle(111, "/projects")
        self.assertEqual(r.type, disp.COMMAND)
        self.assertIn("Мамавделе", r.message)

    def test_status_command(self):
        r = self.d.handle(111, "/status")
        self.assertEqual(r.type, disp.COMMAND)
        self.assertIn("подключено", r.message)

    # -- восстановление журнала --------------------------------------------
    def test_actions_are_logged(self):
        self.d.handle(111, "проверь комментарий в инстаграме")
        events = self.d.audit.tail(20)
        self.assertTrue(any(e.get("event") == "task_routed" for e in events))

    # -- подтверждение опасных действий ------------------------------------
    def test_risky_action_needs_confirmation_then_delivers(self):
        r = self.d.handle(111, "удали комментарий в инстаграме")
        self.assertEqual(r.type, disp.NEEDS_CONFIRMATION)
        task_id = r.data["task_id"]
        # Пока не подтверждено, поручение в домик не ушло.
        inbox = os.path.join(self.config.homes_dir, "mamavdele-agent", "inbox", "tasks.jsonl")
        self.assertFalse(os.path.exists(inbox))
        # Подтверждаем -> доставка.
        r2 = self.d.handle(111, f"/confirm {task_id}")
        self.assertEqual(r2.type, disp.ROUTED)
        self.assertTrue(os.path.exists(inbox))

    def test_risky_action_can_be_rejected(self):
        r = self.d.handle(111, "снеси ветку в инстаграм-проекте комментарии")
        self.assertEqual(r.type, disp.NEEDS_CONFIRMATION)
        task_id = r.data["task_id"]
        r2 = self.d.handle(111, f"/reject {task_id}")
        self.assertEqual(r2.type, disp.COMMAND)
        from acc import store_db
        self.assertEqual(self.d.store.get_task(task_id)["status"], store_db.REJECTED)

    # -- честные статусы и устойчивость -------------------------------------
    def test_undelivered_task_marked_failed_not_stuck(self):
        from acc import store_db
        manifests = [{
            "id": "live", "title": "Живой", "status": "connected",
            "keywords": ["видео"],
            "adapter": {"type": "cross_session", "target_session_name": "s"},
        }]
        config, _ = _fixtures.build_center(manifests=manifests, allowed_user_ids=(111,))
        d = Dispatcher(config)  # cross_session_available=False -> доставка невозможна
        r = d.handle(111, "смонтируй видео")
        self.assertEqual(r.type, disp.ROUTED)
        self.assertFalse(r.data["delivered"])
        self.assertEqual(d.store.get_task(r.data["task_id"])["status"], store_db.FAILED)
        self.assertEqual(d.handle(111, "/queue").message, "Очередь пуста.")

    def test_delivery_exception_does_not_leave_task_hanging(self):
        from unittest import mock
        from acc import store_db

        class Boom:
            def deliver(self, project, task):
                raise OSError("диск недоступен")

        with mock.patch("acc.dispatcher.build_adapter", return_value=Boom()):
            r = self.d.handle(111, "проверь комментарий в инстаграме")
        self.assertEqual(r.type, disp.ERROR)
        self.assertEqual(self.d.store.get_task(r.data["task_id"])["status"], store_db.FAILED)

    def test_reject_finished_task_refused(self):
        r = self.d.handle(111, "проверь комментарий в инстаграме")  # сразу done
        r2 = self.d.handle(111, f"/reject {r.data['task_id']}")
        self.assertIn("Отклонить нельзя", r2.message)

    # -- восстановление после перезапуска ----------------------------------
    def test_recovery_redelivers_queued_task(self):
        from acc import store_db
        # Поручение принято, но процесс "упал" до передачи агенту.
        t = self.d.store.enqueue_task("mamavdele-agent", "проверь комментарий")
        self.d.store.close()
        d2 = Dispatcher(self.config)  # перезапуск: новый экземпляр, те же файлы
        counts = d2.recover_after_restart()
        self.assertEqual(counts["redelivered"], 1)
        self.assertEqual(d2.store.get_task(t["id"])["status"], store_db.DONE)
        inbox = os.path.join(self.config.homes_dir, "mamavdele-agent", "inbox", "tasks.jsonl")
        self.assertTrue(os.path.exists(inbox))

    def test_recovery_marks_interrupted_processing(self):
        from acc import store_db
        t = self.d.store.enqueue_task("mamavdele-agent", "что-то")
        self.d.store.set_task_status(t["id"], store_db.PROCESSING)  # оборвалось посередине
        self.d.store.close()
        d2 = Dispatcher(self.config)
        counts = d2.recover_after_restart()
        self.assertEqual(counts["interrupted"], 1)
        got = d2.store.get_task(t["id"])
        self.assertEqual(got["status"], store_db.FAILED)
        self.assertEqual(got["result"], "interrupted_by_restart")

    def test_confirmation_survives_restart(self):
        r = self.d.handle(111, "удали комментарий в инстаграме")
        self.assertEqual(r.type, disp.NEEDS_CONFIRMATION)
        self.d.store.close()
        d2 = Dispatcher(self.config)
        d2.recover_after_restart()  # ожидающие подтверждения не трогает
        r2 = d2.handle(111, f"/confirm {r.data['task_id']}")
        self.assertEqual(r2.type, disp.ROUTED)

    def test_queue_and_history_commands(self):
        self.d.handle(111, "проверь комментарий в инстаграме")
        rq = self.d.handle(111, "/queue")
        rh = self.d.handle(111, "/history")
        self.assertEqual(rq.type, disp.COMMAND)
        self.assertEqual(rh.type, disp.COMMAND)
        self.assertIn("История", rh.message)


if __name__ == "__main__":
    unittest.main()
