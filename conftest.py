"""Root conftest.

Ensures the repository root is on ``sys.path`` so the ``custom_components``
package is importable during test collection (e.g. ``tests/conftest.py``),
regardless of the working directory pytest is invoked from in CI.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
