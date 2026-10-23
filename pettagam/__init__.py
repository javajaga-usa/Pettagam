"""Pettagam: organize a messy file tree into a smart, type-based library."""
import sys

__version__ = "0.6.0"
MIN_PYTHON = (3, 13)

# Checked here, before any module that uses newer syntax is imported, so an old Python gets a clear message.
if sys.version_info < MIN_PYTHON:
    raise SystemExit(f"Pettagam needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; this is "
                     f"{sys.version.split()[0]}. Install it from https://www.python.org/downloads/")
