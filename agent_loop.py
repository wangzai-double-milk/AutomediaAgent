import sys
from automedia.legacy import agent_loop as _implementation
sys.modules[__name__] = _implementation
