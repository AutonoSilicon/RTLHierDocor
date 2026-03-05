"""Project-level progress tracker for chip-level artifacts.

Stores hash-based status for pass3 outputs under a dedicated output directory,
typically: <output_dir>/chip/.progress.json
"""

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class ProjectArtifactState:
    """State for one project-level artifact file."""

    artifact: str
    content_hash: Optional[str] = None
    input_hash: Optional[str] = None
    status: str = "pending"
    timestamp: Optional[str] = None
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class ProjectProgressTracker:
    """Track resume status for project-level outputs (pass3)."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.progress_file = os.path.join(output_dir, ".progress.json")
        self.states: Dict[str, ProjectArtifactState] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.progress_file):
            return
        try:
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for artifact, state_dict in data.items():
                state = ProjectArtifactState(**state_dict)
                # Crash-safe recovery: stale running state should not block resume.
                if state.status == "running":
                    state.status = "failed"
                    state.error = state.error or "Interrupted before completion"
                    state.timestamp = datetime.now().isoformat()
                self.states[artifact] = state
        except Exception as e:
            print(f"[WARN] Failed to load project progress: {e}")

    def save(self):
        os.makedirs(self.output_dir, exist_ok=True)
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump({k: asdict(v) for k, v in self.states.items()}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] Failed to save project progress: {e}")

    @staticmethod
    def _compute_hash(content: str) -> str:
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def get_state(self, artifact: str) -> ProjectArtifactState:
        if artifact not in self.states:
            self.states[artifact] = ProjectArtifactState(artifact=artifact)
        return self.states[artifact]

    def mark_running(self, artifact: str, input_hash: Optional[str] = None, meta: Optional[Dict[str, Any]] = None):
        state = self.get_state(artifact)
        state.status = "running"
        state.timestamp = datetime.now().isoformat()
        state.error = None
        if input_hash:
            state.input_hash = input_hash
        if meta is not None:
            state.meta = meta
        self.save()

    def update(
        self,
        artifact: str,
        content: str,
        input_hash: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ):
        state = self.get_state(artifact)
        state.content_hash = self._compute_hash(content)
        if input_hash is not None:
            state.input_hash = input_hash
        state.status = "done"
        state.timestamp = datetime.now().isoformat()
        state.error = None
        if meta is not None:
            state.meta = meta
        self.save()

    def is_done(self, artifact: str, file_path: str, expected_input_hash: Optional[str] = None) -> bool:
        state = self.states.get(artifact)
        if not state or state.status != "done" or not state.content_hash:
            return False
        if expected_input_hash is not None and state.input_hash != expected_input_hash:
            return False
        if not os.path.exists(file_path):
            return False
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return self._compute_hash(content) == state.content_hash
        except Exception:
            return False

    def mark_failed(self, artifact: str, error: str):
        state = self.get_state(artifact)
        state.status = "failed"
        state.error = error
        state.timestamp = datetime.now().isoformat()
        self.save()

    def clear(self):
        self.states.clear()
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)
