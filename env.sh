#!/usr/bin/env bash

# RTL Hierarchy Documentor environment setup
# Source this file before running: source env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load Yosys/OSS-CAD-Suite environment
source "$SCRIPT_DIR/../../oss-cad-suite/environment"

# Activate scripts venv (has pyosys configured)
source "$SCRIPT_DIR/../scripts/.venv/bin/activate"

# Add src directory to PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

echo "RTL Hierarchy Documentor environment loaded."
echo "Usage: python -m rtl_hier_docor.cli -v generate -f <filelist> -t <top_module>"