"""Compatibility import; implementation lives in automedia."""
import sys
from automedia.thinking import engine as _implementation
sys.modules[__name__] = _implementation
