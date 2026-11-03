---
name: nix-blueprint
description: "numtide/blueprint — Nix flake framework that maps a fixed directory structure to flake outputs with zero glue code. Apply when a repo's flake.nix calls `inputs.blueprint { ... }`; when deciding where a package, devshell, host, module, check or template file must live; when a package/devshell/host is missing from `nix flake show` or `nix build` says the attribute doesn't exist; when adding the `prefix` option; when writing packages/<name>/default.nix, devshell.nix or modules/<class>/<name>.nix. Do NOT use for generic Nix flakes without blueprint (hand-written outputs, flake-parts, flake-utils), for nixpkgs contributions, or for NixOS module semantics unrelated to file placement. Prefer nix-project-scaffold for creating a NEW project, nix-treefmt-formatter for formatter.nix internals, and nixos-host-provisioning for adding hosts with disko/facter."
user-invocable: true
license: MIT
compatibility: Designed for Claude Code, Codex or similar harness, and for projects using Nix flakes with numtide/blueprint.
metadata:
  author: aldoborrero
  version: "1.0.0"
  openclaw:
    emoji: "🧭"
    homepage: https://github.com/aldoborrero/cc-skills-nix
    requires:
      bins:
        - nix
        - git
    install: []
allowed-tools: Read Edit Write Glob Grep Bash(nix:*) Bash(git:*) Agent
paths:
  - "**/flake.nix"
  - "**/*.nix"
---

# numtide/blueprint

Blueprint generates all flake outputs from the directory layout. The structure IS the API: there is no place to "register" an output — you create a file at the right path and the output exists. Source of truth for every mapping below: `lib/default.nix` in numtide/blueprint.

