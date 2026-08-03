"""Test configuration.

The domain and ingest layers are pure: they need no database, no warehouse and
no Databricks workspace. That is the whole point of the layering, and it is why
this suite runs in under a second.
"""

import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
