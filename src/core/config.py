"""Configuration loading from YAML files.

Provides a ProjectConfig dataclass that unifies all CLI parameters.
Config values are loaded from config.yaml, then overridden by CLI args.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional


# ── Minimal YAML parser ──────────────────────────────────────────

def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Minimal YAML parser supporting one level of nesting.

    Supports:
      - Comments (# ...)
      - Top-level scalar values:   key: value
      - Top-level list values:     key:\\n  - item1\\n  - item2
      - One-level nested dicts:    parent:\\n  child_key: value
      - Nested lists:              parent:\\n  list_key:\\n    - item
      - Quoted and unquoted string values
      - Boolean: true/false
      - Integer values

    Does NOT support: multi-level nesting, flow syntax, anchors, etc.
    """
    result: Dict[str, Any] = {}
    current_section = None       # top-level key expecting nested content
    current_list_key = None      # key currently collecting list items
    current_list_section = None  # section of current list key

    for raw_line in text.split('\n'):
        # Determine indentation level
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())

        # Skip empty lines and comments
        if not stripped or stripped.startswith('#'):
            continue

        # List item: "- value" (may be at indent 2 or 4)
        if stripped.startswith('- '):
            value = _parse_scalar(stripped[2:].strip())
            if current_list_key and current_list_section:
                # Nested list (indent >= 4)
                result[current_list_section][current_list_key].append(value)
            elif current_list_key and current_list_section is None:
                # Top-level list
                result[current_list_key].append(value)
            continue

        # Key-value pair
        match = re.match(r'^([\w][\w_-]*)\s*:\s*(.*)?$', stripped)
        if not match:
            continue

        key = match.group(1)
        raw_value = (match.group(2) or '').strip()

        if indent == 0:
            # Top-level key
            if not raw_value:
                # Section header or top-level list
                current_section = key
                current_list_key = None
                current_list_section = None
                result[key] = {}
            else:
                current_section = None
                current_list_key = None
                current_list_section = None
                result[key] = _parse_scalar(raw_value)
        elif indent >= 2 and current_section:
            # Nested key under current_section
            if not raw_value:
                # Nested list
                result[current_section][key] = []
                current_list_key = key
                current_list_section = current_section
            else:
                current_list_key = None
                current_list_section = None
                result[current_section][key] = _parse_scalar(raw_value)

    # Fix: top-level keys that were initialized as {} but never got children
    # are actually meant to be empty lists (e.g. `remove_signals:` at top level)
    # We handle this by checking if the result is still an empty dict
    # with a list key context — but this is handled fine by the parser above.

    return result


def _parse_scalar(value: str) -> Any:
    """Parse a scalar value string into Python type."""
    # Strip inline comments first (but not inside quotes)
    # Handle: value # comment  and  "value" # comment
    if not value.startswith('"') and not value.startswith("'"):
        if ' #' in value:
            value = value[:value.index(' #')].strip()
    else:
        # For quoted values, find the closing quote then strip comments after
        quote_char = value[0]
        close_idx = value.find(quote_char, 1)
        if close_idx > 0:
            value = value[:close_idx + 1].strip()

    # Strip surrounding quotes
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return value[1:-1]

    # Boolean
    if value.lower() == 'true':
        return True
    if value.lower() == 'false':
        return False

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    return value


