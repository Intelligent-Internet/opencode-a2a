#!/usr/bin/env bash
# Docs: scripts/init_system_readme.md
# Initialize host prerequisites for OpenCode + A2A (idempotent).
set -euo pipefail

OPENCODE_CORE_DIR="/opt/.opencode"
SHARED_WRAPPER_DIR="/opt/opencode-a2a"
OPENCODE_A2A_DIR="${SHARED_WRAPPER_DIR}/opencode-a2a-server"
UV_PYTHON_DIR="/opt/uv-python"
UV_PYTHON_DIR_MODE="770"
UV_PYTHON_DIR_FINAL_MODE="750"
UV_PYTHON_DIR_GROUP="opencode"
UV_PYTHON_INSTALL_DIR="$UV_PYTHON_DIR"
DATA_ROOT="/data/opencode-a2a"
OPENCODE_A2A_REPO="https://github.com/Intelligent-Internet/opencode-a2a-server.git"
OPENCODE_A2A_BRANCH="main"
OPENCODE_INSTALLER_URL="https://opencode.ai/install"
OPENCODE_INSTALLER_VERSION="1.2.5"
OPENCODE_INSTALLER_SHA256="fc3c1b2123f49b6df545a7622e5127d21cd794b15134fc3b66e1ca49f7fb297e"
OPENCODE_INSTALL_CMD="--version ${OPENCODE_INSTALLER_VERSION}"

# Feature toggles.
INSTALL_PACKAGES="true"
INSTALL_UV="true"
INSTALL_GH="true"
INSTALL_NODE="true"

# Node.js configuration.
NODE_MAJOR="20"

DEFAULT_PACKAGES=(
  htop
  vim
  curl
  wget
  git
  net-tools
  ca-certificates
  util-linux
)

UV_PYTHON_VERSIONS=(
  3.10
  3.11
  3.12
  3.13
)

for arg in "$@"; do
  if [[ -n "$arg" ]]; then
    echo "Unknown argument: $arg" >&2
    exit 1
  fi
done

log_start() {
  echo "[init] Start: $*"
}

log_done() {
  echo "[init] Done: $*"
}

warn() {
  echo "[init] WARN: $*" >&2
}

die() {
  echo "[init] ERROR: $*" >&2
  exit 1
}

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    die "sudo not found; run as root or install sudo."
  fi
  SUDO="sudo"
  log_start "Checking sudo access..."
  sudo -v
  log_done "Sudo access ready."
fi

log_start "Detecting package manager..."
PKG_MANAGER=""
if command -v apt-get >/dev/null 2>&1; then
  PKG_MANAGER="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG_MANAGER="dnf"
elif command -v yum >/dev/null 2>&1; then
  PKG_MANAGER="yum"
elif command -v pacman >/dev/null 2>&1; then
  PKG_MANAGER="pacman"
fi
if [[ -n "$PKG_MANAGER" ]]; then
  log_done "Package manager detected: $PKG_MANAGER"
else
  log_done "Package manager not detected."
fi

install_packages() {
  local pkgs=("$@")
  if [[ "${#pkgs[@]}" -eq 0 ]]; then
    return 0
  fi
  if [[ -z "$PKG_MANAGER" ]]; then
    warn "No supported package manager found; install manually: ${pkgs[*]}"
    return 1
  fi
  local missing=()
  for pkg in "${pkgs[@]}"; do
    case "$PKG_MANAGER" in
      apt)
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
          missing+=("$pkg")
        fi
        ;;
      dnf|yum)
        if ! rpm -q "$pkg" >/dev/null 2>&1; then
          missing+=("$pkg")
        fi
        ;;
      pacman)
        if ! pacman -Qi "$pkg" >/dev/null 2>&1; then
          missing+=("$pkg")
        fi
        ;;
      *)
        missing+=("$pkg")
        ;;
    esac
  done
  if [[ "${#missing[@]}" -eq 0 ]]; then
    log_done "Packages already installed; skip."
    return 0
  fi
  log_start "Packages missing: ${missing[*]}"
  log_start "Installing packages: ${pkgs[*]}"
  case "$PKG_MANAGER" in
    apt)
      $SUDO apt-get update
      $SUDO apt-get install -y "${missing[@]}"
      ;;
    dnf)
      $SUDO dnf install -y "${missing[@]}"
      ;;
    yum)
      $SUDO yum install -y "${missing[@]}"
      ;;
    pacman)
      $SUDO pacman -Syu --noconfirm "${missing[@]}"
      ;;
    *)
      warn "Unsupported package manager: $PKG_MANAGER"
      return 1
      ;;
  esac
  log_done "Package installation completed."
}

