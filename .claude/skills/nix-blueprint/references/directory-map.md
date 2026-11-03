# Directory → output map, in full

Everything here is taken from `lib/default.nix` of numtide/blueprint (the discovery logic) and verified by evaluation against real repos. "Under prefix" means relative to the `prefix` directory if one is configured, otherwise the flake root.

## How discovery works (`importDir`)

For every scanned folder, blueprint reads the directory entries and keeps:

- subdirectories, and
- files matching `*.nix` (name = basename without `.nix`).

Everything else (`.md`, `.lua`, `.json`, images…) is ignored, so non-Nix support files can live inside `modules/<class>/` or `packages/` without breaking anything. When `<name>.nix` and `<name>/` both exist, **the `.nix` file wins** and the directory is silently ignored.

## Configuration options (`inputs.blueprint { ... }`)

| Option | Default | Notes |
|---|---|---|
| `inputs` | required | Pass the flake's inputs. `nixpkgs` input required; blueprint's own inputs are used as fallback for missing ones. |
| `prefix` | `null` (flake root) | String (`"nix"`) or path. All scanned paths become `<prefix>/<path>`. |
| `systems` | `inputs.systems` if present, else blueprint's nix-systems default: `x86_64-linux`, `aarch64-linux`, `x86_64-darwin`, `aarch64-darwin` | A list of system strings, or a nix-systems–style flake input. |
| `nixpkgs.config` | `{ }` | If config and overlays are both empty, blueprint reuses `inputs.nixpkgs.legacyPackages.<sys>` (fast). If either is set, it does `import inputs.nixpkgs { inherit system; config; overlays; }` per system, and injects `nixpkgs.pkgs = lib.mkDefault <that pkgs>` into NixOS/darwin hosts so nixpkgs isn't evaluated twice. |
| `nixpkgs.overlays` | `[ ]` | Same mechanism. Upstream docs recommend `perSystem` composition over overlays where possible. |

## Per-system argument scope

Files instantiated once per system are loaded via `lib.callPackageWith` over a scope containing **exactly**:

- `inputs` — flake inputs
- `flake` — `inputs.self`
- `pkgs` — the nixpkgs instance described above
- `system` — current system string
- `perSystem` — for each input: `legacyPackages.<sys> // packages.<sys>`. `perSystem.self` = this flake's own packages (unfiltered by platform, to avoid infinite recursion)
- `pname` — the output name derived from the file/folder name

Because it is `callPackageWith` over this scope and nothing else, a file declaring `{ stdenv }:` or `{ lib }:` fails with `lib.customisation.callPackageWith: Function called without required argument "stdenv"`. Reach those through `pkgs.`, or bridge to nixpkgs-style with `pkgs.callPackage ./package.nix { }` (see patterns.md).

## devshells

| Path | Output |
|---|---|
| `devshell.nix` | `devShells.<sys>.default` |
| `devshell.toml` | `devShells.<sys>.default`, loaded with numtide/devshell (`inputs.devshell` required, throws naming it if missing) |
| `devshells/<name>.nix`, `devshells/<name>/default.nix` | `devShells.<sys>.<name>` |
| `devshells/<name>.toml` | `devShells.<sys>.<name>` via numtide/devshell |

The `.nix` files must evaluate to a shell derivation (any `mkShell`: nixpkgs' `pkgs.mkShell`, numtide devshell's `perSystem.devshell.mkShell`, …). They receive the per-system scope. TOML shells get `perSystem` wired into devshell's module args, so `perSystem.self.<pkg>` works inside devshell modules.

Merge precedence when names collide (later wins): `devshells/*.toml` < `devshells/*.nix` < `devshell.toml` < `devshell.nix`. A `devshells/<name>/` directory without `default.nix` is **silently ignored**.

Every devshell is also added to checks as `checks.<sys>.devshell-<name>`.

## packages

| Path | Output |
|---|---|
| `packages/<name>.nix`, `packages/<name>/default.nix` | `packages.<sys>.<name>` |
| `package.nix` (top level, for single-package repos) | `packages.<sys>.default` |
| `formatter.nix` | registered as the package `formatter`; the flake `formatter.<sys>` output is `perSystem.self.formatter or pkgs.nixfmt-tree` |

Files receive the per-system scope plus `pname`. Loaded through a `lib.makeScope`, so packages can refer to each other via `perSystem.self.<other>`.

Auto-generated checks per package:

- `checks.<sys>.pkgs-<name>` — the package itself
- `checks.<sys>.pkgs-<name>-<tname>` — one per entry in **`passthru.tests`** (NOT `meta.tests` — that attribute is silently ignored)

Packages whose `meta.platforms` is non-empty are filtered out of `packages.<sys>` and `checks.<sys>` for systems not in the list — silently. `pkgs/` is the legacy folder name; it triggers `trace: blueprint: the /pkgs folder is now /packages` and is not loaded.

