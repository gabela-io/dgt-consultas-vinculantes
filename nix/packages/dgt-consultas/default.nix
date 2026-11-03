{ pkgs, ... }:
let
  fs = pkgs.lib.fileset;
in
pkgs.python313Packages.callPackage ./package.nix {
  # Only the Python project: the corpus would put ~700 MB in the build
  # input and rebuild the package on every scrape.
  src = fs.toSource {
    root = ../../..;
    fileset = fs.unions [
      ../../../pyproject.toml
      ../../../src
      ../../../tests
    ];
  };
}
