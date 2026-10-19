"""Legacy import alias; implementation belongs to Contextopus Core."""
import sys
from contextopus.core import chunking as _implementation
sys.modules[__name__] = _implementation
