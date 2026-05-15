"""Root conftest.py — adds src/ to sys.path so tests can import bot.*."""
import sys
from pathlib import Path

# Ensure the src/ directory is on the path so that `import bot` works
# without a full editable install.
sys.path.insert(0, str(Path(__file__).parent / "src"))