def _expand_env_vars(value: Any) -> Any:
    """Expand $ENV_VAR and ${ENV_VAR} in string values, recursively."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


# ── ProjectConfig dataclass ──────────────────────────────────────

@dataclass
class ProjectConfig:
    """Unified project configuration.

    All fields have sensible defaults. Fields can be populated from:
    1. config.yaml (lowest priority)
    2. CLI arguments (highest priority, override config.yaml)
    """

    # Design source
    filelist: Optional[str] = None
    rtlil: Optional[str] = None
    top_module: str = ""

    # Output
    output_dir: str = "./rtl_docs"
    cache_dir: str = ".rtl_cache"
    max_schematics: int = 0

    # Simplification
    simplify: bool = True
    strategy: str = "proc_group"
    remove_signals: List[str] = field(default_factory=list)

    # General
    verbose: bool = False

    # Agent settings
    agent_backend: str = "anthropic"
    agent_model: str = "claude-3-5-sonnet-20241022"
    agent_api_key: Optional[str] = None
    agent_base_url: Optional[str] = None
    agent_thinking: bool = False
    composer_backend: Optional[str] = None
    composer_model: Optional[str] = None
    composer_api_key: Optional[str] = None
    composer_base_url: Optional[str] = None
    composer_thinking: Optional[bool] = None
    skip_modules: List[str] = field(default_factory=lambda: ["ct_had*"])
    code_base_path: str = ""
    resume: bool = True
    max_modules: int = 0  # 0 for unlimited
    block_doc_threshold: int = 64  # Min lines for block-level LLM call
    pass3_enabled: bool = True  # Enable Pass 3 (chip-level overview & subsystem docs)
    pass3_3_enabled: bool = True  # Enable Pass 3.3 (instrack)
    isa_profile: str = "c910"  # Instruction profile: c910|rv64i|rv64gc
    isa_instructions: List[str] = field(default_factory=list)  # Explicit instruction list override
    instrack_single_instruction: Optional[str] = None  # Run only one instruction when set
    instrack_use_graph_markers: bool = False  # Phase-2 switch (kept for forward compatibility)
    max_concurrent_modules: int = 4  # Max concurrent LLM requests per pass

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProjectConfig':
        """Create ProjectConfig from a parsed config dict (config.yaml structure).

        Handles the nested YAML structure:
            design.filelist -> filelist
            design.top_module -> top_module
            output.dir -> output_dir
            simplify.enabled -> simplify
            etc.
        """
        config = cls()

        # design section
        design = data.get('design', {})
        if isinstance(design, dict):
            if 'filelist' in design:
                config.filelist = str(design['filelist'])
            if 'rtlil' in design:
                config.rtlil = str(design['rtlil'])
            if 'top_module' in design:
                config.top_module = str(design['top_module'])

        # output section
        output = data.get('output', {})
        if isinstance(output, dict):
            if 'dir' in output:
                config.output_dir = str(output['dir'])
            if 'cache_dir' in output:
                config.cache_dir = str(output['cache_dir'])
            if 'max_schematics' in output:
                config.max_schematics = int(output['max_schematics'])

        # simplify section
        simplify = data.get('simplify', {})
        if isinstance(simplify, dict):
            if 'enabled' in simplify:
                config.simplify = bool(simplify['enabled'])
            if 'strategy' in simplify:
                config.strategy = str(simplify['strategy'])
            if 'remove_signals' in simplify:
                signals = simplify['remove_signals']
                if isinstance(signals, list):
                    config.remove_signals = [str(s) for s in signals]

        # Backward compatibility: top-level remove_signals
        if 'remove_signals' in data and not config.remove_signals:
            signals = data['remove_signals']
            if isinstance(signals, list):
                config.remove_signals = [str(s) for s in signals]

        # General
        if 'verbose' in data:
            config.verbose = bool(data['verbose'])

        # agent section
        agent = data.get('agent', {})
        if isinstance(agent, dict):
            if 'backend' in agent:
                config.agent_backend = str(agent['backend'])
            if 'model' in agent:
                config.agent_model = str(agent['model'])
            if 'api_key' in agent:
                config.agent_api_key = str(agent['api_key'])
            if 'base_url' in agent:
                config.agent_base_url = str(agent['base_url'])
            if 'thinking' in agent:
                config.agent_thinking = bool(agent['thinking'])
            if 'composer_backend' in agent:
                config.composer_backend = str(agent['composer_backend'])
            if 'composer_model' in agent:
                config.composer_model = str(agent['composer_model'])
            if 'composer_api_key' in agent:
                config.composer_api_key = str(agent['composer_api_key'])
            if 'composer_base_url' in agent:
                config.composer_base_url = str(agent['composer_base_url'])
            if 'composer_thinking' in agent:
                config.composer_thinking = bool(agent['composer_thinking'])
            if 'skip_modules' in agent:
                skips = agent['skip_modules']
                if isinstance(skips, list):
                    config.skip_modules = [str(s) for s in skips]
            if 'code_base_path' in agent:
                config.code_base_path = str(agent['code_base_path'])
            if 'resume' in agent:
                config.resume = bool(agent['resume'])
            if 'max_modules' in agent:
                config.max_modules = int(agent['max_modules'])
            if 'block_doc_threshold' in agent:
                config.block_doc_threshold = int(agent['block_doc_threshold'])
            if 'pass3_enabled' in agent:
                config.pass3_enabled = bool(agent['pass3_enabled'])
            if 'pass3_3_enabled' in agent:
                config.pass3_3_enabled = bool(agent['pass3_3_enabled'])
            if 'isa_profile' in agent:
                config.isa_profile = str(agent['isa_profile'])
            if 'isa_instructions' in agent and isinstance(agent['isa_instructions'], list):
                config.isa_instructions = [str(i) for i in agent['isa_instructions'] if str(i).strip()]
            if 'instrack_single_instruction' in agent:
                text = str(agent['instrack_single_instruction']).strip()
                config.instrack_single_instruction = text or None
            if 'instrack_use_graph_markers' in agent:
                config.instrack_use_graph_markers = bool(agent['instrack_use_graph_markers'])
            if 'max_concurrent_modules' in agent:
                config.max_concurrent_modules = int(agent['max_concurrent_modules'])

        return config

    def override_from_args(self, args) -> 'ProjectConfig':
        """Override config fields with CLI argument values.

        Only overrides when the CLI arg was explicitly provided
        (not None / not default).

        Args:
            args: argparse.Namespace from CLI parsing

        Returns:
            self (for chaining)
        """
        if getattr(args, 'filelist', None) is not None:
            self.filelist = args.filelist
        if getattr(args, 'rtlil', None) is not None:
            self.rtlil = args.rtlil
        if getattr(args, 'top', None) is not None:
            self.top_module = args.top

        # Output - only override if different from argparse defaults
        if getattr(args, 'output', None) is not None:
            self.output_dir = args.output
        if getattr(args, 'cache_dir', None) is not None:
            self.cache_dir = args.cache_dir
        if getattr(args, 'max_schematics', None) is not None:
            self.max_schematics = args.max_schematics

        # Simplification
        if getattr(args, 'no_simplify', False):
            self.simplify = False
        if getattr(args, 'strategy', None) is not None:
            self.strategy = args.strategy

        # Verbose
        if getattr(args, 'verbose', False):
            self.verbose = True

        # Agent overrides
        if getattr(args, 'backend', None) is not None:
            self.agent_backend = args.backend
        if getattr(args, 'model', None) is not None:
            self.agent_model = args.model
        if getattr(args, 'base_url', None) is not None:
            self.agent_base_url = args.base_url
        if getattr(args, 'thinking', False):
            self.agent_thinking = True
        if getattr(args, 'no_resume', False):
            self.resume = False
        if getattr(args, 'max_modules', None) is not None:
            self.max_modules = args.max_modules

        return self

    def validate(self, command: str = "generate") -> List[str]:
        """Validate the configuration for a given command.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        if command in ("generate", "hierarchy", "schematic", "docor", "connectivity"):
            if not self.filelist and not self.rtlil:
                errors.append("Either 'filelist' or 'rtlil' must be specified "
                              "(via config.yaml design section or CLI -f/-r)")
            if not self.top_module:
                errors.append("'top_module' must be specified "
                              "(via config.yaml design.top_module or CLI -t)")

        if command == "docor":
            if self.agent_backend not in ("anthropic", "openai", "agent_sdk"):
                errors.append(f"Invalid agent backend '{self.agent_backend}', "
                              f"must be 'anthropic', 'openai' or 'agent_sdk'")
            
            if self.agent_backend == "anthropic" and not self.agent_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
                errors.append("ANTHROPIC_API_KEY must be provided in config or environment for 'anthropic' backend")
            
            if self.agent_backend == "openai" and not self.agent_api_key and not os.environ.get("OPENAI_API_KEY"):
                errors.append("OPENAI_API_KEY must be provided in config or environment for 'openai' backend")

        if self.strategy not in ("proc_group", "connected_component"):
            errors.append(f"Invalid strategy '{self.strategy}', "
                          f"must be 'proc_group' or 'connected_component'")

        return errors


