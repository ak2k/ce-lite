{
  description = "ce-lite: lightweight-delegation converter for compound-engineering";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    treefmt-nix.url = "github:numtide/treefmt-nix";
    treefmt-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = inputs@{ flake-parts, treefmt-nix, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      perSystem = { pkgs, ... }:
        let
          pythonEnv = pkgs.python3.withPackages (ps: with ps; [ pyyaml pytest ]);
          # `nix fmt` runs every enabled formatter; `nix flake check` gates
          # via treefmt.config.build.check (added to `checks` below).
          # Excludes:
          #   - dist/**     generated; provenance comes from the converter
          #   - .claude/**  Claude Code worktree internals
          #   - converter/resources/ce-lite-persona  extensionless shebang
          #                 script that ships verbatim into dist/bin/. Other
          #                 .py files under converter/resources/ ARE linted.
          treefmt = treefmt-nix.lib.evalModule pkgs {
            projectRootFile = "flake.nix";
            programs.nixpkgs-fmt.enable = true;
            programs.ruff-format.enable = true;
            programs.ruff-check.enable = true;
            settings.global.excludes = [
              "dist/**"
              ".claude/**"
              "converter/resources/ce-lite-persona"
              "*.lock"
            ];
          };
        in
        {
          formatter = treefmt.config.build.wrapper;

          devShells.default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.actionlint
              pkgs.nixpkgs-fmt
              pkgs.ruff
              pkgs.statix
            ];
          };

          # `nix run .#integration-eval` — Tier 3 routing eval against
          # `claude -p` in the user's actual env. Quota-spending; not part
          # of `nix flake check`.
          apps.integration-eval = {
            type = "app";
            meta.description = "Run the ce-lite Tier 3 routing eval (spawns claude -p)";
            program = toString (pkgs.writeShellScript "ce-lite-integration-eval" ''
              export PATH=${pythonEnv}/bin:$PATH
              cd "$(${pkgs.git}/bin/git rev-parse --show-toplevel)"
              exec ${pythonEnv}/bin/python tests/integration/run_routing_eval.py "$@"
            '');
          };

          # `nix flake check` runs these
          checks = {
            treefmt = treefmt.config.build.check inputs.self;
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
              # `git` is required at runtime for `extract.lite_suffix_from_git`
                # tests, which spin up real ephemeral git repos to verify the
                # suffix-computation behaviour.
              pkgs.runCommand "check-tests" { buildInputs = [ pkgs.git ]; } ''
                export PYTHONDONTWRITEBYTECODE=1
                export HOME=$TMPDIR
                cd ${src}
                ${pythonEnv}/bin/pytest tests/ -v --no-header -p no:cacheprovider
                touch $out
              '';
          };
        };
    };
}
