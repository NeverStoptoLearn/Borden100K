"""TRA-001: real SUMO traffic adapter for the frozen Phase I contract."""
from pathlib import Path
import sys

# Optional module-local installation; never modifies the scientific environment.
_deps = Path(__file__).parent / '.deps'
if _deps.is_dir():
    sys.path.insert(0, str(_deps))
