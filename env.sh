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

# Set CODE_BASE_PATH for C910 RTL filelist
export CODE_BASE_PATH="$SCRIPT_DIR/../C910_RTL_FACTORY"

echo "RTL Hierarchy Documentor environment loaded."
echo "  CODE_BASE_PATH=$CODE_BASE_PATH"
echo "Usage: python3 -m cli generate -f <filelist> -t <top_module> -o <output_dir>"
echo "Example: python3 -m cli generate -f \$CODE_BASE_PATH/gen_rtl/filelists/C910_asic_rtl.fl -t openC910 -o output/"