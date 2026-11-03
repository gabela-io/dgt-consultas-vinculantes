# Per-language blocks and wiring variants

Blocks adapted from production `formatter.nix` files (Go, Python and NixOS-config repos); options verified against treefmt-nix's program modules. Merge what you need into the template's `programs`.

Every program module exposes `enable`, `package`, `includes`, `excludes`, `priority`; many add tool-specific options (see treefmt-nix's `programs/<name>.nix`). Ordering is ascending global priority, ties broken by name. `settings.formatter.<name>.pipeline` doesn't exist — don't copy it from older repos.

## Nix (always)

```nix
programs.deadnix = {
  enable = true;
  no-lambda-pattern-names = true;   # keep { pkg1, pkg2, ... }: callPackage patterns intact
  priority = 1;
};
programs.nixfmt = {
  enable = true;                    # RFC-style nixfmt
  priority = 2;
};
```

deadnix removes unused lambda args and let-bindings before nixfmt formats. Its escape hatches, all `programs.deadnix.*`: `no-lambda-pattern-names` (protects callPackage-style argument sets — blueprint repos want this), `no-lambda-arg`, `no-underscore`; for a single intentional unused binding, an `_` name prefix also works.

## Shell

```nix
programs.shellcheck = {
  enable = true;
  includes = [ "*.sh" "*.bash" "*.envrc" "*.envrc.*" ];  # add "bin/*" for script dirs
  priority = 1;
};
programs.shfmt = {
  enable = true;
  includes = [ "*.sh" "*.bash" "*.envrc" "*.envrc.*" ];
  priority = 2;
};
```

## Go

```nix
programs.gofmt.enable = true;
# stricter alternative used in some repos:
# programs.gofumpt.enable = true;
```

`go.mod`/`go.sum` are already excluded by `enableDefaultExcludes`.

## Python

```nix
programs.ruff-check = {
  enable = true;    # lint (with autofix) first
  priority = 1;
};
programs.ruff-format = {
  enable = true;    # then format
  priority = 2;
};
```

## YAML / JSON / TOML / Markdown / just / Lua

```nix
programs.yamlfmt.enable = true;
programs.yamlfmt.settings.formatter = {
  type = "basic";
  indent = 2;
  retain_line_breaks = true;
};
programs.jsonfmt.enable = true;
programs.taplo.enable = true;      # TOML
programs.mdformat.enable = true;   # Markdown
programs.just.enable = true;       # justfile
programs.stylua.enable = true;     # Lua
```

## Excludes

```nix
settings.excludes = [
  ".specify/**"   # vendored spec-kit scripts/templates
  ".agents/**"    # vendored agent skills
  ".claude/**"
];
```

Rationale: reformatting vendored trees turns every upstream refresh into a conflict, and shellcheck's opinion of somebody else's scripts is not a gate on your work. `settings.global.excludes` is the pre-rename spelling (works with a warning). Lock files, `go.mod`/`go.sum`, `.gitignore`, `LICENSE` etc. are covered by `enableDefaultExcludes = true` (the default) — no need to list them. Per-formatter: `programs.<name>.excludes`.

## Wiring without blueprint

```nix
# flake.nix (plain flake)
outputs = inputs@{ nixpkgs, treefmt-nix, ... }:
  let
    eachSystem = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (s: f nixpkgs.legacyPackages.${s});
    treefmt = pkgs: treefmt-nix.lib.evalModule pkgs ./treefmt.nix;
  in {
    formatter = eachSystem (pkgs: (treefmt pkgs).config.build.wrapper);
    checks = eachSystem (pkgs: {
      formatting = (treefmt pkgs).config.build.check inputs.self;
    });
  };
```

With blueprint none of this exists: `formatter.nix` at the prefix root is picked up automatically, `nix fmt` works, and `passthru.tests.check` appears as `checks.<sys>.pkgs-formatter-check`.

## Verification loop

```bash
nix fmt                                                      # format the repo in place
nix eval .#checks.x86_64-linux --apply builtins.attrNames    # pkgs-formatter-check present?
nix build .#checks.x86_64-linux.pkgs-formatter-check -L      # green on a clean tree,
                                                             # red with a diff on a dirty one
treefmt --clear-cache                                        # if results look stale
```

(Substitute your system for `x86_64-linux`.) The check failing prints the offending diff in the build log (`nix log <drv>` for the full output).
