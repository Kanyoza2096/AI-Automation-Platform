"""Facebook connector — impl under plugins.facebook during migration."""
from __future__ import annotations
try:
    from plugins.facebook import *  # noqa: F401,F403
except ImportError:
    pass
