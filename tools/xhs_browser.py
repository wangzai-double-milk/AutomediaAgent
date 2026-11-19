import sys
from automedia.infrastructure.browser import xhs_browser as _implementation
sys.modules[__name__] = _implementation
