#!/usr/bin/env python3
"""Совместимость: стенд переехал в extension-forge/stand/audit.py."""
import runpy
import sys

SCRIPT = "/home/work/repos/extension-forge/stand/audit.py"
sys.argv = [SCRIPT] + sys.argv[1:]
runpy.run_path(SCRIPT, run_name="__main__")
