{
  description = "ce-lite: lightweight-delegation converter for compound-engineering";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs = inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      perSystem = { pkgs, ... }: {
        formatter = pkgs.nixpkgs-fmt;

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python3
            (python3.withPackages (ps: with ps; [ pyyaml ]))
            actionlint
            nixpkgs-fmt
            statix
          ];
        };

        # `nix flake check` runs these
        checks = {
          format = pkgs.runCommand "check-format" { } ''
            ${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt --check ${./.}
            touch $out
          '';
          actionlint = pkgs.runCommand "check-actions" { } ''
            ${pkgs.actionlint}/bin/actionlint \
              ${./.github/workflows/upstream-watch.yml} \
              ${./.github/workflows/publish-dist.yml}
            touch $out
          '';
        };
      };
    };
}
