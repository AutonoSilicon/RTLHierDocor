"""Generic pass runner to eliminate boilerplate code.

This module provides a unified way to run documentation generation passes
with consistent skip logic, progress tracking, and error handling.
"""

import json
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PassConfig:
    """Configuration for a documentation generation pass."""
    name: str  # e.g., "pass2_1", "pass2_2"
    display_name: str  # e.g., "Design Highlights", "Flowchart"
    # Dependencies: list of pass names that must be completed before this pass
    depends_on: List[str]
    # Whether this pass needs SimplifiedGraph
    needs_graph: bool = True
    # Whether to recurse children first (bottom-up)
    recurse_first: bool = True
    # Whether to pass children summaries to generator
    use_children_results: bool = True
    # Output file name (without extension)
    output_file: Optional[str] = None
    # Status field name in tracker
    tracker_field: Optional[str] = None


class PassRunner:
    """Generic runner for documentation generation passes.
    
    Eliminates repetitive boilerplate in doc_generator.py by providing
    a unified execution framework for all passes.
    """
    
    def __init__(
        self,
        config: PassConfig,
        generator: Callable[..., Awaitable[str]],
        doc_generator: 'AgentDocGenerator',  # type: ignore
    ):
        self.config = config
        self.generator = generator
        self.dg = doc_generator
        self._processed: set = set()
        
    def is_done(self, module_name: str) -> bool:
        """Check if this pass is done for the module."""
        tracker = self.dg.tracker
        state = tracker.get_state(module_name)
        
        # Check field presence for parallel passes
        field = self.config.tracker_field or f"pass{self.config.name.replace('pass', '')}"
        return getattr(state, field, None) is not None
        
    def get_result(self, module_name: str) -> Optional[str]:
        """Get the pass result from tracker."""
        state = self.dg.tracker.get_state(module_name)
        field = self.config.tracker_field or f"pass{self.config.name.replace('pass', '')}"
        return getattr(state, field, None)
        
    async def run(self, node: Any) -> Optional[str]:
        """Run the pass for a module node.
        
        Handles all common logic:
        - Skip pattern matching
        - max_modules limit
        - Recursion (if bottom-up)
        - Duplicate detection (session + persistent)
        - Dependency checking
        - Error handling
        - Result saving
        """
        module_name = node.module_name
        pass_id = self.config.name
        display = self.config.display_name
        
        # 1. Skip check
        if self.dg._should_skip(module_name):
            print(f"[INFO] [{display}] Skipping module {module_name} (matches skip pattern)")
            return None
            
        # 2. max_modules limit check
        if self.dg.max_modules > 0 and module_name not in self.dg._processed_pass1:
            return None
            
        # 3. Recurse children first (for bottom-up passes)
        children_results: Dict[str, str] = {}
        if self.config.recurse_first:
            for child in node.children.values():
                result = await self.run(child)
                if result and self.config.use_children_results:
                    children_results[child.module_name] = result
                    
        # 4. Check if already processed in this session
        if module_name in self._processed:
            return self.get_result(module_name)
            
        # 5. Check persistent storage
        if self.is_done(module_name):
            self._processed.add(module_name)
            print(f"  [{display}] Module {module_name} already processed (skipping LLM)")
            return self.get_result(module_name)
            
        # 6. Check dependencies
        for dep in self.config.depends_on:
            if not self.dg._is_pass_done(module_name, dep):
                print(f"  [{display}] Skipping {module_name} ({dep} not completed)")
                self._processed.add(module_name)
                return None
                
        # 7. Check if graph is needed and available
        if self.config.needs_graph and module_name not in self.dg._graphs:
            print(f"  [{display}] Skipping {module_name} (no SimplifiedGraph)")
            self._processed.add(module_name)
            return None
            
        # 8. Generate documentation
        print(f"  [{display}] Generating for {module_name}...")
        
        try:
            if self.config.use_children_results:
                result = await self.generator(node, children_results)
            else:
                result = await self.generator(node)
                
            if result:
                # Save to file if configured
                if self.config.output_file:
                    self.dg._save_module_file(module_name, self.config.output_file, result)
                    
                # Update tracker
                self.dg._update_tracker(module_name, pass_id, result)
                
            self._processed.add(module_name)
            self.dg._save_metadata(node)
            return result
            
        except Exception as e:
            print(f"  [ERROR] [{display}] Failed for {module_name}: {e}")
            self._processed.add(module_name)
            return None
