#!/usr/bin/env python3
"""Compat shim. The code lives in ``shmem/summarize.py`` since v0.5.0.

Invoked by the systemd/launchd units and by integrations through this path.
"""
import sys
from pathlib import Path

try:
    import shmem.summarize as _mod
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        import shmem.summarize as _mod
    except ImportError:
        sys.exit("shmem: the implementation moved to the 'shmem' package. Install it "
                 "(pip install shmem) or keep the package directory next to tools/ "
                 "(make new copies both).")

if __name__ == "__main__":
    _mod.main()
