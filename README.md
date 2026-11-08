# awtrix-ng-nix

Declarative management of **AWTRIX NG clocks on classic ESP32 hardware** through
their local HTTP API. Includes a NixOS module, a Python client and a CLI.

- Pin firmware and verify the clock's MAC-derived identity before writing.
- Apply device and display settings with read-back verification.
- Define static pushed apps and rotation order in Nix.
- Queue custom messages without replacing an active notification.
- Save a private, restore-compatible device backup before firmware updates.
- Supply existing HTTP Basic credentials through systemd credentials at runtime.

Tested with AWTRIX NG **1.2.3** on a 4 MB ESP32 clock. ESP32-S3 and TC002 images
are intentionally refused. AWTRIX 3 requires a one-time USB migration using the
[upstream installation guide](https://blueforcer.github.io/awtrix-ng/esp32/getting-started/flashing/).
Subsequent NG updates use the clock's OTA endpoint.

## NixOS

Add the flake input and import its module:

```nix
inputs.awtrix-ng-nix = {
  url = "github:fbettag/awtrix-ng-nix";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

```nix
{ inputs, ... }: {
  imports = [ inputs.awtrix-ng-nix.nixosModules.default ];
  services.awtrix-ng = {
    enable = true;
    devices.desk = {
      host = "192.0.2.42";       # replace with your clock's address
      uid = "001122334455";     # lowercase Wi-Fi MAC without colons
      system = { tzName = "Europe/Berlin"; tz = "CET-1CEST,M3.5.0,M10.5.0/3"; };
      settings = { volume = 0; timeMode = 0; };
      pages.hello = { text = "HELLO"; durationMs = 7000; };
      rotation = { order = [ "Time" "hello" ]; disabled = [ "Date" "Temperature" "Humidity" "Battery" ]; };
    };
  };
}
```

The default firmware is pinned by version and SHA-256. To change it, set **both**
`version` and `firmware` to a matching, hash-pinned ESP32 OTA image from upstream.
The checked-in `flake.lock` pins dependencies.

Each device gets `awtrix-ng-<name>.service`, a five-minute reconciliation timer,
`/etc/awtrix-ng/<name>.json` and an `awtrix-ng-<name>` CLI helper. Deploy the NixOS
host through its normal deployment mechanism, including deploy-rs. The clock
does not run Nix or SSH. A NixOS rollback cannot undo changes to its firmware.

```sh
awtrix-ng-desk check
awtrix-ng-desk status
awtrix-ng-desk notify 'Hello from Nix' --duration 10 --color '#55CC55'
sudo awtrix-ng-desk backup
```

Notifications queue behind existing messages and do not request sound or force
the display on. Long messages scroll to completion. Named static pages are
refreshed on each apply; removing a declaration does not delete an existing app.
Unlisted settings are preserved. A configured `rotation.disabled` is the complete
list of disabled apps, matching NG's API semantics.

Dynamic sensor feeds should use a separate process and bounded `lifetimeMs`
values. The NixOS module manages the clock; it does not collect home telemetry.

## Authentication and backups

For a clock with HTTP Basic authentication already enabled:

```nix
services.awtrix-ng.devices.desk.credentialsFile = "/run/secrets/awtrix-auth";
```

The runtime file contains a JSON object with `username` and `password`. The module
passes it using `LoadCredential`, never through Nix interpolation. Do not put
Wi-Fi, MQTT or HTTP passwords in `system`; the module rejects those fields.
Provision or rotate device passwords separately through a trusted runtime path.

Backups include credentials. They are stored with mode `0600` beneath
`/var/lib/awtrix-ng-<name>/`. Incomplete backups keep a `.partial` extension.
Firmware uploads require a completed and CRC-checked backup. Restore ZIPs using
the upstream web UI. Keep an original full-flash backup before the USB migration.

## Python and standalone CLI

```sh
nix run github:fbettag/awtrix-ng-nix -- --config /path/to/clock.json status
```

The JSON configuration uses `host`, `uid`, `version`, `firmware`, `stateDir`,
`system`, `settings`, optional `pages`, optional `rotation`, and optionally
`credentialsFile`. The NixOS module generates it automatically.

```python
from awtrix_ng_nix import Device

clock = Device({"host": "192.0.2.42", "uid": "001122334455"})
clock.notify("Hello", duration=10, color="#FFB000")
```

## Development

```sh
python -m unittest discover -s tests -v
nix fmt
nix flake check
```

The CLI runs on Linux and macOS; the service module targets NixOS. Firmware
updates and Wi-Fi changes can interrupt the connection. A timeout is reported
as a failure until the desired state can be verified.

## License

This management code is MIT licensed. AWTRIX NG firmware is a separate upstream
project under [its own license](https://github.com/Blueforcer/awtrix-ng/blob/main/LICENSE.md).
Firmware is fetched from upstream with a fixed hash, not included in this repository.
