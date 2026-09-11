#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

py_version_ok() {
  "$1" -c '
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
' 2>/dev/null
}

# Prefer PYTHON env var, then modern minor versions, then generic python3.
PY_BIN=""
for cand in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.14 python3; do
  [[ -z "${cand}" ]] && continue
  if command -v "${cand}" >/dev/null 2>&1 && py_version_ok "${cand}"; then
    PY_BIN="$(command -v "${cand}")"
    break
  fi
done

if [[ -z "${PY_BIN}" ]]; then
  cat >&2 <<EOF
No Python >=3.11 found on PATH. Tried: PYTHON (env), python3.13, python3.12,
python3.11, python3.14, python3. Install one (e.g. \`brew install python@3.12\`)
or export PYTHON=/full/path/to/python and re-run.
EOF
  exit 1
fi

PY_VER="$("${PY_BIN}" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r "${SCRIPT_DIR}/requirements.txt"

echo "ok: python ${PY_VER}, venv at ${VENV_DIR}"
