"""Tracks documentation progress with generic pass support."""

import json
import os
import hashlib
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List
from datetime import datetime


@dataclass
class ModuleDocState:
    """State of documentation for a single module."""
    module_name: str
    status: str = "pending"

    # Pass results - stored as content hash for change detection
    # The actual content is stored in module output files
    pass1_overview_hash: Optional[str] = None
    pass1_5_block_docs_hash: Optional[str] = None
    pass2_1_highlights_hash: Optional[str] = None
    pass2_2_mermaid_hash: Optional[str] = None
    pass2_3_interface_hash: Optional[str] = None
    pass2_4_functional_hash: Optional[str] = None
    pass2_5_register_hash: Optional[str] = None
    pass2_6_timing_cdc_hash: Optional[str] = None
    pass2_7_architecture_hash: Optional[str] = None
    pass2_a_root_hash: Optional[str] = None
    pass2_b_expand_hash: Optional[str] = None
    pass2_c_polish_hash: Optional[str] = None
    pass2_description_hash: Optional[str] = None

    error: Optional[str] = None
    timestamp: Optional[str] = None

    # Track arbitrary pass results for extensibility
    extra: Dict[str, str] = field(default_factory=dict)


class ProgressTracker:
    """Tracks documentation progress with resume capability."""
    
    # Define pass progression order
    LINEAR_PASSES = ["pass1", "pass1_5", "pass2"]
    PARALLEL_PASSES = [
        "pass2_1", "pass2_2", "pass2_3", "pass2_4", "pass2_5", "pass2_6", "pass2_7",
        "pass2_a_root", "pass2_b_expand", "pass2_c_polish"
    ]

    # Map pass names to status values and field names
    PASS_STATUS_MAP = {
        "pass1": "pass1_done",
        "pass1_5": "pass1_5_done",
        "pass2_1": "pass2_1_done",
        "pass2_2": "pass2_2_done",
        "pass2_3": "pass2_3_done",
        "pass2_4": "pass2_4_done",
        "pass2_5": "pass2_5_done",
        "pass2_6": "pass2_6_done",
        "pass2_7": "pass2_7_done",
        "pass2": "pass2_done",
    }

    PASS_FIELD_MAP = {
        "pass1": "pass1_overview_hash",
        "pass1_5": "pass1_5_block_docs_hash",
        "pass2_1": "pass2_1_highlights_hash",
        "pass2_2": "pass2_2_mermaid_hash",
        "pass2_3": "pass2_3_interface_hash",
        "pass2_4": "pass2_4_functional_hash",
        "pass2_5": "pass2_5_register_hash",
        "pass2_6": "pass2_6_timing_cdc_hash",
        "pass2_7": "pass2_7_architecture_hash",
        "pass2_a_root": "pass2_a_root_hash",
        "pass2_b_expand": "pass2_b_expand_hash",
        "pass2_c_polish": "pass2_c_polish_hash",
        "pass2": "pass2_description_hash",
    }

    # Map pass names to output file names
    PASS_FILE_MAP = {
        "pass1": "preview.md",
        "pass1_5": "block_docs.md",
        "pass2_1": "design_highlights.md",
        "pass2_2": "flowchart.mmd",
        "pass2_3": "interface_spec.md",
        "pass2_4": "functional_desc.md",
        "pass2_5": "register_desc.md",
        "pass2_6": "timing_cdc.md",
        "pass2_7": "architecture.md",
        "pass2_a_root": "description_root.md",
        "pass2_b_expand": "description_working.md",
        "pass2_c_polish": "description_polished.md",
        "pass2": "description.md",
    }

    DEBUG_STAGE_PASSES = {"pass2_a_root", "pass2_b_expand", "pass2_c_polish"}

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.progress_file = os.path.join(output_dir, ".progress.json")
        self.states: Dict[str, ModuleDocState] = {}
        self._load()

    def _load(self):
        """Load progress from disk."""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for name, state_dict in data.items():
                    # Handle extra fields
                    extra = state_dict.pop('extra', {})
                    state = ModuleDocState(**state_dict)
                    state.extra = extra
                    self.states[name] = state
            except Exception as e:
                print(f"[WARN] Failed to load progress: {e}")

    def save(self):
        """Save progress to disk."""
        os.makedirs(self.output_dir, exist_ok=True)
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump({n: asdict(s) for n, s in self.states.items()}, 
                         f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] Failed to save progress: {e}")

    def get_state(self, module_name: str) -> ModuleDocState:
        """Get or create state for a module."""
        if module_name not in self.states:
            self.states[module_name] = ModuleDocState(module_name=module_name)
        return self.states[module_name]

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute MD5 hash of content."""
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def update(self, module_name: str, pass_name: str, result: str):
        """Generic update method for any pass.

        Stores the hash of the result content. The actual content should be
        saved to the corresponding output file separately.
        """
        state = self.get_state(module_name)

        # Compute and store the hash
        content_hash = self._compute_hash(result)
        field_name = self.PASS_FIELD_MAP.get(pass_name)
        if field_name:
            setattr(state, field_name, content_hash)
        else:
            # Store in extra for unknown passes
            state.extra[f"{pass_name}_hash"] = content_hash

        # Update status
        status = self.PASS_STATUS_MAP.get(pass_name)
        if status:
            state.status = status

        state.timestamp = datetime.now().isoformat()
        self.save()

    def get_content(self, module_name: str, pass_name: str) -> Optional[str]:
        """Load content from the output file for a given pass.

        Returns None if the file doesn't exist or the pass is unknown.
        """
        filename = self.PASS_FILE_MAP.get(pass_name)
        if not filename:
            return None

        if pass_name in self.DEBUG_STAGE_PASSES:
            file_path = os.path.join(self.output_dir, "debug", module_name, filename)
        else:
            file_path = os.path.join(self.output_dir, "modules", module_name, filename)
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return None

    def verify_content(self, module_name: str, pass_name: str) -> bool:
        """Verify that the stored hash matches the actual file content.

        Returns True if hash matches, False otherwise.
        """
        state = self.states.get(module_name)
        if not state:
            return False

        field_name = self.PASS_FIELD_MAP.get(pass_name)
        if not field_name:
            return False

        stored_hash = getattr(state, field_name, None)
        if not stored_hash:
            return False

        content = self.get_content(module_name, pass_name)
        if content is None:
            return False

        return self._compute_hash(content) == stored_hash

    def is_done(self, module_name: str, pass_name: str) -> bool:
        """Generic completion check."""
        state = self.states.get(module_name)
        if not state:
            return False
        
        # For parallel passes, check field presence
        if pass_name in self.PARALLEL_PASSES:
            field_name = self.PASS_FIELD_MAP.get(pass_name)
            return field_name and getattr(state, field_name, None) is not None
        
        # For linear passes, check status progression
        target_status = self.PASS_STATUS_MAP.get(pass_name)
        if not target_status:
            return False
            
        status_order = ["pending"] + [self.PASS_STATUS_MAP.get(p, f"{p}_done") 
                                       for p in self.LINEAR_PASSES]
        try:
            target_idx = status_order.index(target_status)
            current_idx = status_order.index(state.status) if state.status in status_order else -1
            return current_idx >= target_idx
        except ValueError:
            return False

    # Convenience methods for backward compatibility
    def update_pass1(self, module_name: str, overview: str):
        self.update(module_name, "pass1", overview)

    def update_pass1_5(self, module_name: str, marker: str):
        self.update(module_name, "pass1_5", marker)

    def update_pass2(self, module_name: str, description: str):
        self.update(module_name, "pass2", description)

    def update_pass2_a_root(self, module_name: str, root_doc: str):
        self.update(module_name, "pass2_a_root", root_doc)

    def update_pass2_b_expand(self, module_name: str, expanded_doc: str):
        self.update(module_name, "pass2_b_expand", expanded_doc)

    def update_pass2_c_polish(self, module_name: str, polished_doc: str):
        self.update(module_name, "pass2_c_polish", polished_doc)

    def update_pass2_1(self, module_name: str, highlights: str):
        self.update(module_name, "pass2_1", highlights)

    def update_pass2_2(self, module_name: str, mermaid: str):
        self.update(module_name, "pass2_2", mermaid)

    def update_pass2_3(self, module_name: str, interface: str):
        self.update(module_name, "pass2_3", interface)

    def update_pass2_4(self, module_name: str, functional: str):
        self.update(module_name, "pass2_4", functional)

    def update_pass2_5(self, module_name: str, register: str):
        self.update(module_name, "pass2_5", register)

    def update_pass2_6(self, module_name: str, timing: str):
        self.update(module_name, "pass2_6", timing)

    def update_pass2_7(self, module_name: str, architecture: str):
        self.update(module_name, "pass2_7", architecture)

    # Content retrieval methods
    def get_pass1_content(self, module_name: str) -> Optional[str]:
        """Get pass1 (preview) content from file."""
        return self.get_content(module_name, "pass1")

    def get_pass2_1_content(self, module_name: str) -> Optional[str]:
        """Get pass2_1 (design highlights) content from file."""
        return self.get_content(module_name, "pass2_1")

    def get_pass2_2_content(self, module_name: str) -> Optional[str]:
        """Get pass2_2 (mermaid) content from file."""
        return self.get_content(module_name, "pass2_2")

    def get_pass2_3_content(self, module_name: str) -> Optional[str]:
        """Get pass2_3 (interface spec) content from file."""
        return self.get_content(module_name, "pass2_3")

    def get_pass2_4_content(self, module_name: str) -> Optional[str]:
        """Get pass2_4 (functional desc) content from file."""
        return self.get_content(module_name, "pass2_4")

    def get_pass2_5_content(self, module_name: str) -> Optional[str]:
        """Get pass2_5 (register desc) content from file."""
        return self.get_content(module_name, "pass2_5")

    def get_pass2_6_content(self, module_name: str) -> Optional[str]:
        """Get pass2_6 (timing/cdc) content from file."""
        return self.get_content(module_name, "pass2_6")

    def get_pass2_7_content(self, module_name: str) -> Optional[str]:
        """Get pass2_7 (architecture) content from file."""
        return self.get_content(module_name, "pass2_7")

    def get_pass2_a_root_content(self, module_name: str) -> Optional[str]:
        """Get pass2_a_root content from file."""
        return self.get_content(module_name, "pass2_a_root")

    def get_pass2_b_expand_content(self, module_name: str) -> Optional[str]:
        """Get pass2_b_expand content from file."""
        return self.get_content(module_name, "pass2_b_expand")

    def get_pass2_c_polish_content(self, module_name: str) -> Optional[str]:
        """Get pass2_c_polish content from file."""
        return self.get_content(module_name, "pass2_c_polish")

    def get_pass2_content(self, module_name: str) -> Optional[str]:
        """Get pass2 (description) content from file."""
        return self.get_content(module_name, "pass2")

    def is_pass1_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass1")

    def is_pass1_5_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass1_5")

    def is_pass2_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2")

    def is_pass2_1_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_1")

    def is_pass2_2_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_2")

    def is_pass2_3_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_3")

    def is_pass2_4_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_4")

    def is_pass2_5_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_5")

    def is_pass2_6_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_6")

    def is_pass2_7_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_7")

    def is_pass2_a_root_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_a_root")

    def is_pass2_b_expand_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_b_expand")

    def is_pass2_c_polish_done(self, module_name: str) -> bool:
        return self.is_done(module_name, "pass2_c_polish")

    def mark_failed(self, module_name: str, error: str):
        state = self.get_state(module_name)
        state.status = "failed"
        state.error = error
        state.timestamp = datetime.now().isoformat()
        self.save()

    def clear(self):
        self.states.clear()
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)
