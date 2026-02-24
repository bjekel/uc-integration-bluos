{ pkgs, lib, config, ... }:

{
  imports = [ ./devenv.nix ];

  # CLI-specific packages
  packages = with pkgs; [
    jq
    python311Packages.pyinstaller
  ];

  enterShell = ''
    echo "BluOS Integration - CLI Development Environment"
    echo "Commands: run, test, lint, format, build, package, clean"
    echo ""
  '';
}
