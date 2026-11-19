import sys
from automedia.behavior.platforms import xhs_library as _implementation
sys.modules[__name__] = _implementation
