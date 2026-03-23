#!/usr/bin/env python3
"""Setup script for RTL Hierarchy Documentor."""

from setuptools import setup, find_packages

setup(
    name="rtl-hier-docor",
    version="0.1.0",
    description="Generate hierarchical documentation for RTL designs using Yosys",
    author="RTLHierDocor Team",
    python_requires=">=3.8",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    entry_points={
        "console_scripts": [
            "rtl-hier-doc=rtl_hier_docor.cli:main",
        ],
    },
    install_requires=[
        # pyosys is provided by Yosys installation
        # pyyaml optional; built-in simple YAML parser used by default
    ],
    extras_require={
        "dev": [
            "pytest",
            "pytest-cov",
        ],
        "agent": [
            "anthropic>=0.3.0",
        ],
        "webui": [
            "fastapi>=0.100.0",
            "uvicorn[standard]>=0.23.0",
            "websockets>=11.0",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Scientific/Engineering :: Electronic Design Automation (EDA)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
)
