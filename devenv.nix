{ pkgs, lib, config, ... }:

{
  cachix.enable = false;

  # Base Python configuration
  languages.python = {
    enable = true;
    version = "3.11";
    venv = {
      enable = true;
      requirements = ./requirements.txt;
    };
  };

  # Common packages for all profiles
  packages = with pkgs; [
    git
    gnumake
    jq
  ];

  # Environment variables
  env = {
    UC_CONFIG_HOME = "${config.env.DEVENV_ROOT}/data";
    UC_LOG_LEVEL = "DEBUG";
    PYTHONPATH = "${config.env.DEVENV_ROOT}/intg-bluos:${config.env.DEVENV_ROOT}";
  };

  # Common scripts
  scripts = {
    lint-check.exec = ''
      pylint intg-bluos/
      black --check intg-bluos/ tests/
      isort --check-only intg-bluos/ tests/
    '';

    lint-fix.exec = ''
      pylint intg-bluos/
      black intg-bluos/ tests/
      isort intg-bluos/ tests/
    '';

    format.exec = ''
      black intg-bluos/ tests/
      isort intg-bluos/ tests/
    '';

    test.exec = ''
      python -m pytest tests/ -v
    '';

    run.exec = ''
      python intg-bluos/driver.py
    '';

    build.exec = ''
      echo "Building BluOS integration..."
      pyinstaller --clean --onedir \
        --name intg-bluos \
        --add-data "driver.json:." \
        intg-bluos/driver.py
      echo "Build complete: dist/intg-bluos/"
    '';

    package.exec = ''
      echo "Building and packaging..."
      build
      VERSION=$(jq -r '.version' driver.json)
      ARCH=$(uname -m)
      cd dist/intg-bluos
      cp ../../driver.json ../../LICENSE .
      echo "$VERSION" > version.txt
      tar -czf "../uc-intg-bluos-$VERSION-$ARCH.tar.gz" .
      echo "Package created: dist/uc-intg-bluos-$VERSION-$ARCH.tar.gz"
    '';

    clean.exec = ''
      rm -rf dist/ build/ *.spec
      echo "Build artifacts cleaned"
    '';
  };

  # Pre-commit hooks
  pre-commit.hooks = {
    black.enable = true;
    isort.enable = true;
  };
}
