"""Shared release registry. No application code is imported by automation."""
import json
from pathlib import Path
RELEASE_DIR = Path(__file__).resolve().parent
ROOT = RELEASE_DIR.parents[2]
def projects():
    return json.loads((RELEASE_DIR / "projects.json").read_text(encoding="utf-8"))
def project(key):
    return projects()[key]
