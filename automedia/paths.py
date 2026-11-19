"""Stable resource and workspace paths, independent of module nesting."""
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = RESOURCE_ROOT
