import sys
from automedia.infrastructure import llmcore as _implementation
sys.modules[__name__] = _implementation
