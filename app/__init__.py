# Fallback: if senior_ai is not pip-installed (e.g. macOS hides the editable .pth), use the sibling AI/ checkout.
import sys
from pathlib import Path

try:
    import senior_ai  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "AI"))
