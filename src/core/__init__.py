"""Core functionality modules."""

from .yosys_backend import YosysBackend
from .hierarchy_builder import HierarchyBuilder
from .source_extractor import SourceExtractor
from .config import load_config, load_project_config, get_remove_signals, ProjectConfig
from .signal_tracer import SignalTracer, trace_remove_signals

__all__ = [
    "YosysBackend",
    "HierarchyBuilder",
    "SourceExtractor",
    "load_config",
    "load_project_config",
    "get_remove_signals",
    "ProjectConfig",
    "SignalTracer",
    "trace_remove_signals",
]
