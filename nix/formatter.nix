{ inputs, pkgs, ... }:
let
  fs = pkgs.lib.fileset;

  # The scraped corpus is generated output: formatting it would rewrite
  # 68k files, and the exporter output is pinned by tests/test_parser.py.
  corpus = [
    "consultas/**"
    "search_index/**"
    "metadata.json"
    "tests/fixtures/**"
    # vendored agent skills (npx skills); refreshed upstream, not ours to format
    ".claude/**"
    "skills-lock.json"
  ];

  mod = inputs.treefmt-nix.lib.evalModule pkgs {
    projectRootFile = "flake.nix";
    settings.excludes = corpus;
    programs = {
      # nix — deadnix strips dead code, then nixfmt formats
      deadnix.enable = true;
      deadnix.no-lambda-pattern-names = true;
      deadnix.priority = 1;
      nixfmt.enable = true;
      nixfmt.priority = 2;

      # shell
      shellcheck.enable = true;
      shellcheck.includes = [
        "*.sh"
        "*.bash"
        "*.envrc"
        "*.envrc.*"
      ];
      shellcheck.priority = 1;
      shfmt.enable = true;
      shfmt.includes = [
        "*.sh"
        "*.bash"
        "*.envrc"
        "*.envrc.*"
      ];
      shfmt.priority = 2;

      # python
      ruff-check.enable = true;
      ruff-check.priority = 1;
      ruff-format.enable = true;
      ruff-format.priority = 2;

      yamlfmt.enable = true;
      yamlfmt.settings.formatter = {
        type = "basic";
        indent = 2;
        retain_line_breaks = true;
      };
      jsonfmt.enable = true;
      taplo.enable = true;
      mdformat.enable = true;
      just.enable = true;
    };
  };

  # The check copies its source tree and git-commits it; leave the
  # ~700 MB corpus out so it stays fast. Excluded from formatting anyway.
  checkSrc = fs.toSource {
    root = ../.;
    fileset = fs.difference ../. (
      fs.unions [
        (fs.maybeMissing ../consultas)
        (fs.maybeMissing ../search_index)
        (fs.maybeMissing ../metadata.json)
      ]
    );
  };

  wrapper = mod.config.build.wrapper;
in
wrapper
// {
  passthru = wrapper.passthru // {
    tests.check = mod.config.build.check checkSrc;
  };
}
