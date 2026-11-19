"""Compatibility import; implementation lives in automedia."""
import sys
from automedia.thinking import planner as _implementation
sys.modules[__name__] = _implementation
