import sys
from automedia.infrastructure import channels as _implementation
sys.modules[__name__] = _implementation
