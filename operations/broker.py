import sys
from automedia.infrastructure import broker as _implementation
sys.modules[__name__] = _implementation
