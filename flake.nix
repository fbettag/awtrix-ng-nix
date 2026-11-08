{
  description = "Declarative AWTRIX NG management for NixOS";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forSystems (system: {
        default = nixpkgs.legacyPackages.${system}.python3Packages.callPackage ./package.nix { };
      });
      apps = forSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/awtrix-ng-nix";
        };
      });
      formatter = forSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);
      checks = forSystems (system: {
        package = self.packages.${system}.default;
        module =
          let
            evaluated = nixpkgs.lib.nixosSystem {
              system = "x86_64-linux";
              modules = [
                self.nixosModules.default
                {
                  services.awtrix-ng = {
                    enable = true;
                    devices.desk = {
                      host = "192.0.2.42";
                      uid = "001122334455";
                      credentialsFile = "/run/secrets/clock";
                    };
                  };
                }
              ];
            };
          in
          assert
            evaluated.config.systemd.services.awtrix-ng-desk.serviceConfig.LoadCredential
            == [ "auth:/run/secrets/clock" ];
          assert evaluated.config.systemd.timers.awtrix-ng-desk.timerConfig.OnUnitInactiveSec == "5min";
          nixpkgs.legacyPackages.${system}.runCommand "awtrix-ng-module" { } "touch $out";
      });
      nixosModules.default = import ./modules/nixos.nix;
    };
}
