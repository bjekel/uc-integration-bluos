{ pkgs, lib, config, ... }:

{
  cachix.enable = false;

  # Base Python configuration
  languages.python = {
    enable = true;
    version = "3.11";
    venv = {
      enable = true;
      requirements = ''
        # Runtime dependencies
        ucapi==0.5.1
        pyblu>=0.7.0
        pyee~=13.0.0
        zeroconf>=0.80.0
        aiohttp>=3.9.0
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
    claude-code
    git
    gnumake
    jq
    docker
    docker-buildx
    qemu
    qemu-utils
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

    build.exec = ''
      echo "Building BluOS integration for host architecture..."
      pyinstaller --clean --onedir \
        --name driver \
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
      echo "Note: Requires Docker with QEMU emulation. On first run, execute:"
      echo "  sudo apt install qemu-system-arm binfmt-support qemu-user-static"
      echo "  docker run --rm --privileged multiarch/qemu-user-static --reset -p yes"
      echo ""
      docker run --rm --name builder \
        --platform=linux/arm64 \
        --user=$(id -u):$(id -g) \
        -v "$PWD":/workspace \
        -w /workspace \
        docker.io/unfoldedcircle/r2-pyinstaller:3.11.13 \
        bash -c 'PYTHON_VERSION=$(python --version | cut -d" " -f2 | cut -d. -f1,2) && \
        python -m pip install --user -r requirements.txt && \
        PYTHONPATH=~/.local/lib/python''${PYTHON_VERSION}/site-packages:$PYTHONPATH \
        pyinstaller --clean --onedir --name driver -y \
        --add-data driver.json:. \
        intg-bluos/driver.py'
      echo "Build complete: dist/driver/"
    '';

    package-aarch64.exec = ''
      echo "Building and packaging for aarch64..."
      build-aarch64
      VERSION=$(jq -r '.version' driver.json)
      ARCH=arm64
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
          echo "Setting up Qemu for aarch64 emulation..."
          docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
          echo "✓ Qemu setup complete!"
        else
          echo "Running on aarch64, Qemu not needed"
        fi
      '';
      description = "Setup Qemu for aarch64 emulation (x86-64 only)";
    };
  };

  # Git hooks
  git-hooks.hooks = {
    black.enable = true;
    isort.enable = true;
  };
}
