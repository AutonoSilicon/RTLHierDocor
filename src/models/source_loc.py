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

