import json
import os
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List
from datetime import datetime

@dataclass
class ModuleDocState:
    module_name: str
    status: str  # "pending" | "pass1_done" | "pass1_5_done" | "pass2_1_done" | "pass2_5_done" | "pass2_done" | "failed"
    pass1_overview: Optional[str] = None
    pass1_5_block_docs: Optional[str] = None  # Block docs completion marker
    pass2_1_highlights: Optional[str] = None  # Design highlights/tricks
    pass2_5_mermaid: Optional[str] = None
    pass2_description: Optional[str] = None  # Now comes LAST (synthesis)
    error: Optional[str] = None
    timestamp: Optional[str] = None

class ProgressTracker:
    """Tracks documentation progress and supports resume."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.progress_file = os.path.join(output_dir, ".progress.json")
        self.states: Dict[str, ModuleDocState] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for mod_name, state_dict in data.items():
                        self.states[mod_name] = ModuleDocState(**state_dict)
            except Exception as e:
                print(f"[WARN] Failed to load progress file: {e}")

    def save(self):
        os.makedirs(self.output_dir, exist_ok=True)
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                data = {name: asdict(state) for name, state in self.states.items()}
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] Failed to save progress file: {e}")

    def get_state(self, module_name: str) -> ModuleDocState:
        if module_name not in self.states:
            self.states[module_name] = ModuleDocState(
                module_name=module_name,
                status="pending"
            )
        return self.states[module_name]

    def update_pass1(self, module_name: str, overview: str):
        state = self.get_state(module_name)
        state.pass1_overview = overview
        state.status = "pass1_done"
        state.timestamp = datetime.now().isoformat()
        self.save()

    def update_pass1_5(self, module_name: str, block_docs_marker: str = "done"):
        state = self.get_state(module_name)
        state.pass1_5_block_docs = block_docs_marker
        state.status = "pass1_5_done"
        state.timestamp = datetime.now().isoformat()
        self.save()

    def update_pass2(self, module_name: str, description: str):
        state = self.get_state(module_name)
        state.pass2_description = description
        state.status = "pass2_done"  # Terminal state
        state.timestamp = datetime.now().isoformat()
        self.save()

    def update_pass2_1(self, module_name: str, highlights: str):
        state = self.get_state(module_name)
        state.pass2_1_highlights = highlights
        state.status = "pass2_1_done"
        state.timestamp = datetime.now().isoformat()
        self.save()

    def update_pass2_5(self, module_name: str, mermaid: str):
        state = self.get_state(module_name)
        state.pass2_5_mermaid = mermaid
        state.status = "pass2_5_done"
        state.timestamp = datetime.now().isoformat()
        self.save()

    def mark_failed(self, module_name: str, error: str):
        state = self.get_state(module_name)
        state.status = "failed"
        state.error = error
        state.timestamp = datetime.now().isoformat()
        self.save()

    def is_pass1_done(self, module_name: str) -> bool:
        state = self.states.get(module_name)
        return state and state.status in ["pass1_done", "pass1_5_done", "pass2_1_done", "pass2_5_done", "pass2_done"]

    def is_pass1_5_done(self, module_name: str) -> bool:
        state = self.states.get(module_name)
        return state and state.status in ["pass1_5_done", "pass2_1_done", "pass2_5_done", "pass2_done"]

    def is_pass2_1_done(self, module_name: str) -> bool:
        state = self.states.get(module_name)
        # Check field presence (parallel execution, not linear status)
        return state and state.pass2_1_highlights is not None

    def is_pass2_5_done(self, module_name: str) -> bool:
        state = self.states.get(module_name)
        # Check field presence (parallel execution, not linear status)
        return state and state.pass2_5_mermaid is not None

    def is_pass2_done(self, module_name: str) -> bool:
        state = self.states.get(module_name)
        return state and state.status == "pass2_done"

    def clear(self):
        self.states.clear()
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)
