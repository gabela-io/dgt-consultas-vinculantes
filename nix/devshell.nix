{ pkgs, perSystem }:
perSystem.devshell.mkShell {
  packages = (
    with pkgs;
    [
      just
      perSystem.self.formatter

      # uv drives the local venv (pytest, running the scraper from source)
      python313
      uv
      # lint/format loop without going through treefmt
      ruff
    ]
  );

  env = [
    {
      name = "NIX_PATH";
      value = "nixpkgs=${toString pkgs.path}";
    }
    {
      name = "NIX_DIR";
      eval = "$PRJ_ROOT/nix";
    }
    {
      # Keep the toolchain cache in the project rather than $HOME.
      name = "UV_PROJECT_ENVIRONMENT";
      eval = "$PRJ_ROOT/.venv";
    }
  ];
}
