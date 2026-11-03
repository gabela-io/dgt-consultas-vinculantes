# Failure modes

Ordered by time lost in practice. Every item below was reproduced by evaluation against blueprint (same discovery code as `lib/default.nix`); exact error strings are quoted verbatim.

## 1. New file invisible until `git add`

In a git repository, `nix flake show` / `nix build` evaluate the flake from the **tracked** file set. A newly created `packages/foo/default.nix` (or devshell, host, module…) that hasn't been `git add`ed produces no output, no error, no warning — to blueprint's directory scan the file doesn't exist. Staging is enough; no commit needed.

Diagnosis: `git status --short` — anything relevant marked `??` is invisible to the flake.

```bash
git add nix/packages/foo/   # then re-run nix flake show
```

This also bites when renaming (`git add -A` after `mv`) and when a `.gitignore` rule accidentally covers a nix file.

## 2. `meta.platforms` silently filters packages

A package with non-empty `meta.platforms` is dropped from `packages.<sys>` and `checks.<sys>` for every system not in the list. `nix build .#foo` then reports the attribute doesn't exist on your system, with no hint why. Packages with no `meta.platforms` are kept everywhere.

Diagnosis: check the derivation's `meta.platforms` against your system; the package still exists on the listed systems (`nix eval .#packages.aarch64-darwin --apply builtins.attrNames`).

Deliberate use: `meta.platforms = pkgs.lib.platforms.linux;` on a check-carrying derivation restricts it to Linux CI (seen in real formatter checks).

## 3. `<name>.nix` shadows `<name>/`

If `packages/hello.nix` and `packages/hello/default.nix` both exist, the `.nix` file wins and the directory is silently ignored (same rule in `devshells/`, `checks/`, `modules/<class>/`). Editing the directory version then has no effect — a classic "my change does nothing" trap.

## 4. Things skipped silently

- `devshells/<name>/` without a `default.nix` inside.
- `hosts/<host>/users/<user>/` without `home-configuration.nix` inside.
- Loose `.nix` files directly under `hosts/` (hosts must be directories).
- Non-`.nix` files anywhere in scanned folders (by design — support files are fine).
- Whole `pkgs/` folder (old name): emits `trace: blueprint: the /pkgs folder is now /packages` but loads nothing.

## 5. Things that throw, with their messages

- Host dir with no recognized file and no users: `error: host '<name>' does not have a configuration`. This breaks evaluation of **all** host-derived outputs, not just that host — an empty/WIP host directory (even one containing only unrecognized files) takes down `nix flake show`.
- Asking for an argument outside the per-system scope, e.g. `{ stdenv }:` in `packages/foo.nix`: `error: … 'lib.customisation.callPackageWith: Function called without required argument "stdenv" at <file>'`. Fix: take `pkgs` and use `pkgs.stdenv`, or bridge via `pkgs.callPackage ./package.nix { }`.
- `self` in a host/module/lib context: `self was renamed to flake`. Use `flake`.
- Missing optional inputs — each throws a message naming the exact input to add to flake.nix:
  - TOML devshells → `inputs.devshell.url = "github:numtide/devshell";`
  - `hosts/*/users/` → `inputs.home-manager.url = "github:nix-community/home-manager";`
  - `darwin-configuration.nix` → `inputs.nix-darwin.url = "github:Lnl7/nix-darwin";`
  - `system-configuration.nix` → `inputs.system-manager.url = "github:numtide/system-manager";`
  - `rpi-configuration.nix` → `inputs.nixos-raspberrypi.url = "github:nvmd/nixos-raspberrypi";`

## 6. `passthru.tests` vs `meta.tests`

Blueprint materializes per-package checks from **`passthru.tests`** only: `checks.<sys>.pkgs-<name>-<tname>`. Tests attached under `meta.tests` are silently not collected — `nix flake check` green-lights a formatter/package whose check never ran. Observed in the wild: a treefmt `formatter.nix` ending in

```nix
formatter // { meta = formatter.meta // { tests = { check = check; }; }; }   # WRONG: never runs
```

shows `pkgs-formatter` but no `pkgs-formatter-check` in `nix eval .#checks.<sys> --apply builtins.attrNames`. The working form (used by blueprint's own formatter.nix):

```nix
formatter // { passthru = formatter.passthru // { tests = { check = check; }; }; }
```

## 7. Name collisions across sources

- `checks/<name>.nix` overrides an auto-generated check of the same name (`pkgs-*`, `devshell-*`, `nixos-*`…) — intentional escape hatch, but also a way to accidentally mask a real check.
- devshell merge order (later wins): `devshells/*.toml` < `devshells/*.nix` < `devshell.toml` < `devshell.nix`. A `devshells/default.nix` is silently beaten by a top-level `devshell.nix`.
- `formatter.nix` occupies the package name `formatter`; a `packages/formatter.nix` would collide with it (last merge wins — avoid the name).

## Diagnosis toolkit

```bash
git status --short                                           # untracked = invisible (failure #1)
nix flake show                                               # what blueprint actually produced
nix eval .#packages.x86_64-linux --apply builtins.attrNames  # cheap, no builds
nix eval .#checks.x86_64-linux --apply builtins.attrNames    # did my passthru.tests register?
nix eval .#devShells.x86_64-linux --apply builtins.attrNames
nix eval .#nixosConfigurations --apply builtins.attrNames
nix build .#foo --show-trace                                 # full trace on scope/arg errors
```
