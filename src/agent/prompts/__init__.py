"""Prompt templates organized by processing pass.

This module provides centralized access to all prompt templates used by Docor Agent.
Prompts are organized into separate modules by pass/functionality for better maintainability.
"""

# Block-level prompts
from .block_level import BLOCK_SYSTEM, BLOCK_PROMPT

# Pass 1: Preview generation
from .pass1_preview import PASS1_SYSTEM, PASS1_PROMPT

# Pass 2: Documentation generation
from .pass2_docs import (
    PASS2_LEAF_SYSTEM,
    PASS2_LEAF_PROMPT,
    PASS2_NONLEAF_SYSTEM,
    PASS2_NONLEAF_PROMPT,
)

# Pass 2.1: Design highlights identification
from .pass2_1_highlights import PASS2_1_SYSTEM, PASS2_1_PROMPT

# Pass 2.2: Flowchart generation
from .pass2_2_flowchart import PASS2_2_MODULE_SYSTEM, PASS2_2_MODULE_PROMPT

# Pass 2.3: Interface specification
from .pass2_3_interface import PASS2_3_INTERFACE_SYSTEM, PASS2_3_INTERFACE_PROMPT

# Pass 2.4: Functional detailed description
from .pass2_4_functional import PASS2_4_FUNCTIONAL_SYSTEM, PASS2_4_FUNCTIONAL_PROMPT

# Pass 2.5: Register description
from .pass2_5_register import PASS2_5_REGISTER_SYSTEM, PASS2_5_REGISTER_PROMPT

__all__ = [
    # Block-level
    "BLOCK_SYSTEM",
    "BLOCK_PROMPT",
    # Pass 1
    "PASS1_SYSTEM",
    "PASS1_PROMPT",
    # Pass 2
    "PASS2_LEAF_SYSTEM",
    "PASS2_LEAF_PROMPT",
    "PASS2_NONLEAF_SYSTEM",
    "PASS2_NONLEAF_PROMPT",
    # Pass 2.1
    "PASS2_1_SYSTEM",
    "PASS2_1_PROMPT",
    # Pass 2.2
    "PASS2_2_MODULE_SYSTEM",
    "PASS2_2_MODULE_PROMPT",
    # Pass 2.3
    "PASS2_3_INTERFACE_SYSTEM",
    "PASS2_3_INTERFACE_PROMPT",
    # Pass 2.4
    "PASS2_4_FUNCTIONAL_SYSTEM",
    "PASS2_4_FUNCTIONAL_PROMPT",
    # Pass 2.5
    "PASS2_5_REGISTER_SYSTEM",
    "PASS2_5_REGISTER_PROMPT",
]
