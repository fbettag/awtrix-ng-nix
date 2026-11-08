"""Declarative, identity-checked management of AWTRIX NG clocks."""

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

AMBER = "#FFB000"


def request(url, method="GET", body=None, content_type="application/json", timeout=8, headers=None):
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body, allow_nan=False).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
        return json.loads(data) if data else None


class Device:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = "http://" + cfg["host"]
        self.headers = {}
        credentials = cfg.get("credentialsFile")
        if not credentials and os.environ.get("CREDENTIALS_DIRECTORY"):
            candidate = pathlib.Path(os.environ["CREDENTIALS_DIRECTORY"]) / "auth"
            if candidate.is_file():
                credentials = candidate
        if credentials:
            auth = json.loads(pathlib.Path(credentials).read_text())
            token = base64.b64encode((auth["username"] + ":" + auth["password"]).encode()).decode()
            self.headers["Authorization"] = "Basic " + token

    def api(self, path, method="GET", body=None):
        return request(self.base + "/api/v1/" + path, method, body, headers=self.headers)

    def raw(self, path):
        req = urllib.request.Request(self.base + path, headers=self.headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.read()

    def identity(self):
        data = self.api("device")
        if data.get("uid") != self.cfg["uid"] or data.get("soc") != "esp32" or data.get("updateImage") != "firmware-awtrix-ng.bin":
            raise RuntimeError("Device identity or firmware target mismatch; refusing writes")
        return data

    def notify(self, text, duration=10, color=AMBER):
        if not text.strip():
            raise RuntimeError("Notification text must not be empty")
        if duration <= 0:
            raise RuntimeError("Notification duration must be positive")
        if len(color) != 7 or not color.startswith("#") or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
            raise RuntimeError("Notification color must be #RRGGBB")
        self.identity()
        result = self.api("notifications", "POST", {
            "name": "manual", "text": text, "textColor": color,
            "durationMs": duration * 1000, "stack": True, "hold": False,
            "repeat": 1})
        if result.get("ok") is not True:
            raise RuntimeError("Notification was not accepted")

    def configure(self, check=False):
        data = self.identity()
        if data.get("version") != self.cfg["version"]:
            if check:
                raise RuntimeError("Firmware differs from the pinned version")
            self.update_firmware()
        for endpoint, method in [("system", "PUT"), ("settings", "PATCH")]:
            current = self.api(endpoint)
            diff = {key: value for key, value in self.cfg[endpoint].items() if current.get(key) != value}
            if diff:
                if check:
                    raise RuntimeError(f"Managed {endpoint} differs: {', '.join(diff)}")
                self.api(endpoint, method, diff)
                current = self.api(endpoint)
                if any(current.get(key) != value for key, value in diff.items()):
                    raise RuntimeError(f"Could not verify {endpoint}")
                if endpoint == "system" and set(diff) & {"hostname", "netStatic", "ip", "gateway", "subnet", "dns1", "dns2"}:
                    self.api("device/reboot", "POST")
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        time.sleep(3)
                        try:
                            if self.identity()["uptimeSeconds"] < 30:
                                break
                        except (OSError, ValueError):
                            pass
                    else:
                        raise RuntimeError("Reboot after network settings could not be verified")
        print(f"AWTRIX {self.cfg['uid']}: NG {self.cfg['version']}, settings verified", flush=True)

    def update_firmware(self):
        # A NixOS rollback cannot undo an ESP32 update. Keep a device backup first.
        target = pathlib.Path(self.cfg["stateDir"]) / f"before-{time.time_ns()}.zip"
        self.backup(target)
        firmware = pathlib.Path(self.cfg["firmware"]).read_bytes()
        if not firmware or firmware[0] != 0xE9 or len(firmware) > 1769472:
            raise RuntimeError("Invalid ESP32 OTA image")
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="firmware"; filename="firmware.bin"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + firmware + f"\r\n--{boundary}--\r\n".encode()
        request(self.base + "/update", "POST", body, "multipart/form-data; boundary=" + boundary, timeout=90, headers=self.headers)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(3)
            try:
                if self.identity().get("version") == self.cfg["version"]:
                    return
            except (OSError, ValueError):
                pass
        raise RuntimeError("Firmware update could not be verified")

    def backup(self, target):
        # Same uncompressed format and manifest as NG's webui/src/shared/backup.js.
        if target.exists():
            raise RuntimeError("Backup destination already exists")
        partial = target.with_suffix(".partial")
        system = self.api("system?secrets=1")
        with partial.open("xb") as output:
            partial.chmod(0o600)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
                def put(name, data):
                    archive.writestr(name, json.dumps(data, allow_nan=False))
                put("manifest.json", {"app": "awtrix-ng", "backupFormat": 1})
                put("config/wifi.json", {key: system.pop(key) for key in ("wifiSsid", "wifiPass")})
                put("config/system.json", system)
                put("config/settings.json", self.api("settings"))
                try:
                    archive.writestr("apploop.json", self.raw("/apploop.json"))
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    apps = self.api("apps")
                    put("apploop.json", {"order": [a["name"] for a in apps if a["inLoop"]],
                                         "disabled": [a["name"] for a in apps if not a["enabled"]]})
                for folder in ("ICONS", "PALETTES", "SCRIPTS"):
                    files = self.api("files?dir=" + urllib.parse.quote("/" + folder, safe=""))
                    for entry in files["files"]:
                        name = pathlib.PurePosixPath(entry["name"]).name
                        path = folder + "/" + name
                        archive.writestr(path, self.raw("/" + urllib.parse.quote(path)))
                for melody in self.api("audio/melodies").get("melodies", []):
                    name = pathlib.PurePosixPath(melody["name"]).name
                    archive.writestr("MELODIES/" + name + ".txt", melody["rtttl"])
                put("config/icon-origins.json", self.api("icons/origins"))
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Device backup failed validation")
        partial.rename(target)

    def apply_pages(self):
        # Only named, declaratively managed pages are touched. Other user apps remain.
        for name, payload in self.cfg.get("pages", {}).items():
            self.api("apps/pushed/" + urllib.parse.quote(name, safe=""), "PUT", payload)
        if "rotation" in self.cfg:
            self.api("apps/order", "PUT", self.cfg["rotation"])


def main():
    parser = argparse.ArgumentParser(prog="awtrix-ng-nix", description=__doc__)
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("configure", "check", "status", "backup"):
        commands.add_parser(command)
    notify = commands.add_parser("notify", help="Queue a temporary message")
    notify.add_argument("text")
    notify.add_argument("--duration", type=int, default=10, metavar="SECONDS")
    notify.add_argument("--color", default=AMBER, metavar="#RRGGBB")
    args = parser.parse_args()
    cfg = json.loads(pathlib.Path(args.config).read_text())
    device = Device(cfg)
    if args.command in ("configure", "check"):
        device.configure(check=args.command == "check")
        if args.command == "configure":
            device.apply_pages()
    elif args.command == "status":
        print(json.dumps({"device": device.identity(), "apps": device.api("apps")}, indent=2))
    elif args.command == "notify":
        device.notify(args.text, args.duration, args.color)
        print(f"Notification queued ({args.duration} s).")
    elif args.command == "backup":
        device.identity()
        target = pathlib.Path(cfg["stateDir"]) / f"manual-{time.time_ns()}.zip"
        device.backup(target)
        print(f"Verified backup: {target}")


def cli():
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"AWTRIX command failed: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
