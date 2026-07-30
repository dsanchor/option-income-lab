"""Backwards-compatible entry point — delegates to run.py --web-only."""
import sys
import runpy

sys.argv = [sys.argv[0], "--web-only"]
runpy.run_path("run.py", run_name="__main__")
