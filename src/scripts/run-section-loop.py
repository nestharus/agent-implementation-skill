#!/usr/bin/env python3
"""Wrapper to run the section-loop using __pycache__ bytecode files."""
import os
import sys

# Ensure we're in the scripts directory
scripts_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(scripts_dir)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# Install the PycFinder to load modules from __pycache__
import _pyc_loader
_pyc_loader.install(scripts_dir)

# Now import and run the section loop
from orchestrator.engine.main import main
main()
