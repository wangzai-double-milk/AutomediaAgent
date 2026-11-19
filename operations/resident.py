"""Compatibility entry for python -m operations.resident."""
import sys
if __name__ == "__main__":
    from automedia.infrastructure.resident import main
    main()
else:
    from automedia.infrastructure import resident as _implementation
    sys.modules[__name__] = _implementation
