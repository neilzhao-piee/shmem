#!/usr/bin/env python3
"""Compat shim, same pattern as summarize-sessions.py: code lives in shmem/health.py."""
import sys
from pathlib import Path

try:
    import shmem.health as _mod
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        import shmem.health as _mod
    except ImportError:
        sys.exit("shmem: the implementation lives in the 'shmem' package. Keep the package "
                 "directory next to tools/ or pip install shmem.")

if __name__ == "__main__":
    sys.exit(_mod.main())
