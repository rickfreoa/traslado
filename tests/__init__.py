import sys
from pathlib import Path

_p = str(Path(__file__).parent)
if _p not in sys.path:
    sys.path.insert(0, _p)
