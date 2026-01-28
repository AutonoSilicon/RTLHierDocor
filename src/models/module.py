"""Module and port information data models."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from enum import Enum

from .source_loc import SourceLocation


class PortDirection(Enum):
    """Port direction enumeration."""
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


@dataclass
class PortInfo:
    """Information about a module port.
    
    Attributes:
        name: Port signal name
        direction: Port direction (input/output/inout)
        width: Bit width of the port
        location: Source code location where the port is defined
        connected_modules: List of module names connected to this port
    """
    name: str
    direction: PortDirection
    width: int = 1
    location: Optional[SourceLocation] = None
    connected_modules: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "direction": self.direction.value,
            "width": self.width,
            "location": self.location.to_dict() if self.location else None,
            "connected_modules": self.connected_modules
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PortInfo':
        """Create from dictionary."""
        return cls(
            name=data["name"],
            direction=PortDirection(data["direction"]),
            width=data["width"],
            location=SourceLocation.from_dict(data["location"]) if data.get("location") else None,
            connected_modules=data.get("connected_modules", [])
        )


@dataclass
class CellInfo:
    """Information about a cell instance within a module.
    
    Attributes:
        name: Cell instance name
        cell_type: Type of the cell (module name or primitive)
        location: Source code location where the cell is instantiated
        connections: Port connections as dict {port_name: wire_name}
    """
    name: str
    cell_type: str
    location: Optional[SourceLocation] = None
    connections: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "cell_type": self.cell_type,
            "location": self.location.to_dict() if self.location else None,
            "connections": self.connections
        }


@dataclass  
class ModuleInfo:
    """Information about an RTL module.
    
    Attributes:
        name: Module name (without leading backslash)
        raw_name: Original module name from Yosys (may include backslash)
        file_path: Source file path where module is defined
        location: Source code location of module definition
        ports: Dictionary of port information
        cells: Dictionary of cell instances
        is_blackbox: Whether this is a blackbox module
        is_parameterized: Whether this is a parameterized instance
    """
    name: str
    raw_name: str = ""
    file_path: Optional[str] = None
    location: Optional[SourceLocation] = None
    ports: Dict[str, PortInfo] = field(default_factory=dict)
    cells: Dict[str, CellInfo] = field(default_factory=dict)
    is_blackbox: bool = False
    is_parameterized: bool = False

    def __post_init__(self):
        if not self.raw_name:
            self.raw_name = self.name
        # Clean name: remove leading backslash
        if self.name.startswith("\\"):
            self.name = self.name[1:]
        # Check if parameterized
        if self.raw_name.startswith("$paramod"):
            self.is_parameterized = True

    @property
    def port_count(self) -> int:
        """Return the number of ports."""
        return len(self.ports)

    @property
    def cell_count(self) -> int:
        """Return the number of cell instances."""
        return len(self.cells)

    @property
    def input_ports(self) -> List[PortInfo]:
        """Return list of input ports."""
        return [p for p in self.ports.values() if p.direction == PortDirection.INPUT]

    @property
    def output_ports(self) -> List[PortInfo]:
        """Return list of output ports."""
        return [p for p in self.ports.values() if p.direction == PortDirection.OUTPUT]

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "raw_name": self.raw_name,
            "file_path": self.file_path,
            "location": self.location.to_dict() if self.location else None,
            "ports": {k: v.to_dict() for k, v in self.ports.items()},
            "cells": {k: v.to_dict() for k, v in self.cells.items()},
            "is_blackbox": self.is_blackbox,
            "is_parameterized": self.is_parameterized
        }
