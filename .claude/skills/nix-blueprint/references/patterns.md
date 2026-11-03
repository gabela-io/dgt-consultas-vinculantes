# Real-world patterns

Extracted from three production blueprint repos (a Go CLI, a Python service, and a NixOS+home-manager system config) plus blueprint's own repo. These are conventions the docs don't spell out.

## Project shapes

**App/library repo** (Go, Python, …): `prefix = "nix"`, tree is `nix/{devshell.nix, formatter.nix, packages/<name>/{default.nix,package.nix}}`. Inputs: nixpkgs + blueprint + treefmt-nix + devshell. The language toolchain lives in the devshell; the Nix build is a parallel consumer of the same sources.

**System config repo**: no prefix; `hosts/`, `modules/{nixos,home}/`, `packages/`, `devshell.nix`, `formatter.nix` at the root; `nixpkgs.config.allowUnfree = true` passed to blueprint. Many inputs (disko, sops-nix, home-manager, nixos-hardware, …).

## Two-file package: default.nix shim + nixpkgs-style package.nix

```nix
# nix/packages/cfleet/default.nix
{ flake, pkgs, ... }:
pkgs.callPackage ./package.nix { src = flake; }
```

```nix
# nix/packages/cfleet/package.nix — pure nixpkgs style, upstreamable
{ lib, src, buildGoModule }:
buildGoModule {
  pname = "cfleet";
  # ...
  inherit src;
  meta.platforms = lib.platforms.all;
}
```

Why: blueprint's scope only offers `inputs/flake/pkgs/system/perSystem/pname`, so a nixpkgs-style file can't be loaded directly. The shim converts; `package.nix` gets real dependency injection (overridable with `.override`), and `src = flake` uses the whole flake source as the package source.

Language-set variant: `pkgs.python313Packages.callPackage ./package.nix { src = flake; }` — then `package.nix` can request `buildPythonApplication`, `pytestCheckHook`, and library deps by name from that python scope.

Cross-package dependency goes through the shim:

```nix
# nix/packages/docker/default.nix — an OCI image wrapping the main package
{ pkgs, perSystem }:
pkgs.callPackage ./package.nix { reolink-exporter = perSystem.self.reolink-exporter; }
```

## formatter.nix with treefmt-nix and a CI check

Requires `inputs.treefmt-nix`. The formatter is a treefmt wrapper; a `format-check` derivation re-runs it over a pristine copy of the flake and fails on diff. Attach the check via `passthru.tests` so it lands in `checks.<sys>.pkgs-formatter-check` (see failure-modes.md #6 — `meta.tests` does NOT work):

```nix
{ flake, inputs, pkgs, ... }:
let
  formatter = inputs.treefmt-nix.lib.mkWrapper pkgs {
    projectRootFile = "flake.nix";
    programs = { nixfmt.enable = true; deadnix.enable = true; /* per-language: ruff, gofmt, shfmt, … */ };
  };
  check = pkgs.runCommand "format-check"
    { nativeBuildInputs = [ formatter pkgs.git ];
      meta.platforms = pkgs.lib.platforms.linux; }   # run the check only on Linux CI
    ''
      export HOME=$NIX_BUILD_TOP/home
      cp --no-preserve=mode --preserve=timestamps -r ${flake} source
      cd source && git init --quiet && git add .
      treefmt --no-cache
      git diff --exit-code
      touch $out
    '';
in
formatter // { passthru = formatter.passthru // { tests.check = check; }; }
```

`nix fmt` then runs treefmt over the repo.

## devshell.nix

With plain nixpkgs (`env` is an attrset, `shellHook` for aliases):

```nix
{ pkgs, perSystem }:
pkgs.mkShell {
  packages = with pkgs; [ just sops perSystem.self.formatter ];
  env.NIX_PATH = "nixpkgs=${toString pkgs.path}";
  shellHook = '' alias fmt='nix fmt' '';
}
```

With numtide/devshell (`env` is a list; needs `inputs.devshell`):

```nix
{ pkgs, perSystem }:
perSystem.devshell.mkShell {
  packages = with pkgs; [ just perSystem.self.formatter go gopls golangci-lint ];
  env = [
    { name = "NIX_PATH"; value = "nixpkgs=${toString pkgs.path}"; }
    { name = "GOPATH"; eval = "$PRJ_ROOT/.go"; }   # keep caches inside the project
  ];
}
```

Recurring details: `perSystem.self.formatter` in every shell so `treefmt`/the formatter is on PATH; `NIX_PATH` pinned to the flake's nixpkgs; language caches (`GOPATH`, `UV_PROJECT_ENVIRONMENT`) redirected under `$PRJ_ROOT`.

## Hosts: escape-hatch default.nix wiring input modules

`hosts/<host>/default.nix` when the host needs modules from inputs (disko, facter, nixos-hardware, sops-nix) that would otherwise have nowhere to be imported:

```nix
{ flake, inputs, hostName }:
{
  class = "nixos";
  value = inputs.nixpkgs.lib.nixosSystem {
    modules = (with inputs; [
      disko.nixosModules.disko
      nixos-facter-modules.nixosModules.facter
      nixos-hardware.nixosModules.framework-12th-gen-intel
      sops-nix.nixosModules.sops
    ]) ++ [ ./configuration.nix ./disko.nix ];
    specialArgs = { inherit flake inputs hostName; };
  };
}
```

Note: with the escape hatch **you** provide specialArgs and module wiring — blueprint's automatic `perSystem` injection and home-manager user auto-wiring apply to the `configuration.nix` path, not to a hand-rolled `nixosSystem` (re-add what you need yourself). `default.nix` takes precedence over a sibling `configuration.nix`, which then only exists because the escape hatch imports it.

## Thin hosts, shared modules

`configuration.nix` stays a list of imports from the flake's own module outputs, plus host-specific hardware settings:

```nix
{ flake, hostName, lib, ... }:
{
  imports = with flake.modules.nixos; [ audio core networking /* … */ ];
  networking.hostName = hostName;
  nixpkgs.hostPlatform = "x86_64-linux";
  system.stateVersion = "24.05";
}
```

Same shape for users: `hosts/<host>/users/<user>.nix` is `{ flake, ... }: { imports = with flake.modules.home; [ cli git dev ]; home.stateVersion = "24.05"; }`. The `modules/{nixos,home}/<topic>.nix` files hold all actual configuration, one topic per file, consumable from any host (and any downstream flake via `nixosModules`/`homeModules`).

## Consuming own packages elsewhere

- Inside a host/module: `perSystem.self.<pname>` (module arg).
- Inside another package or devshell: `perSystem.self.<pname>` (scope arg).
- From another flake: `inputs.<thisflake>.packages.<sys>.<pname>`, or shorthand `perSystem.<thisflake>.<pname>` if the consumer also uses blueprint.