Overlay export: blueprint exposes `mkPackagesFor`, which re-loads the `packages/` tree against a caller-supplied `pkgs` (for offering your packages as a nixpkgs overlay built with the consumer's nixpkgs):

```nix
outputs = inputs:
  let bp = inputs.blueprint { inherit inputs; };
  in bp // {
    overlays.default = final: _prev: { myproject = bp.mkPackagesFor final; };
  };
```

## checks

`checks/<name>.nix` or `checks/<name>/default.nix` → `checks.<sys>.<name>`.

Loaded with plain `import path args` (not callPackage), where args = the full per-system scope + `pname`; the function must therefore end its argument set with `...` (e.g. `{ pkgs, system, ... }:`). Checks defined here **take precedence** over the auto-generated `pkgs-*` / `devshell-*` / `nixos-*` names on collision.

Full auto-check inventory: `pkgs-<name>`, `pkgs-<name>-<tname>`, `devshell-<name>`, `nixos-<host>` (system closure, only on the host's own system), `darwin-<host>`, `system-<host>`, `lib-tests`.

## lib

`lib/default.nix` → the `lib` flake output. Imported with exactly `{ inputs, flake, self }` where `self` throws "self was renamed to flake" — so the function should be `{ inputs, flake, ... }:` or `{ flake, inputs }:`. If the result has a non-empty `tests` attribute, blueprint adds `checks.<sys>.lib-tests` running them with `nix-unit`.

## hosts

`hosts/<host>/` must be a **directory** (loose `.nix` files directly under `hosts/` are silently ignored). Inside, the first match wins:

1. `default.nix` — escape hatch. Called as `import path { inherit flake inputs hostName; }`; must return `{ class, value }` where `class` ∈ `nixos`, `nix-darwin`, `system-manager`, `robotnix`, `nix-on-droid` and `value` is the fully evaluated system (e.g. your own `nixosSystem` call, so you control modules and specialArgs completely).
2. `configuration.nix` — NixOS module → `nixosConfigurations.<host>` via `nixpkgs.lib.nixosSystem`. Must set `nixpkgs.hostPlatform`. Also `checks.<sys>.nixos-<host>`.
3. `rpi-configuration.nix` — → `nixosConfigurations.<host>` via nixos-raspberrypi (`inputs.nixos-raspberrypi` required; also passed as specialArg).
4. `darwin-configuration.nix` — → `darwinConfigurations.<host>` via nix-darwin (`inputs.nix-darwin` required). Also `checks.<sys>.darwin-<host>`.
5. `system-configuration.nix` — → `systemConfigs.<host>` via system-manager (`inputs.system-manager` required). Also `checks.<sys>.system-<host>`.
6. none of the above but the host has `users/` home configs → host is skipped without error (standalone home-manager only).
7. otherwise → **throws** `host '<host>' does not have a configuration`, breaking evaluation of host outputs.

Module-style host files (2–5) get specialArgs `inputs`, `flake`, `hostName` and module arg `perSystem` (resolved for the host's `nixpkgs.hostPlatform`). Extra files in the host dir (`disko.nix`, `facter.json`, hardware configs) are inert until imported explicitly.

## hosts/…/users (home-manager)

`hosts/<host>/users/<user>.nix` or `hosts/<host>/users/<user>/home-configuration.nix`. Requires `inputs.home-manager` (clear throw if missing).

- If the host has an OS configuration: the appropriate home-manager module is auto-imported and each user auto-registered under `home-manager.users.<user>`, with `useGlobalPkgs`/`useUserPackages` defaulting to true and specialArgs `inputs`/`flake` available.
- Always: a standalone config at `legacyPackages.<sys>.homeConfigurations."<user>@<host>"`, usable as `home-manager switch --flake .#<user>@<host>` (or bare `--flake .` when username+hostname match).
- `home.username` defaults to the folder-derived username; `home.homeDirectory` defaults to `/home/<user>` (Linux) / `/Users/<user>` (Darwin); both `mkDefault`, overridable.
- A `users/<user>/` directory without `home-configuration.nix` is silently omitted.

## modules

`modules/<class>/<name>.nix` or `modules/<class>/<name>/` (with `default.nix`) → `modules.<class>.<name>`. `<class>` is any folder name; three get aliased outputs: `nixos` → `nixosModules`, `darwin` → `darwinModules`, `home` → `homeModules`. Only subdirectories of `modules/` are scanned.

Publisher-args wrapping: if the file evaluates to a function whose arguments are all within `{ flake, inputs }`, blueprint calls it with those **before** exposing the module — letting a module close over the flake that defines it while normal module args (`config`, `lib`, `pkgs`, plus consumer `flake`/`inputs`/`perSystem`) refer to the consuming flake:

```nix
# modules/nixos/demo.nix — outer function is called at load time
{ flake }:
{ config, lib, ... }:
{ ... }
```

A plain module (`{ config, lib, ... }: { ... }`) is exposed as-is (its args are not a subset of `{flake, inputs}`).

## templates

`templates/<name>/` → `templates.<name>` with `path` and `description` (taken from the template's own `flake.nix` description, falling back to the name). Consumed via `nix flake init -t <flake>#<name>`.
