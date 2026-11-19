import sys
from automedia.application import api as _implementation
sys.modules[__name__] = _implementation
