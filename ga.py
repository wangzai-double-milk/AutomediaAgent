"""Compatibility import for existing tools and integrations."""
import sys
from automedia.legacy import ga as _implementation
sys.modules[__name__] = _implementation
