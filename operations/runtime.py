import sys
from automedia.application import runtime as _implementation
sys.modules[__name__] = _implementation