download_script() {
  local url="$1"
  local dest="$2"
  if ! curl -fL "$url" -o "$dest"; then
    return 1
  fi
  if [[ ! -s "$dest" ]]; then
    return 1
  fi
  return 0
}

validate_script_contains() {
  local path="$1"
  local pattern="$2"
  if ! grep -Eqi "$pattern" "$path"; then
    return 1
  fi
  return 0
}

verify_file_checksum() {
  local path="$1"
  local expected="$2"
  local actual

  if [[ ! -s "$path" ]]; then
    return 1
  fi
  if [[ -z "$expected" ]]; then
    warn "OPENCODE_INSTALLER_SHA256 is empty; refusing to run remote installer."
    return 1
  fi
  actual="$(sha256sum "$path" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    warn "Checksum mismatch for $path"
    warn "  expected: $expected"
    warn "  actual:   $actual"
    return 1
  fi
  return 0
}

ensure_group() {
  local group="$1"
  if [[ -z "$group" ]]; then
    return 0
  fi
  if getent group "$group" >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v groupadd >/dev/null 2>&1; then
    warn "groupadd not found; cannot create uv python group '${group}'."
    return 1
  fi
  $SUDO groupadd --system "$group"
}

ensure_gh() {
  if command -v gh >/dev/null 2>&1; then
    log_done "gh already installed; skip."
    return 0
  fi
  if [[ -z "$PKG_MANAGER" ]]; then
    warn "No package manager; cannot install gh."
    return 1
  fi
  log_start "Installing gh (GitHub CLI)..."
  case "$PKG_MANAGER" in
    apt)
      $SUDO install -d -m 755 /etc/apt/keyrings
      if [[ ! -f /etc/apt/keyrings/githubcli-archive-keyring.gpg ]]; then
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
          $SUDO dd of=/etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
        $SUDO chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
      fi
      if [[ ! -f /etc/apt/sources.list.d/github-cli.list ]]; then
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | \
          $SUDO tee /etc/apt/sources.list.d/github-cli.list >/dev/null
      fi
      $SUDO apt-get update
      $SUDO apt-get install -y gh
      ;;
    dnf)
      $SUDO dnf install -y 'dnf-command(config-manager)'
      $SUDO dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
      $SUDO dnf install -y gh
      ;;
    yum)
      $SUDO yum install -y yum-utils
      $SUDO yum-config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
      $SUDO yum install -y gh
      ;;
    pacman)
      $SUDO pacman -Syu --noconfirm github-cli
      ;;
    *)
      warn "Unsupported package manager for gh install: $PKG_MANAGER"
      return 1
      ;;
  esac
  if command -v gh >/dev/null 2>&1; then
    log_done "gh installation completed."
    return 0
  fi
  warn "gh installation failed."
  return 1
}

ensure_dir() {
  local path="$1"
  local mode="$2"
  if [[ -e "$path" && ! -d "$path" ]]; then
    die "$path exists but is not a directory."
  fi
  if [[ -d "$path" ]]; then
    log_done "Directory exists; skip: $path"
    return 0
  fi
  log_start "Creating directory: $path"
  $SUDO install -d -m "$mode" "$path"
  log_done "Directory created: $path"
}

log_start "System initialization."
INCOMPLETE=0

