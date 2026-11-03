{
  lib,
  src,
  buildPythonApplication,
  uv-build,
  # runtime deps
  beautifulsoup4,
  click,
  mcp,
  python-frontmatter,
  pyyaml,
  requests,
  snowballstemmer,
  # test deps
  pytestCheckHook,
}:
buildPythonApplication {
  pname = "dgt-consultas";
  version = "0.1.0";
  pyproject = true;

  inherit src;

  build-system = [ uv-build ];

  dependencies = [
    beautifulsoup4
    click
    mcp
    python-frontmatter
    pyyaml
    requests
    snowballstemmer
  ];

  nativeCheckInputs = [ pytestCheckHook ];

  pythonImportsCheck = [ "dgt_consultas" ];

  meta = {
    description = "Scraper for the DGT consultas vinculantes published on PETETE";
    homepage = "https://github.com/gabela-io/dgt-consultas-vinculantes";
    license = lib.licenses.mit;
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
    maintainers = with lib.maintainers; [ aldoborrero ];
    mainProgram = "dgt-consultas";
    platforms = lib.platforms.all;
  };
}
