#!/usr/bin/env bash
# Install the local WRF tools without pip downloads or third-party packages.
set -Eeuo pipefail

readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly VENV_DIR="${1:-$PROJECT_DIR/.venv}"

python3 -m venv "$VENV_DIR"
site_packages="$($VENV_DIR/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "$PROJECT_DIR/src" > "$site_packages/wrf_source_tools_local.pth"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -Eeuo pipefail' \
  'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"' \
  'exec "$SCRIPT_DIR/python" -m wrf_source_tools "$@"' \
  > "$VENV_DIR/bin/wrf-source"
chmod +x "$VENV_DIR/bin/wrf-source"

printf 'Installed without network access: %s\n' "$VENV_DIR"
printf 'Activate with: . %s/bin/activate\n' "$VENV_DIR"
printf 'Or run directly: %s/bin/wrf-source --help\n' "$VENV_DIR"
