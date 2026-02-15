"""Root conftest.py for test configuration.

Ensures local src/ directory takes priority over any installed packages.
"""

import sys
from pathlib import Path

# Insert local src directory at the beginning of sys.path
# This ensures that the local codeplane package is used, not any installed one
_src_dir = Path(__file__).parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Force reimport of codeplane modules if already imported
for module_name in list(sys.modules.keys()):
    if module_name.startswith("codeplane"):
        del sys.modules[module_name]