log_start "Checking and installing base packages..."
if is_truthy "$INSTALL_PACKAGES"; then
  packages=("${DEFAULT_PACKAGES[@]}")
  if [[ "$PKG_MANAGER" == "apt" ]]; then
    packages+=("gnupg")
  fi
  if [[ "${#packages[@]}" -gt 0 ]]; then
    if ! install_packages "${packages[@]}"; then
      INCOMPLETE=1
    fi
  else
    log_done "Required packages already installed; skip."
  fi
  if is_truthy "$INSTALL_GH"; then
    if ! ensure_gh; then
      INCOMPLETE=1
    fi
  fi
else
  if ! command -v git >/dev/null 2>&1; then
    warn "git not found and INSTALL_PACKAGES=false."
    INCOMPLETE=1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found and INSTALL_PACKAGES=false."
    INCOMPLETE=1
  fi
fi
log_done "Base package check completed."

log_start "Checking Node.js installation..."
if is_truthy "$INSTALL_NODE"; then
  node_version=""
  node_major=""
  if command -v node >/dev/null 2>&1; then
    node_version="$(node -v 2>/dev/null || true)"
    if [[ "$node_version" =~ ^v([0-9]+) ]]; then
      node_major="${BASH_REMATCH[1]}"
    fi
  fi
  if [[ -n "$node_major" && "$node_major" -ge "$NODE_MAJOR" ]]; then
    log_done "Node.js already installed: ${node_version}"
  else
    log_start "Installing Node.js ${NODE_MAJOR}.x..."
    case "$PKG_MANAGER" in
      apt)
        nodesource_script="$(mktemp)"
        log_start "Downloading NodeSource setup script..."
        if ! download_script "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" "$nodesource_script"; then
          warn "Failed to download NodeSource setup script."
          INCOMPLETE=1
        else
          log_done "NodeSource setup script downloaded."
          log_start "Validating NodeSource setup script..."
          if ! validate_script_contains "$nodesource_script" "nodesource|NodeSource"; then
            warn "NodeSource setup script validation failed."
            INCOMPLETE=1
          else
            log_done "NodeSource setup script validated."
            $SUDO -E bash "$nodesource_script"
          fi
        fi
        rm -f "$nodesource_script"
        $SUDO apt-get install -y nodejs
        ;;
      dnf)
        nodesource_script="$(mktemp)"
        log_start "Downloading NodeSource setup script..."
        if ! download_script "https://rpm.nodesource.com/setup_${NODE_MAJOR}.x" "$nodesource_script"; then
          warn "Failed to download NodeSource setup script."
          INCOMPLETE=1
        else
          log_done "NodeSource setup script downloaded."
          log_start "Validating NodeSource setup script..."
          if ! validate_script_contains "$nodesource_script" "nodesource|NodeSource"; then
            warn "NodeSource setup script validation failed."
            INCOMPLETE=1
          else
            log_done "NodeSource setup script validated."
            $SUDO -E bash "$nodesource_script"
          fi
        fi
        rm -f "$nodesource_script"
        $SUDO dnf install -y nodejs
        ;;
      yum)
        nodesource_script="$(mktemp)"
        log_start "Downloading NodeSource setup script..."
        if ! download_script "https://rpm.nodesource.com/setup_${NODE_MAJOR}.x" "$nodesource_script"; then
          warn "Failed to download NodeSource setup script."
          INCOMPLETE=1
        else
          log_done "NodeSource setup script downloaded."
          log_start "Validating NodeSource setup script..."
          if ! validate_script_contains "$nodesource_script" "nodesource|NodeSource"; then
            warn "NodeSource setup script validation failed."
            INCOMPLETE=1
          else
            log_done "NodeSource setup script validated."
            $SUDO -E bash "$nodesource_script"
          fi
        fi
        rm -f "$nodesource_script"
        $SUDO yum install -y nodejs
        ;;
      pacman)
        $SUDO pacman -Syu --noconfirm nodejs npm
        ;;
      *)
        warn "Unsupported package manager for Node.js install: $PKG_MANAGER"
        INCOMPLETE=1
        ;;
    esac
    if command -v node >/dev/null 2>&1; then
      node_version="$(node -v 2>/dev/null || true)"
      log_done "Node.js installed: ${node_version}"
    else
      warn "Node.js installation failed."
      INCOMPLETE=1
    fi
  fi
  if ! command -v npm >/dev/null 2>&1; then
    warn "npm not found after Node.js installation."
    INCOMPLETE=1
  fi
  if ! command -v npx >/dev/null 2>&1; then
    warn "npx not found after Node.js installation."
    INCOMPLETE=1
  fi
