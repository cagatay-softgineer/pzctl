"""Tests for pzctl.json loading, defaults merging and dotted-path access."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pzctl.config import Config, _merge, defaults


class MergeTests(unittest.TestCase):
    def test_missing_keys_filled_from_base(self):
        merged = _merge({"a": 1, "b": 2}, {"a": 9})
        self.assertEqual(merged, {"a": 9, "b": 2})

    def test_nested_dicts_merge_recursively(self):
        merged = _merge({"x": {"a": 1, "b": 2}}, {"x": {"a": 9}})
        self.assertEqual(merged, {"x": {"a": 9, "b": 2}})

    def test_unknown_keys_pass_through(self):
        merged = _merge({"a": 1}, {"custom": "kept"})
        self.assertEqual(merged["custom"], "kept")

    def test_none_override_falls_back_to_base(self):
        self.assertEqual(_merge({"a": 1}, None), {"a": 1})

    def test_scalar_override_replaces_dict(self):
        self.assertEqual(_merge({"x": {"a": 1}}, {"x": "scalar"}), {"x": "scalar"})

    def test_base_is_not_mutated(self):
        base = {"x": {"a": 1}}
        _merge(base, {"x": {"a": 2}})
        self.assertEqual(base, {"x": {"a": 1}})

    def test_lists_are_replaced_not_merged(self):
        merged = _merge({"nums": [1, 2, 3]}, {"nums": [9]})
        self.assertEqual(merged["nums"], [9])


class TempConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "pzctl.json"

    def write_config(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")


class LoadTests(TempConfigTest):
    def test_missing_file_uses_defaults(self):
        cfg = Config(self.path)
        self.assertEqual(cfg.get("server_name"), defaults()["server_name"])

    def test_partial_file_is_filled_with_defaults(self):
        self.write_config({"server_name": "myserver"})
        cfg = Config(self.path)
        self.assertEqual(cfg.get("server_name"), "myserver")
        # Untouched branches still come from defaults.
        self.assertEqual(cfg.get("http.port"), 8077)
        self.assertEqual(cfg.get("supervisor.max_restarts"), 5)

    def test_nested_partial_override_keeps_siblings(self):
        self.write_config({"rcon": {"port": 12345}})
        cfg = Config(self.path)
        self.assertEqual(cfg.get("rcon.port"), 12345)
        self.assertEqual(cfg.get("rcon.host"), "127.0.0.1")
        self.assertIs(cfg.get("rcon.enabled"), True)

    def test_malformed_json_falls_back_to_defaults(self):
        self.path.write_text("{ this is not json", encoding="utf-8")
        cfg = Config(self.path)
        self.assertEqual(cfg.get("server_name"), "servertest")

    def test_token_is_minted_when_absent(self):
        cfg = Config(self.path)
        self.assertTrue(cfg.get("http.token"))

    def test_minted_token_is_persisted(self):
        cfg = Config(self.path)
        token = cfg.get("http.token")
        self.assertEqual(json.loads(self.path.read_text())["http"]["token"], token)

    def test_existing_token_is_reused(self):
        self.write_config({"http": {"token": "keep-me"}})
        self.assertEqual(Config(self.path).get("http.token"), "keep-me")

    def test_token_survives_reload(self):
        first = Config(self.path).get("http.token")
        self.assertEqual(Config(self.path).get("http.token"), first)


class AccessTests(TempConfigTest):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = Config(self.path)

    def test_get_dotted(self):
        self.assertEqual(self.cfg.get("http.host"), "127.0.0.1")

    def test_get_missing_returns_default(self):
        self.assertIsNone(self.cfg.get("nope.nothing"))
        self.assertEqual(self.cfg.get("nope.nothing", "fallback"), "fallback")

    def test_get_through_non_dict_returns_default(self):
        self.assertEqual(self.cfg.get("server_name.deeper", "fallback"), "fallback")

    def test_set_dotted(self):
        self.cfg.set("http.port", 9000)
        self.assertEqual(self.cfg.get("http.port"), 9000)

    def test_set_creates_intermediate_dicts(self):
        self.cfg.set("brand.new.branch", 42)
        self.assertEqual(self.cfg.get("brand.new.branch"), 42)

    def test_set_does_not_persist_on_its_own(self):
        self.cfg.set("http.port", 9000)
        self.assertEqual(json.loads(self.path.read_text())["http"]["port"], 8077)

    def test_update_merges_and_persists(self):
        self.cfg.update({"http": {"port": 9000}})
        self.assertEqual(self.cfg.get("http.port"), 9000)
        self.assertEqual(self.cfg.get("http.host"), "127.0.0.1")
        self.assertEqual(json.loads(self.path.read_text())["http"]["port"], 9000)

    def test_save_writes_valid_json(self):
        self.cfg.set("server_name", "written")
        self.cfg.save()
        self.assertEqual(json.loads(self.path.read_text())["server_name"], "written")

    def test_save_leaves_no_tmp_file(self):
        self.cfg.save()
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class DerivedPathTests(TempConfigTest):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = Config(self.path)
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.set("server_name", "myserver")

    def test_ini_path_uses_server_name(self):
        self.assertEqual(self.cfg.ini_path.name, "myserver.ini")

    def test_sandbox_path_uses_server_name(self):
        self.assertEqual(self.cfg.sandbox_path.name, "myserver_SandboxVars.lua")

    def test_spawnregions_path_uses_server_name(self):
        self.assertEqual(self.cfg.spawnregions_path.name, "myserver_spawnregions.lua")

    def test_config_files_live_under_server_dir(self):
        self.assertEqual(self.cfg.ini_path.parent, self.cfg.zomboid_dir / "Server")

    def test_save_dir_is_multiplayer_world_folder(self):
        self.assertEqual(
            self.cfg.save_dir, self.cfg.zomboid_dir / "Saves" / "Multiplayer" / "myserver"
        )

    def test_backup_dir_follows_config(self):
        self.cfg.set("backup.dir", str(self.dir / "elsewhere"))
        self.assertEqual(self.cfg.backup_dir, self.dir / "elsewhere")


if __name__ == "__main__":
    unittest.main()
