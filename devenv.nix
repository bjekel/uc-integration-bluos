{ pkgs, lib, config, ... }:

{
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
  ];

  # Environment variables
  env = {
    UC_CONFIG_HOME = "${config.env.DEVENV_ROOT}/data";
    UC_LOG_LEVEL = "DEBUG";
    PYTHONPATH = "${config.env.DEVENV_ROOT}/intg-bluos:${config.env.DEVENV_ROOT}";
  };

  # Common scripts
  scripts = {
    lint.exec = ''
      pylint intg-bluos/
      black --check intg-bluos/ tests/
      isort --check-only intg-bluos/ tests/
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
  };

  # Pre-commit hooks
  pre-commit.hooks = {
    black.enable = true;
    isort.enable = true;
  };
}
