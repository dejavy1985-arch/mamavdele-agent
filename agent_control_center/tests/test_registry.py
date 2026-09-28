import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fixtures  # noqa: E402
from acc.registry import Registry, RegistryError, load_project  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_loads_two_homes(self):
        config, _ = _fixtures.build_center()
        reg = Registry(config.homes_dir).load()
        self.assertEqual(len(reg.all()), 2)
        self.assertIsNotNone(reg.get("mamavdele-agent"))
        self.assertIsNotNone(reg.get("content-studio"))

    def test_connected_filter(self):
        manifests = _fixtures.default_manifests()
        manifests[1]["status"] = "not_connected"
        config, _ = _fixtures.build_center(manifests=manifests)
        reg = Registry(config.homes_dir).load()
        self.assertEqual([p.id for p in reg.connected()], ["mamavdele-agent"])

    def test_invalid_status_rejected(self):
        manifests = _fixtures.default_manifests()
        manifests[0]["status"] = "wat"
        config, _ = _fixtures.build_center(manifests=manifests)
        with self.assertRaises(RegistryError):
            Registry(config.homes_dir).load()

    def test_missing_required_field_rejected(self):
        config, base = _fixtures.build_center(manifests=[])
        home = _fixtures.make_home(config.homes_dir, {"id": "x", "title": "X",
                                                      "adapter": {"type": "manual"}})
        # Уберём adapter, чтобы проверить валидацию.
        import json
        with open(os.path.join(home, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "x", "title": "X"}, fh)
        with self.assertRaises(RegistryError):
            load_project(os.path.join(home, "manifest.json"))


if __name__ == "__main__":
    unittest.main()
