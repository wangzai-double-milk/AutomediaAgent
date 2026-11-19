import sys
from automedia.behavior.media import evidence as _implementation
sys.modules[__name__] = _implementation
