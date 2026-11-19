import sys
from automedia.behavior.platforms import xhs_desktop as _implementation
sys.modules[__name__] = _implementation
