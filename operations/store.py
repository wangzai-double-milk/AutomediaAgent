import sys
from automedia.infrastructure import store as _implementation
sys.modules[__name__] = _implementation
