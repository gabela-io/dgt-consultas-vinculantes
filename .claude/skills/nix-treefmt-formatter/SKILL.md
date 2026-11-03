---
name: nix-treefmt-formatter
description: "treefmt-nix formatter for Nix flakes — `formatter.nix` via `treefmt-nix.lib.evalModule` (build.wrapper + build.check), lint→format ordering with priorities (deadnix→nixfmt, shellcheck→shfmt, ruff-check→ruff-format), and the format check wired into `nix flake check` via passthru.tests. Apply when writing or editing formatter.nix / treefmt config in a flake; when adding a language formatter (Go, Python, shell, YAML, TOML, Markdown, just); when `nix fmt` misbehaves, formats vendored files, or skips files (excludes, on-unmatched, default excludes); when CI passes `nix flake check` but formatting is actually broken (check never ran); when deadnix deletes wanted lambda args. Do NOT use for non-treefmt formatting setups, editor/IDE format-on-save config, or choosing code style rules of a language."
user-invocable: true
license: MIT
compatibility: Designed for Claude Code, Codex or similar harness, and for Nix flakes using treefmt-nix (with or without numtide/blueprint).
metadata:
  author: aldoborrero
  version: "1.1.0"
  openclaw:
    emoji: "🧹"
    homepage: https://github.com/aldoborrero/cc-skills-nix
    requires:
      bins:
        - nix
        - git
    install: []
allowed-tools: Read Edit Write Glob Grep Bash(nix:*) Bash(git:*) Agent
paths:
  - "**/formatter.nix"
  - "**/treefmt.nix"
  - "**/treefmt.toml"
  - "**/flake.nix"
---

# treefmt-nix formatter with a CI-enforced check

One `formatter.nix` gives you `nix fmt` (format everything) and a format check that makes `nix flake check` fail on unformatted code. Requires `inputs.treefmt-nix` (with `inputs.nixpkgs.follows = "nixpkgs"`).

In a blueprint repo, the file lives at `<prefix>/formatter.nix` and is auto-wired: it becomes the flake `formatter` output *and* the package `formatter`, whose `passthru.tests` land in `checks.<sys>.pkgs-formatter-*`. Without blueprint, export `build.wrapper` as `formatter.<sys>` and `build.check` under `checks` yourself.

## The template (verified end to end: red on a dirty tree, green after nix fmt)

```nix
{ flake, inputs, pkgs, ... }:
let
  mod = inputs.treefmt-nix.lib.evalModule pkgs {
    projectRootFile = "flake.nix";
    programs = {
      # nix — deadnix strips dead code, then nixfmt formats
      deadnix.enable = true;
      deadnix.no-lambda-pattern-names = true;   # don't break callPackage-style { pkg1, pkg2, ... } patterns
      deadnix.priority = 1;
      nixfmt.enable = true;
      nixfmt.priority = 2;
      # add per-language blocks here (see reference)
    };
  };
  wrapper = mod.config.build.wrapper;
in
wrapper // { passthru = wrapper.passthru // { tests.check = mod.config.build.check flake; }; }
```

`build.check` is treefmt-nix's own check derivation (function: source tree → package): it copies the tree, `git init && git add && git commit`, runs treefmt, and fails printing the diff if git detects changes. Hermetic, so one `nix flake check` covers builds, tests and formatting in a single CI job. Older repos hand-roll the same thing as a `pkgs.runCommand` copying `${flake}` — equivalent, just more code to maintain.

`treefmt-nix.lib.mkWrapper pkgs settings` is the wrapper-only shorthand; use `evalModule` when you also want `build.check` (or `build.configFile`) from one evaluation.

## THE bug to check first: `passthru.tests`, not `meta.tests`

Blueprint (and nixpkgs convention) collects package tests from **`passthru.tests`** only. A formatter ending in

```nix
formatter // { meta = formatter.meta // { tests = { check = check; }; }; }   # WRONG
```

evaluates fine, `nix flake check` stays green — and the check **never runs**. Symptom: `nix eval .#checks.<sys> --apply builtins.attrNames` lists `pkgs-formatter` but no `pkgs-formatter-check`. Fix is the last line of the template above. Verify after any edit:

```bash
nix eval .#checks.x86_64-linux --apply builtins.attrNames   # must contain "pkgs-formatter-check"
nix build .#checks.x86_64-linux.pkgs-formatter-check -L     # must actually run treefmt
```

## Rules that keep it working

- **`priority` orders lint before format.** treefmt runs formatters sharing files in ascending global priority, ties broken by name: `deadnix` (1) → `nixfmt` (2); `shellcheck` (1) → `shfmt` (2); `ruff-check` (1) → `ruff-format` (2). Every program module exposes `programs.<name>.priority` (also `includes`, `excludes`, `package`).
- **`settings.formatter.<name>.pipeline` does not exist.** Many real formatter.nix files carry `pipeline = "nix"` lines; neither treefmt v2 nor treefmt-nix defines that option — inert config, silently dropped. Ordering comes from priority alone.
- **Exclusions live at three levels**: `settings.excludes` for repo-wide globs (`settings.global.excludes` is the renamed old spelling — still works, warns); `programs.<name>.excludes` per formatter; and `enableDefaultExcludes` (default **true**) already excludes `*.lock`, `*.patch`, `package-lock.json`, `go.mod`/`go.sum`, `.gitignore`/`.gitattributes`, `LICENSE` — don't re-add those, and set it false if you actually want them formatted.
- **Vendored trees**: `settings.excludes = [ ".specify/**" ".agents/**" ".claude/**" ];` — otherwise every upstream refresh becomes a format conflict.
- **Shell tools need `includes` for non-`*.sh` files**: `[ "*.sh" "*.bash" "*.envrc" "*.envrc.*" ]` (add `"bin/*"` if you keep scripts there), on BOTH shellcheck and shfmt.
- **Files nothing formats are silent by default.** Set `settings.on-unmatched = "warn"` (levels debug…fatal) to see them; `"fatal"` turns uncovered files into a hard error.
- **What treefmt sees**: in a git repo, `nix fmt` walks `git ls-files --cached --others --exclude-standard` — tracked plus untracked non-ignored files. The check is stricter: its copy of `flake` contains only tracked files, so an untracked file can be formatted locally yet invisible to the check until `git add`.
- **`projectRootFile` defaults to `.git/config`** in treefmt-nix; blueprint-style repos set `"flake.nix"`. The wrapper finds the root by walking up, so `nix fmt` works from any subdirectory.
- The devshell should include the wrapper so `treefmt` is on PATH: `perSystem.self.formatter` (blueprint) or `build.wrapper`.
- Running bare `treefmt` in CI without nix? `treefmt --ci` = `--no-cache --fail-on-change` plus CI-suited settings. Stale cache locally: `treefmt --clear-cache`.

Per-language `programs` blocks (go, python, shell, yaml, json, toml, markdown, just, lua) and the non-blueprint wiring: `references/languages.md`.