else
  if ! command -v node >/dev/null 2>&1; then
    warn "Node.js not found and INSTALL_NODE=false."
    INCOMPLETE=1
  fi
fi
log_done "Node.js check completed."

log_start "Checking systemd availability..."
if ! command -v systemctl >/dev/null 2>&1; then
  die "systemctl not found; systemd is required."
else
  log_done "systemd detected."
fi
log_done "Systemd check completed."

log_start "Ensuring shared directories exist..."
if ! ensure_group "$UV_PYTHON_DIR_GROUP"; then
  INCOMPLETE=1
fi
ensure_dir "$OPENCODE_CORE_DIR" "755"
ensure_dir "$SHARED_WRAPPER_DIR" "755"
ensure_dir "$UV_PYTHON_DIR" "$UV_PYTHON_DIR_MODE"
if [[ -n "$UV_PYTHON_DIR_GROUP" ]]; then
  if getent group "$UV_PYTHON_DIR_GROUP" >/dev/null 2>&1; then
    $SUDO chgrp "$UV_PYTHON_DIR_GROUP" "$UV_PYTHON_DIR"
  else
    warn "UV_PYTHON_DIR_GROUP not found; keep default ownership for $UV_PYTHON_DIR."
  fi
fi
$SUDO chmod "$UV_PYTHON_DIR_MODE" "$UV_PYTHON_DIR"
ensure_dir "$DATA_ROOT" "711"
log_done "Directory setup completed."

log_start "Checking uv installation..."
if is_truthy "$INSTALL_UV"; then
  if command -v uv >/dev/null 2>&1; then
    log_done "uv already installed; skip."
  else
    if ! command -v curl >/dev/null 2>&1; then
      warn "curl not available; cannot install uv."
      INCOMPLETE=1
    else
      log_start "Installing uv..."
      uv_script="$(mktemp)"
      log_start "Downloading uv install script..."
      if ! download_script "https://astral.sh/uv/install.sh" "$uv_script"; then
        warn "Failed to download uv install script."
        INCOMPLETE=1
      else
        log_done "uv install script downloaded."
        log_start "Validating uv install script..."
        if ! validate_script_contains "$uv_script" "astral|uv"; then
          warn "uv install script validation failed."
          INCOMPLETE=1
        else
          log_done "uv install script validated."
          UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" sh "$uv_script"
        fi
      fi
      rm -f "$uv_script"
      if ! command -v uv >/dev/null 2>&1; then
        if [[ -x "$HOME/.local/bin/uv" ]]; then
          log_start "Moving uv into /usr/local/bin."
          $SUDO install -d -m 755 /usr/local/bin
          if [[ -e /usr/local/bin/uv ]]; then
            log_done "uv already exists in /usr/local/bin; skip move."
          else
            $SUDO mv "$HOME/.local/bin/uv" /usr/local/bin/uv
            $SUDO chmod 755 /usr/local/bin/uv
            log_done "uv moved into /usr/local/bin."
          fi
        fi
      fi
      if ! command -v uv >/dev/null 2>&1; then
        warn "uv installation failed or not in PATH."
        INCOMPLETE=1
      else
        log_done "uv installation completed."
      fi
    fi
  fi
else
  if ! command -v uv >/dev/null 2>&1; then
    warn "uv not found and INSTALL_UV=false."
    INCOMPLETE=1
  fi
