"""Tracks documentation progress with generic pass support."""

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List
from datetime import datetime


@dataclass
class ModuleDocState:
    """State of documentation for a single module."""
    module_name: str
    status: str = "pending"
    
    # Pass results - using generic naming pattern
    pass1_overview: Optional[str] = None
    pass1_5_block_docs: Optional[str] = None
    pass2_1_highlights: Optional[str] = None
    pass2_2_mermaid: Optional[str] = None
    pass2_3_interface: Optional[str] = None
    pass2_4_functional: Optional[str] = None
    pass2_5_register: Optional[str] = None
    pass2_6_timing_cdc: Optional[str] = None
    pass2_description: Optional[str] = None
    
    error: Optional[str] = None
    timestamp: Optional[str] = None
    
    # Track arbitrary pass results for extensibility
    extra: Dict[str, str] = field(default_factory=dict)


class ProgressTracker:
    """Tracks documentation progress with resume capability."""
    
    # Define pass progression order
    LINEAR_PASSES = ["pass1", "pass1_5", "pass2"]
    PARALLEL_PASSES = ["pass2_1", "pass2_2", "pass2_3", "pass2_4", "pass2_5", "pass2_6"]
    
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
        "pass2": "pass2_done",
    }
    
    PASS_FIELD_MAP = {
        "pass1": "pass1_overview",
        "pass1_5": "pass1_5_block_docs",
        "pass2_1": "pass2_1_highlights",
        "pass2_2": "pass2_2_mermaid",
        "pass2_3": "pass2_3_interface",
        "pass2_4": "pass2_4_functional",
        "pass2_5": "pass2_5_register",
        "pass2_6": "pass2_6_timing_cdc",
        "pass2": "pass2_description",
    }

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

    def update(self, module_name: str, pass_name: str, result: str):
        """Generic update method for any pass."""
        state = self.get_state(module_name)
        
        # Set the result field
        field_name = self.PASS_FIELD_MAP.get(pass_name)
        if field_name:
            setattr(state, field_name, result)
        else:
            # Store in extra for unknown passes
            state.extra[pass_name] = result
        
        # Update status
        status = self.PASS_STATUS_MAP.get(pass_name)
        if status:
            state.status = status
        
        state.timestamp = datetime.now().isoformat()
        self.save()

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
