"""Source code location data model."""

from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class SourceLocation:
    """Represents a source code location in an RTL file.
    
    Attributes:
        file_path: Absolute or relative path to the source file
        start_line: Starting line number (1-indexed)
        end_line: Ending line number (optional, for multi-line constructs)
        start_col: Starting column number (optional)
        end_col: Ending column number (optional)
    """
    file_path: str
    start_line: int
    end_line: Optional[int] = None
    start_col: Optional[int] = None
    end_col: Optional[int] = None

    # Pattern for parsing Yosys src attribute: "file:line" or "file:line.col-line.col"
    SRC_PATTERN = re.compile(
        r'"?([^:"]+):(\d+)(?:\.(\d+))?(?:-(\d+)(?:\.(\d+))?)?"?'
    )

    def short_display(self) -> str:
        """Return short format: filename:line"""
        filename = self.file_path.split('/')[-1]
        if self.end_line and self.end_line != self.start_line:
            return f"{filename}:{self.start_line}-{self.end_line}"
        return f"{filename}:{self.start_line}"

    def full_display(self) -> str:
        """Return full path format: /path/to/file:line"""
        if self.end_line and self.end_line != self.start_line:
            return f"{self.file_path}:{self.start_line}-{self.end_line}"
        return f"{self.file_path}:{self.start_line}"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "file": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_col": self.start_col,
            "end_col": self.end_col
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SourceLocation':
        """Create from dictionary."""
        return cls(
            file_path=data["file"],
            start_line=data["start_line"],
            end_line=data.get("end_line"),
            start_col=data.get("start_col"),
            end_col=data.get("end_col")
        )

    @classmethod
    def parse(cls, src_value: str) -> Optional['SourceLocation']:
        """Parse a Yosys src attribute value.
        
        Args:
            src_value: String like "file.v:123" or "file.v:10.5-20.8"
            
        Returns:
            SourceLocation if parsing succeeds, None otherwise
        """
        match = cls.SRC_PATTERN.match(src_value)
        if match:
            return cls(
                file_path=match.group(1),
                start_line=int(match.group(2)),
                start_col=int(match.group(3)) if match.group(3) else None,
                end_line=int(match.group(4)) if match.group(4) else None,
                end_col=int(match.group(5)) if match.group(5) else None
            )
        return None


@dataclass
class AggregatedLocation:
    """Aggregated location information from multiple cells.
    
    Used to summarize location information for simplified schematic nodes
    that combine multiple cells.
    """
    files: dict  # file_path -> set of line numbers
    
    def __init__(self):
        self.files = {}
    
    def add(self, file_path: str, line: int) -> None:
        """Add a location to the aggregation."""
        if file_path not in self.files:
            self.files[file_path] = set()
        self.files[file_path].add(line)
    
    def add_location(self, loc: SourceLocation) -> None:
        """Add a SourceLocation to the aggregation."""
        self.add(loc.file_path, loc.start_line)
        if loc.end_line:
            for line in range(loc.start_line, loc.end_line + 1):
                self.add(loc.file_path, line)
    
    def get_display_label(self, max_files: int = 2) -> str:
        """Get a display label for the aggregated locations.
        
        Args:
            max_files: Maximum number of files to show
            
        Returns:
            String like "file.v:10-20" or "file.v:10-20, other.v:5"
        """
        if not self.files:
            return ""
        
        parts = []
        for file_path in sorted(self.files.keys())[:max_files]:
            filename = file_path.split('/')[-1]
            lines = sorted(self.files[file_path])
            if lines:
                min_line, max_line = min(lines), max(lines)
                if min_line == max_line:
                    parts.append(f"{filename}:{min_line}")
                else:
                    parts.append(f"{filename}:{min_line}-{max_line}")
        
        result = "\\n".join(parts)
        remaining = len(self.files) - max_files
        if remaining > 0:
            result += f"\\n(+{remaining} files)"
        
        return result
