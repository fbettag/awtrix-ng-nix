{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.awtrix-ng;
  defaultFirmware = pkgs.fetchurl {
    url = "https://github.com/Blueforcer/awtrix-ng/releases/download/v1.2.3/firmware-awtrix-ng.bin";
    hash = "sha256-cCQQc/O3KIsGNxof5DJv96uUP6vZCO5tNJhzUWS5IpQ=";
  };
  json = pkgs.formats.json { };
  deviceType = lib.types.submodule {
    options = {
      host = lib.mkOption {
        type = lib.types.str;
        description = "Clock hostname or address, optionally including a port.";
      };
      uid = lib.mkOption {
        type = lib.types.strMatching "[0-9a-f]{12}";
        description = "Expected Wi-Fi MAC address, lowercase without colons.";
      };
      version = lib.mkOption {
        type = lib.types.str;
        default = "1.2.3";
        description = "Exact NG firmware version to apply and verify.";
      };
      firmware = lib.mkOption {
        type = lib.types.path;
        default = defaultFirmware;
        description = "Pinned classic ESP32 OTA image. Must match version.";
      };
      system = lib.mkOption {
        type = json.type;
        default = { };
        description = "Managed non-secret /api/v1/system fields.";
      };
      settings = lib.mkOption {
        type = json.type;
        default = { };
        description = "Managed /api/v1/settings fields.";
      };
      pages = lib.mkOption {
        type = lib.types.attrsOf json.type;
        default = { };
        description = "Named static pushed apps, refreshed on each apply. Removed names are left untouched.";
      };
      rotation = lib.mkOption {
        type = lib.types.nullOr json.type;
        default = null;
        description = "Optional rotation object with order and disabled lists.";
      };
      credentialsFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Runtime JSON file with username and password for existing HTTP Basic authentication.";
      };
      autoApply = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Apply at boot and periodically using a systemd timer.";
      };
      interval = lib.mkOption {
        type = lib.types.str;
        default = "5min";
        description = "Time between reconciliations (systemd duration).";
      };
    };
  };
  configs = lib.mapAttrs (
    name: device:
    json.generate "awtrix-ng-${name}.json" (
      {
        inherit (device)
          host
          uid
          version
          firmware
          system
          settings
          pages
          ;
        stateDir = "/var/lib/awtrix-ng-${name}";
      }
      // lib.optionalAttrs (device.rotation != null) { inherit (device) rotation; }
    )
  ) cfg.devices;
in
{
  options.services.awtrix-ng = {
    enable = lib.mkEnableOption "declarative AWTRIX NG management";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.python3Packages.callPackage ../package.nix { };
    };
    devices = lib.mkOption {
      type = lib.types.attrsOf deviceType;
      default = { };
      description = "Classic ESP32 clocks managed over their local NG API.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = lib.mapAttrsToList (name: device: {
      assertion =
        !(builtins.any (key: builtins.hasAttr key device.system) [
          "wifiPass"
          "mqttPass"
          "authPass"
        ]);
      message = "AWTRIX NG ${name}: do not put passwords in system settings or the Nix store. Provision them separately at runtime.";
    }) cfg.devices;
    environment.systemPackages = [
      cfg.package
    ]
    ++ lib.mapAttrsToList (
      name: _:
      pkgs.writeShellApplication {
        name = "awtrix-ng-${name}";
        text = ''exec ${lib.getExe cfg.package} --config ${configs.${name}} "$@"'';
      }
    ) cfg.devices;
    environment.etc = lib.mapAttrs' (
      name: _: lib.nameValuePair "awtrix-ng/${name}.json" { source = configs.${name}; }
    ) cfg.devices;
    systemd.services = lib.mapAttrs' (
      name: device:
      lib.nameValuePair "awtrix-ng-${name}" {
        description = "Apply managed AWTRIX NG configuration (${name})";
        wantedBy = lib.optional device.autoApply "multi-user.target";
        wants = [ "network-online.target" ];
        after = [ "network-online.target" ];
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${lib.getExe cfg.package} --config ${configs.${name}} configure";
          TimeoutStartSec = 300;
          StateDirectory = "awtrix-ng-${name}";
          StateDirectoryMode = "0700";
          UMask = "0077";
          DynamicUser = true;
          NoNewPrivileges = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          PrivateDevices = true;
          LoadCredential = lib.optional (device.credentialsFile != null) "auth:${device.credentialsFile}";
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
          ];
        };
      }
    ) cfg.devices;
    systemd.timers = lib.mapAttrs' (
      name: _:
      lib.nameValuePair "awtrix-ng-${name}" {
        wantedBy = [ "timers.target" ];
        timerConfig = {
          OnBootSec = "1min";
          OnUnitInactiveSec = cfg.devices.${name}.interval;
        };
      }
    ) (lib.filterAttrs (_: device: device.autoApply) cfg.devices);
  };
}
