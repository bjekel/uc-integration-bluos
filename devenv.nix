{ pkgs, inputs, lib, config, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable { system = pkgs.stdenv.system; config.allowUnfree = true; };
in
{
  cachix.enable = false;

  # Base Python configuration
  languages.python = {
    enable = true;
    version = "3.11";
    venv = {
      enable = true;
      requirements = (builtins.readFile ./requirements.txt) + ''
        # Dev dependencies
        pylint
        pytest
        pytest-asyncio
        pyinstaller
      '';
    };
  };

  # Common packages for all profiles
  packages = with pkgs; [
    pkgs-unstable.claude-code
    git
    openssh
    gnumake
    jq
    docker
    docker-buildx
    qemu
    qemu-utils
    websocat
  ];

  # Environment variables
  env = {
    UC_CONFIG_HOME = "${config.env.DEVENV_ROOT}/data";
    UC_LOG_LEVEL = "DEBUG";
    PYTHONPATH = lib.mkForce "${config.env.DEVENV_ROOT}/intg-bluos:${config.env.DEVENV_ROOT}";
    FORCE_COLOR = "1";
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

    build.exec = ''
      echo "Building BluOS integration for host architecture..."
      pyinstaller --clean --onedir -y \
        --name driver \
        --paths intg-bluos \
        --add-data "driver.json:." \
        intg-bluos/driver.py
      echo "Build complete: dist/driver/"
    '';

    package.exec = ''
      echo "Building and packaging for host architecture..."
      build
      VERSION=$(jq -r '.version' driver.json)
      ARCH=$(uname -m)
      cd dist/driver
      mkdir -p bin
      mv driver _internal bin/
      cp ../../driver.json ../../LICENSE .
      echo "$VERSION" > version.txt
      tar -czf "../uc-intg-bluos-$VERSION-$ARCH.tar.gz" .
      echo "Package created: dist/uc-intg-bluos-$VERSION-$ARCH.tar.gz"
    '';

    build-aarch64.exec = ''
      echo "Building BluOS integration for aarch64 using Docker..."

      if ! docker run --rm --name builder \
        --platform=linux/arm64 \
        --user=$(id -u):$(id -g) \
        -v "$PWD":/workspace \
        -w /workspace \
        docker.io/unfoldedcircle/r2-pyinstaller:3.11.13 \
        bash -c 'PYTHON_VERSION=$(python --version | cut -d" " -f2 | cut -d. -f1,2) && \
        python -m pip install --user -r requirements.txt && \
        PYTHONPATH=~/.local/lib/python''${PYTHON_VERSION}/site-packages:$PYTHONPATH \
        pyinstaller --clean --onedir --name driver -y \
        --paths intg-bluos \
        --add-data driver.json:. \
        intg-bluos/driver.py'; then
        echo "Docker build failed!"
        exit 1
      fi
      echo "Build complete: dist/driver/"
    '';

    package-aarch64.exec = ''
      echo "Building and packaging for aarch64..."
      build-aarch64
      VERSION=$(jq -r '.version' driver.json)
      ARCH=aarch64
      cd dist/driver
      mkdir -p bin
      mv driver _internal bin/
      cp ../../driver.json ../../LICENSE .
      echo "$VERSION" > version.txt
      tar -czf "../uc-intg-bluos-$VERSION-$ARCH.tar.gz" .
      echo "Package created: dist/uc-intg-bluos-$VERSION-$ARCH.tar.gz"
    '';

    clean.exec = ''
      rm -rf dist/ build/ *.spec
      echo "Build artifacts cleaned"
    '';

    setup-qemu = {
      exec = ''
        if [ "$(uname -m)" = "x86_64" ]; then
          echo "Setting up QEMU for aarch64 emulation..."
          docker run --privileged --rm tonistiigi/binfmt --install arm64
          echo "✓ QEMU setup complete!"
        else
          echo "Running on aarch64, QEMU not needed"
        fi
      '';
      description = "Setup QEMU for aarch64 emulation (x86-64 only)";
    };

    register-integration = {
      exec = ''
        # Get values from driver.json
        PORT=$(jq -r '.port' driver.json)
        VERSION=$(jq -r '.version' driver.json)
        SETUP_SCHEMA=$(jq -c '.setup_data_schema' driver.json)

        # Get local IP address (first non-loopback IPv4)
        IP=$(hostname -I | awk '{print $1}')

        if [ -z "$IP" ]; then
          echo "Error: Could not determine local IP address"
          exit 1
        fi

        DRIVER_URL="ws://$IP:$PORT"
        echo "Registering integration with driver_url: $DRIVER_URL"

        curl --location "http://''${UC_REMOTE_HOST:-localhost:8080}/api/intg/drivers" \
          --user "web-configurator:''${UC_REMOTE_PIN:-1234}" \
          --header 'Content-Type: application/json' \
          --data "$(jq -n \
            --arg driver_url "$DRIVER_URL" \
            --arg version "$VERSION" \
            --argjson setup_schema "$SETUP_SCHEMA" \
            '{
              "name": {"en": "BluOS driver"},
              "driver_url": $driver_url,
              "version": $version,
              "icon": "uc:speaker",
              "enabled": true,
              "description": {"en": "Control BluOS-enabled streaming players"},
              "device_discovery": false,
              "setup_data_schema": $setup_schema,
              "release_date": "2026-01-26"
            }')"
      '';
      description = "Register integration with Unfolded Circle remote";
    };

    register-integration-remote = {
      exec = ''
        # Get values from driver.json
        PORT=$(jq -r '.port' driver.json)
        VERSION=$(jq -r '.version' driver.json)
        SETUP_SCHEMA=$(jq -c '.setup_data_schema' driver.json)

        # Get local IP address (first non-loopback IPv4)
        IP=$(hostname -I | awk '{print $1}')

        if [ -z "$IP" ]; then
          echo "Error: Could not determine local IP address"
          exit 1
        fi

        DRIVER_URL="ws://$IP:$PORT"
        echo "Registering integration with driver_url: $DRIVER_URL"

        REMOTE_HOST="''${UC_REMOTE_HOST:?set UC_REMOTE_HOST to your Remote's IP or hostname}"
        REMOTE_PIN="''${UC_REMOTE_PIN:?set UC_REMOTE_PIN to the web-configurator PIN}"

        curl --location "http://$REMOTE_HOST/api/intg/drivers" \
          --user "web-configurator:$REMOTE_PIN" \
          --header 'Content-Type: application/json' \
          --data "$(jq -n \
            --arg driver_url "$DRIVER_URL" \
            --arg version "$VERSION" \
            --argjson setup_schema "$SETUP_SCHEMA" \
            '{
              "name": {"en": "BluOS driver"},
              "driver_url": $driver_url,
              "version": $version,
              "icon": "uc:speaker",
              "enabled": true,
              "description": {"en": "Control BluOS-enabled streaming players"},
              "device_discovery": false,
              "setup_data_schema": $setup_schema,
              "release_date": "2026-01-26"
            }')"
      '';
      description = "Register integration with Unfolded Circle remote";
    };
  };

  # Git hooks
  git-hooks.hooks = {
    black.enable = true;
    isort.enable = true;
  };

  enterShell = ''
    echo "BluOS Integration — development environment"
    echo ""
    echo "Available commands:"
    echo "  run                          Run the integration locally"
    echo "  test                         Run the test suite"
    echo "  lint                         pylint + black + isort checks"
    echo "  format                       Auto-format (black + isort)"
    echo "  build                        PyInstaller build (host arch)"
    echo "  package                      build + tarball (host arch)"
    echo "  build-aarch64                PyInstaller build for ARM64 (Docker)"
    echo "  package-aarch64              build-aarch64 + tarball"
    echo "  clean                        Remove build artifacts"
    echo "  setup-qemu                   Install QEMU arm64 emulation (x86-64 only)"
    echo "  register-integration         Register driver with a local Remote/simulator"
    echo "  register-integration-remote  Register driver with a remote device (UC_REMOTE_HOST/PIN)"
    echo ""
  '';
}
