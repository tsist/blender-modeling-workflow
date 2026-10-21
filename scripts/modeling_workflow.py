"""Portable entry point for the repository's bounded modeling CLI."""
from pathlib import Path
import runpy
import sys

cli = Path(__file__).resolve().parents[1] / 'tools/blenderctl/cli.py'
sys.path.insert(0, str(cli.parent))
runpy.run_path(str(cli), run_name='__main__')
