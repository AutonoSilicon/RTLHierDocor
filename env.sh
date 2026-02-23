#!/usr/bin/env bash

# RTL Hierarchy Documentor environment setup
# Source this file before running: source env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Step 1: Activate our Python 3.10 venv with pyosys
source "$SCRIPT_DIR/.venv/bin/activate"

# Step 2: Add OSS-CAD-Suite tools to PATH (放在后面，不覆盖我们的 Python)
export PATH="$PATH:$SCRIPT_DIR/oss-cad-suite/bin:$SCRIPT_DIR/oss-cad-suite/py3bin"
export LD_LIBRARY_PATH="$SCRIPT_DIR/oss-cad-suite/lib:${LD_LIBRARY_PATH}"

# Step 3: Add src directory to PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

# Step 4: Set CODE_BASE_PATH for C910 RTL filelist
export CODE_BASE_PATH="$SCRIPT_DIR/target/openc910/C910_RTL_FACTORY"

echo "RTL Hierarchy Documentor environment loaded."
echo "  CODE_BASE_PATH=$CODE_BASE_PATH"
echo "  Python3: $(which python3) ($(python3 --version))"
echo "  Yosys: $(which yosys)"
echo "Usage: python3 -m cli generate -f <filelist> -t <top_module> -o <output_dir>"
echo "Example: python3 -m cli generate -f \$CODE_BASE_PATH/gen_rtl/filelists/C910_asic_rtl.fl -t openC910 -o output/"