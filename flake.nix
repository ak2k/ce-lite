{
  description = "ce-lite: lightweight-delegation converter for compound-engineering";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs = inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      perSystem = { pkgs, ... }:
        let
          pythonEnv = pkgs.python3.withPackages (ps: with ps; [ pyyaml pytest ]);
        in
        {
          formatter = pkgs.nixpkgs-fmt;

          devShells.default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.actionlint
              pkgs.nixpkgs-fmt
              pkgs.statix
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
            tests =
              let
                src = pkgs.lib.fileset.toSource {
                  root = ./.;
                  fileset = pkgs.lib.fileset.unions [
                    ./converter
                    ./tests
                  ];
                };
              in
              pkgs.runCommand "check-tests" { } ''
                export PYTHONDONTWRITEBYTECODE=1
                cd ${src}
                ${pythonEnv}/bin/pytest tests/ -v --no-header -p no:cacheprovider
                touch $out
              '';
          };
        };
    };
}