A blueprint flake.nix is ~10 lines and rarely changes:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs?ref=nixos-unstable";
    blueprint.url = "github:numtide/blueprint";
    blueprint.inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs = inputs: inputs.blueprint {
    inherit inputs;
    prefix = "nix";                        # optional: load tree from ./nix/ instead of repo root
    # systems = [ "x86_64-linux" ];        # default: x86_64-linux, aarch64-linux, x86_64-darwin, aarch64-darwin
    # nixpkgs.config.allowUnfree = true;   # forces a fresh nixpkgs import per system
    # nixpkgs.overlays = [ ... ];
  };
}
```

With `prefix = "nix"` every path below lives under `nix/` (e.g. `nix/packages/foo/default.nix`); without it, at the repo root. Convention observed in real repos: application/library projects use `prefix = "nix"` to keep Nix out of the way; NixOS/system config repos skip the prefix since the whole repo is Nix.

## Directory → output map (condensed)

| Path (under prefix) | Flake output |
|---|---|
| `devshell.nix` | `devShells.<sys>.default` |
| `devshell.toml` | `devShells.<sys>.default` (needs `devshell` input) |
| `devshells/<name>.nix` or `devshells/<name>/default.nix` | `devShells.<sys>.<name>` |
| `devshells/<name>.toml` | `devShells.<sys>.<name>` (needs `devshell` input) |
| `package.nix` | `packages.<sys>.default` |
| `packages/<name>.nix` or `packages/<name>/default.nix` | `packages.<sys>.<name>` + `checks.<sys>.pkgs-<name>` |
| `formatter.nix` | `formatter.<sys>` (also registered as `packages.<sys>.formatter`) |
| `checks/<name>.nix` or `checks/<name>/default.nix` | `checks.<sys>.<name>` (overrides auto-generated ones) |
| `lib/default.nix` | `lib` (and `lib.tests` runs under `checks.<sys>.lib-tests` via nix-unit) |
| `hosts/<host>/configuration.nix` | `nixosConfigurations.<host>` + `checks.<sys>.nixos-<host>` |
| `hosts/<host>/darwin-configuration.nix` | `darwinConfigurations.<host>` (needs `nix-darwin` input) |
| `hosts/<host>/system-configuration.nix` | `systemConfigs.<host>` (needs `system-manager` input) |
| `hosts/<host>/rpi-configuration.nix` | `nixosConfigurations.<host>` (needs `nixos-raspberrypi` input) |
| `hosts/<host>/default.nix` | escape hatch; returns `{ class, value }`, takes precedence over the others |
| `hosts/<host>/users/<user>.nix` or `<user>/home-configuration.nix` | home-manager user on that host + standalone `legacyPackages.<sys>.homeConfigurations."<user>@<host>"` (needs `home-manager` input) |
| `modules/nixos/<name>(.nix)` | `nixosModules.<name>` (same for `darwin`→`darwinModules`, `home`→`homeModules`; any other `<class>` under `modules.<class>.<name>`) |
| `templates/<name>/` | `templates.<name>` |

No `formatter.nix` → formatter defaults to `pkgs.nixfmt-tree`. Full detail (arguments, precedence rules, auto-check naming, home-manager defaults): `references/directory-map.md`.

## Arguments files receive

Per-system files (`packages/`, `devshells/`, `checks/`, `devshell.nix`, `formatter.nix`, `package.nix`) are loaded with `callPackage` over a scope containing exactly: `inputs`, `flake` (= `inputs.self`), `pkgs`, `system`, `perSystem`, `pname`. Declare only what you use — asking for anything else (e.g. `stdenv`, `lib`, `self`) fails with `Function called without required argument`. Take `stdenv` etc. through `pkgs.`, or write a nixpkgs-style file and load it with `pkgs.callPackage` (see the two-file pattern below).

`perSystem.<input>.<pkg>` is shorthand for `inputs.<input>.packages.<sys>.<pkg>` (with `legacyPackages` merged in). `perSystem.self.<pkg>` refers to this flake's own packages — the way one package or devshell consumes another.

Host and module files are NixOS/darwin/home-manager modules with extra specialArgs `inputs`, `flake`, `hostName`, plus `perSystem` as a module arg. `self` was renamed: using `self` in these throws "self was renamed to flake".

## The two-file package pattern (real-world convention)

`packages/<name>/default.nix` is a one-line shim bridging blueprint's scope to nixpkgs' `callPackage`; `package.nix` next to it is a normal nixpkgs-style derivation (so `stdenv`, `lib`, `buildGoModule`, etc. inject normally and the file is upstreamable):

```nix
# packages/cfleet/default.nix
{ flake, pkgs, ... }:
pkgs.callPackage ./package.nix { src = flake; }
```

Ecosystem variants swap the scope: `pkgs.python313Packages.callPackage ./package.nix { src = flake; }`. Cross-package deps pass through the shim: `pkgs.callPackage ./package.nix { other = perSystem.self.other; }`.

More patterns (treefmt formatter with working format check, devshell with `perSystem.self.formatter`, host escape hatch wiring disko/nixos-hardware/sops, shared modules imported as `flake.modules.nixos.*`): `references/patterns.md`.

## Inspecting what blueprint produced

```bash
nix flake show                                              # full output tree
nix eval .#packages.x86_64-linux --apply builtins.attrNames # names only, no building
nix eval .#checks.x86_64-linux --apply builtins.attrNames
nix eval .#nixosConfigurations --apply builtins.attrNames
nix build .#<name> / nix develop .#<name> / nix fmt / nix flake check
```

## Failure modes — check these BEFORE debugging anything else

1. **New file not `git add`ed → output silently missing.** In a git repo, flake evaluation only sees tracked files. A brand-new `packages/foo/` simply doesn't appear in `nix flake show` — no error, no warning. `git add` the files (staging is enough) and re-run. This is the #1 blueprint time-sink.
2. **`meta.platforms` silently filters packages** out of `packages.<sys>` and `checks.<sys>` for systems not listed.
3. **`<name>.nix` beats `<name>/`** when both exist in the same folder — the directory is silently ignored.
4. **`devshells/<name>/` without `default.nix` is silently skipped**; likewise `users/<user>/` without `home-configuration.nix`, non-`.nix` files in `packages/`/`modules/<class>/`, and loose `.nix` files directly under `hosts/`.
5. **A `hosts/<name>/` dir with no recognized configuration file throws** `host '<name>' does not have a configuration` and breaks evaluation of all host outputs.
6. **Checks collect `passthru.tests`, not `meta.tests`.** A package's tests appear as `checks.<sys>.pkgs-<name>-<test>` only from `passthru.tests`; putting them under `meta.tests` silently registers nothing.
7. **Optional features need explicit inputs** — TOML devshells → `devshell`; `hosts/*/users/` → `home-manager`; darwin/system-manager/rpi hosts → their inputs. Missing ones throw a clear message naming the input to add.
8. **`pkgs/` is the old name** — emits `trace: blueprint: the /pkgs folder is now /packages` and is otherwise ignored.

Details, exact error messages and diagnosis commands: `references/failure-modes.md`.
