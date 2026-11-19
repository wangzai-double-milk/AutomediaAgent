import sys
from automedia.infrastructure.browser import mac_browser as _implementation
sys.modules[__name__] = _implementation