fi
log_done "uv installation check completed."

log_start "Ensuring uv Python versions are installed..."
if command -v uv >/dev/null 2>&1; then
  installed_versions=""
  if installed_versions="$(UV_PYTHON_DIR="$UV_PYTHON_DIR" \
    UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
    uv python list 2>/dev/null)"; then
    :
  else
    installed_versions=""
  fi
  missing_versions=()
  for version in "${UV_PYTHON_VERSIONS[@]}"; do
    pattern="(^|[^0-9])${version//./\\.}(\.|[[:space:]]|$)"
    if [[ -n "$installed_versions" ]] && echo "$installed_versions" | grep -Eq "$pattern"; then
      log_done "uv python ${version} already installed; skip."
    else
      missing_versions+=("$version")
    fi
  done
  if [[ "${#missing_versions[@]}" -gt 0 ]]; then
    if ! UV_PYTHON_DIR="$UV_PYTHON_DIR" \
      UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
      uv python install "${missing_versions[@]}"; then
      warn "uv python install failed."
      INCOMPLETE=1
    else
      log_done "uv Python versions installed: ${missing_versions[*]}"
    fi
  else
    log_done "All uv Python versions already installed; skip."
  fi
  log_start "Setting uv python directory permissions..."
  if [[ -n "$UV_PYTHON_DIR_GROUP" ]]; then
    if getent group "$UV_PYTHON_DIR_GROUP" >/dev/null 2>&1; then
      $SUDO chgrp -R "$UV_PYTHON_DIR_GROUP" "$UV_PYTHON_DIR"
    else
      warn "UV_PYTHON_DIR_GROUP not found; skip recursive chgrp for $UV_PYTHON_DIR."
    fi
  fi
  $SUDO chmod -R "$UV_PYTHON_DIR_FINAL_MODE" "$UV_PYTHON_DIR"
  log_done "uv python directory permissions set."
else
  warn "uv not available; skipping uv python install."
  INCOMPLETE=1
fi
log_done "uv Python version check completed."

log_start "Checking repository state..."
if [[ -d "$OPENCODE_A2A_DIR/.git" ]]; then
  log_done "Repo exists; skip clone: $OPENCODE_A2A_DIR"
else
  if [[ -d "$OPENCODE_A2A_DIR" && -n "$(ls -A "$OPENCODE_A2A_DIR" 2>/dev/null)" ]]; then
    warn "Directory not empty and not a git repo; skip clone: $OPENCODE_A2A_DIR"
    INCOMPLETE=1
  else
    if ! command -v git >/dev/null 2>&1; then
      warn "git not available; cannot clone repo."
      INCOMPLETE=1
    else
      log_start "Cloning repo to $OPENCODE_A2A_DIR"
      if ! $SUDO git clone "$OPENCODE_A2A_REPO" "$OPENCODE_A2A_DIR"; then
        warn "git clone failed. Ensure SSH key is configured or manually clone into $OPENCODE_A2A_DIR."
        INCOMPLETE=1
      else
        if [[ -n "$OPENCODE_A2A_BRANCH" ]]; then
          $SUDO git -C "$OPENCODE_A2A_DIR" checkout "$OPENCODE_A2A_BRANCH"
        fi
        log_done "Repo cloned to $OPENCODE_A2A_DIR"
      fi
    fi
  fi
fi
log_done "Repository check completed."

log_start "Checking A2A virtual environment..."
if [[ -x "${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a-server" ]]; then
  log_done "A2A venv already initialized; skip."
else
  if ! command -v uv >/dev/null 2>&1; then
    warn "uv not available; cannot create A2A venv."
    INCOMPLETE=1
  elif [[ ! -f "${OPENCODE_A2A_DIR}/pyproject.toml" ]]; then
    warn "pyproject.toml not found in ${OPENCODE_A2A_DIR}; cannot create venv."
    INCOMPLETE=1
  else
    log_start "Creating A2A venv with uv sync..."
    (
      cd "$OPENCODE_A2A_DIR"
      UV_PYTHON_DIR="$UV_PYTHON_DIR" \
        UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
        uv sync --all-extras
    )
    log_done "A2A venv created."
  fi
