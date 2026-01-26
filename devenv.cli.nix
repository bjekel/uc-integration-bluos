{ pkgs, lib, config, ... }:

{
  imports = [ ./devenv.nix ];

  # CLI-specific packages
  packages = with pkgs; [
    vim
    tmux
    jq
  ];

  enterShell = ''
    echo "BluOS Integration - CLI Development Environment"
    echo "Commands: run, test, lint, format"
    echo ""
  '';
}