# ── Public API ───────────────────────────────────────────────────

def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load raw configuration dict from a YAML file.

    Args:
        config_path: Path to config.yaml. If None, searches for config.yaml
            in the current directory and the project root.

    Returns:
        Configuration dictionary with env vars expanded. Empty dict if not found.
    """
    if config_path:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(path, 'r') as f:
            data = _parse_simple_yaml(f.read())
            return _expand_env_vars(data)

    # Search default locations
    search_paths = [
        Path.cwd() / "config.yaml",
        Path(__file__).parent.parent.parent / "config.yaml",  # project root
    ]

    for path in search_paths:
        if path.exists():
            with open(path, 'r') as f:
                data = _parse_simple_yaml(f.read())
                return _expand_env_vars(data)

    return {}


def load_project_config(config_path: Optional[str] = None) -> ProjectConfig:
    """Load ProjectConfig from a YAML file.

    Convenience wrapper: loads YAML -> parses -> returns ProjectConfig.

    Args:
        config_path: Path to config.yaml (auto-detected if None)

    Returns:
        ProjectConfig instance
    """
    data = load_config(config_path)
    return ProjectConfig.from_dict(data)


def get_remove_signals(config: Dict[str, Any]) -> List[str]:
    """Extract remove_signals list from config dict.

    Handles both nested (simplify.remove_signals) and flat (remove_signals).

    Args:
        config: Raw configuration dictionary

    Returns:
        List of signal name patterns to remove
    """
    # Try nested first
    simplify = config.get('simplify', {})
    if isinstance(simplify, dict) and 'remove_signals' in simplify:
        signals = simplify['remove_signals']
        if isinstance(signals, list):
            return [str(s) for s in signals]

    # Fall back to flat
    signals = config.get("remove_signals", [])
    if isinstance(signals, list):
        return [str(s) for s in signals]
    return []
