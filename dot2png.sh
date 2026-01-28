#!/bin/bash
# Convert DOT schematics to PNG images
# Usage: ./dot2png.sh <input.dot> [output.png]
#        ./dot2png.sh <directory>   # batch convert all .dot files

set -e

# Load environment (graphviz is in oss-cad-suite)
# SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# source "$SCRIPT_DIR/env.sh" 2>/dev/null || true

if [ $# -eq 0 ]; then
    echo "Usage: $0 <input.dot> [output.png]"
    echo "       $0 <directory>  # batch convert all .dot files"
    exit 1
fi

# Check graphviz installed
if ! command -v dot &> /dev/null; then
    echo "Error: graphviz not installed. Install with: sudo yum install graphviz"
    exit 1
fi

if [ -d "$1" ]; then
    # Batch mode: convert all .dot files in directory
    DIR="$1"
    COUNT=0
    for DOT_FILE in "$DIR"/*.dot; do
        [ -f "$DOT_FILE" ] || continue
        PNG_FILE="${DOT_FILE%.dot}.png"
        echo "Converting: $(basename "$DOT_FILE")"
        # Fix Yosys double-semicolon syntax error
        sed 's/;;/;/g' "$DOT_FILE" | dot -Tpng -o "$PNG_FILE"
        COUNT=$((COUNT + 1))
    done
    echo "Done. Converted $COUNT files."
else
    # Single file mode
    INPUT="$1"
    OUTPUT="${2:-${INPUT%.dot}.png}"
    
    if [ ! -f "$INPUT" ]; then
        echo "Error: File not found: $INPUT"
        exit 1
    fi
    
    echo "Converting: $INPUT -> $OUTPUT"
    # Fix Yosys double-semicolon syntax error
    sed 's/;;/;/g' "$INPUT" | dot -Tpng -o "$OUTPUT"
    echo "Done."
fi