fi
log_done "A2A virtual environment check completed."

log_start "Checking OpenCode installation..."
OPENCODE_BIN="${OPENCODE_CORE_DIR}/bin/opencode"
if [[ -x "$OPENCODE_BIN" ]]; then
  log_done "opencode found at $OPENCODE_BIN"
elif command -v opencode >/dev/null 2>&1; then
  log_done "opencode found in PATH."
else
  if [[ -n "$OPENCODE_INSTALL_CMD" ]]; then
    opencode_install_script="$(mktemp)"
    log_start "Downloading OpenCode installer..."
    if ! download_script "$OPENCODE_INSTALLER_URL" "$opencode_install_script"; then
      warn "Failed to download OpenCode installer."
      INCOMPLETE=1
    elif ! verify_file_checksum "$opencode_install_script" "$OPENCODE_INSTALLER_SHA256"; then
      warn "OpenCode installer checksum validation failed."
      INCOMPLETE=1
    else
      log_done "OpenCode installer downloaded and verified."
      read -r -a opencode_install_args <<< "$OPENCODE_INSTALL_CMD"
      if [[ -n "$SUDO" ]]; then
        if ! $SUDO env OPENCODE_CORE_DIR="$OPENCODE_CORE_DIR" \
          bash "$opencode_install_script" "${opencode_install_args[@]}"; then
          warn "OPENCODE_INSTALL_CMD execution failed."
          INCOMPLETE=1
        fi
      else
        if ! OPENCODE_CORE_DIR="$OPENCODE_CORE_DIR" \
          bash "$opencode_install_script" "${opencode_install_args[@]}"; then
          warn "OPENCODE_INSTALL_CMD execution failed."
          INCOMPLETE=1
        fi
      fi
      if [[ "$INCOMPLETE" -eq 0 ]]; then
        log_done "OPENCODE_INSTALL_CMD completed."
      fi
    fi
    rm -f "$opencode_install_script"
    if [[ ! -d "$OPENCODE_CORE_DIR" && -d "/root/.opencode" ]]; then
      log_start "Relocating OpenCode from /root/.opencode to $OPENCODE_CORE_DIR..."
      $SUDO mv /root/.opencode "$OPENCODE_CORE_DIR"
      $SUDO ln -sf "${OPENCODE_CORE_DIR}/bin/opencode" /usr/local/bin/opencode
      log_done "OpenCode relocated and symlinked."
    elif [[ ! -d "$OPENCODE_CORE_DIR" && -n "${HOME:-}" && -d "${HOME}/.opencode" ]]; then
      log_start "Relocating OpenCode from ${HOME}/.opencode to $OPENCODE_CORE_DIR..."
      $SUDO mv "${HOME}/.opencode" "$OPENCODE_CORE_DIR"
      $SUDO ln -sf "${OPENCODE_CORE_DIR}/bin/opencode" /usr/local/bin/opencode
      log_done "OpenCode relocated and symlinked."
    fi
  else
    warn "opencode not found; set OPENCODE_INSTALL_CMD to install it."
    INCOMPLETE=1
  fi
  if [[ ! -x "$OPENCODE_BIN" ]] && ! command -v opencode >/dev/null 2>&1; then
    warn "opencode still missing after install command."
    INCOMPLETE=1
  fi
fi
log_done "OpenCode installation check completed."

if [[ "$INCOMPLETE" -ne 0 ]]; then
  warn "Initialization incomplete; review warnings above."
  exit 1
fi

if [[ "$SUDO" == "sudo" ]]; then
  log_start "Restoring /root permissions..."
  $SUDO chmod 700 /root
  log_done "Restored /root permissions."
fi

log_done "Initialization complete."
