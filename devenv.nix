{ pkgs, ... }:

{
  packages = with pkgs; [
    bandit
    docker-client
    gitleaks
    jq
    semgrep
    sqlite
    tree-sitter
    uv
  ];

  languages.python = {
    enable = true;
    package = pkgs.python312;
    uv.enable = true;
  };

  languages.javascript = {
    enable = true;
    package = pkgs.nodejs_22;
    pnpm.enable = true;
  };

  services.postgres = {
    enable = true;
    package = pkgs.postgresql_16;
    createDatabase = false;
    initialDatabases = [
      {
        name = "enten";
        user = "enten";
        pass = "enten";
      }
    ];
    extensions = extensions: [ extensions.pgvector ];
    initialScript = "CREATE EXTENSION IF NOT EXISTS vector;";
  };

  scripts.up.exec = "scripts/dev-up.sh";
  scripts.test.exec = "make test";
  scripts.eval.exec = "make eval";

  enterTest = ''
    python --version
    node --version
    pnpm --version
    uv --version
    postgres --version
    tree-sitter --version
    semgrep --version
    gitleaks version
  '';
}
