{ pkgs, lib, config, ... }:

# Desktop/IDE development environment. Imports the base (CLI) devenv.nix and
# layers IDE tooling on top. Use this profile when developing in an editor/IDE.

{
  imports = [ ./devenv.nix ];

  # IDE / language-server packages
  packages = with pkgs; [
    # Python language server for editors
    python311Packages.python-lsp-server
    python311Packages.pylsp-mypy
    python311Packages.python-lsp-black
    python311Packages.python-lsp-isort

    # Debugging
    python311Packages.debugpy

    # Type checking
    nodePackages.pyright

    # Linting
    python311Packages.pylint
    python311Packages.black
    python311Packages.isort
    python311Packages.mypy
  ];

  scripts.setup-vscode.exec = ''
    mkdir -p .vscode
    cat > .vscode/settings.json << 'EOF'
{
  "python.defaultInterpreterPath": ".devenv/state/venv/bin/python",
  "python.linting.pylintEnabled": true,
  "python.formatting.provider": "black",
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter"
  },
  "python.analysis.typeCheckingMode": "basic"
}
EOF
    echo "VSCode settings generated at .vscode/settings.json"
  '';

  enterShell = ''
    echo "Desktop/IDE extras loaded — run 'setup-vscode' to generate .vscode/settings.json"
    echo ""
  '';
}
