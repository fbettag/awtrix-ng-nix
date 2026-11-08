import copy
import json
import pathlib
import tempfile
import unittest
import urllib.error
import zipfile
from unittest.mock import patch

import awtrix_ng_nix as a

CFG = {"host": "192.0.2.42", "uid": "001122334455", "version": "1.2.3", "system": {}, "settings": {"volume": 0}}
IDENTITY = {"uid": CFG["uid"], "soc": "esp32", "updateImage": "firmware-awtrix-ng.bin", "version": "1.2.3"}


class ClientTest(unittest.TestCase):
    def test_wrong_identity_never_receives_writes(self):
        device = a.Device(CFG)
        with patch.object(device, "api", return_value={"uid": "wrong"}) as api:
            for action in (device.configure, lambda: device.notify("Hello")):
                with self.assertRaises(RuntimeError):
                    action()
            self.assertTrue(all(call.args == ("device",) for call in api.call_args_list))

    def test_check_never_updates_firmware(self):
        device = a.Device(CFG)
        with patch.object(device, "api", return_value={**IDENTITY, "version": "1.2.2"}) as api:
            with self.assertRaises(RuntimeError):
                device.configure(check=True)
            api.assert_called_once_with("device")

    def test_apply_is_idempotent_and_verifies_readback(self):
        device = a.Device(CFG)
        state = {"system": {}, "settings": {"volume": 60}}
        calls = []
        def api(path, method="GET", body=None):
            calls.append((path, method))
            if path == "device":
                return IDENTITY
            if body:
                state[path].update(body)
            return copy.deepcopy(state[path])
        with patch.object(device, "api", side_effect=api):
            device.configure()
            self.assertEqual(state["settings"]["volume"], 0)
            self.assertEqual(calls.count(("settings", "PATCH")), 1)
            calls.clear()
            device.configure()
            self.assertTrue(all(method == "GET" for _, method in calls))

    def test_failed_readback_is_not_reported_as_success(self):
        device = a.Device(CFG)
        with patch.object(device, "api", side_effect=[IDENTITY, {}, {"volume": 60}, {"ok": True}, {"volume": 60}]):
            with self.assertRaises(RuntimeError):
                device.configure()

    def test_notification_queues_without_overwriting_alerts(self):
        device = a.Device(CFG)
        with patch.object(device, "identity"), patch.object(device, "api", return_value={"ok": True}) as api:
            device.notify("Hello", 12, "#55CC55")
            endpoint, method, body = api.call_args.args
            self.assertEqual((endpoint, method), ("notifications", "POST"))
            self.assertEqual(body["durationMs"], 12000)
            self.assertTrue(body["stack"])
            self.assertFalse(body["hold"])
            self.assertNotIn("sound", body)

    def test_invalid_notification_never_contacts_clock(self):
        device = a.Device(CFG)
        with patch.object(device, "api") as api:
            for text, seconds, color in [(" ", 10, a.AMBER), ("Hello", 0, a.AMBER), ("Hello", 5, "red")]:
                with self.assertRaises(RuntimeError):
                    device.notify(text, seconds, color)
            api.assert_not_called()

    def test_credentials_are_loaded_only_at_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)/"auth.json"
            path.write_text(json.dumps({"username": "fixture", "password": "fixture"}))
            device = a.Device({**CFG, "credentialsFile": str(path)})
            with patch.object(a, "request", return_value=IDENTITY) as request:
                device.identity()
                self.assertTrue(request.call_args.kwargs["headers"]["Authorization"].startswith("Basic "))
            self.assertNotIn("password", device.cfg)

    def test_backup_works_before_rotation_file_exists(self):
        device = a.Device(CFG)
        responses = {"system?secrets=1": {"wifiSsid": "fixture", "wifiPass": "fixture"},
                     "settings": {}, "apps": [{"name": "Time", "enabled": True, "inLoop": True}],
                     "audio/melodies": {"melodies": []}, "icons/origins": {"icons": []}}
        def api(path):
            return copy.deepcopy({"files": []} if path.startswith("files?") else responses[path])
        with tempfile.TemporaryDirectory() as folder, patch.object(device, "api", side_effect=api), \
             patch.object(device, "raw", side_effect=urllib.error.HTTPError("fixture", 404, "missing", {}, None)):
            target = pathlib.Path(folder)/"backup.zip"
            device.backup(target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist()[0], "manifest.json")
                self.assertEqual(json.loads(archive.read("manifest.json"))["backupFormat"], 1)
                self.assertEqual(json.loads(archive.read("apploop.json"))["order"], ["Time"])
            with self.assertRaises(RuntimeError):
                device.backup(target)


if __name__ == "__main__":
    unittest.main()
