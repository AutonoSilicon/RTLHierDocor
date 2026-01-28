"""
RTL Hierarchy Documentor

A tool to build hierarchical index trees for RTL codebases using Yosys,
with source code location annotations.
"""

__version__ = "0.1.0"

from .models import HierarchyNode, SourceLocation, ModuleInfo, PortInfo
from .core import YosysBackend, HierarchyBuilder, SourceExtractor
from .schematic import SchematicGenerator

__all__ = [
    "HierarchyNode",
    "SourceLocation", 
    "ModuleInfo",
    "PortInfo",
    "YosysBackend",
    "HierarchyBuilder",
    "SourceExtractor",
    "SchematicGenerator",
]
