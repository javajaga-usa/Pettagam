"""Pettagam: organize a messy file tree into a smart, type-based library."""
import sys

__version__ = "0.3.0"
MIN_PYTHON = (3, 13)

# Checked here, before any module that uses newer syntax is imported, so an old Python gets a clear message.
if sys.version_info < MIN_PYTHON:
    raise SystemExit("Pettagam needs Python %d.%d or newer; this is %s. Install it from https://www.python.org/downloads/"
                     % (MIN_PYTHON + (sys.version.split()[0],)))
