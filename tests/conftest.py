import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "spyqqq"))      # ported tests import bare module names
os.environ.setdefault("SPYQQQ_DRY_RUN", "1")
