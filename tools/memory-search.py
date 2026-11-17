#!/usr/bin/env python3
"""Compat shim. The code lives in ``shmem/search.py`` since v0.5.0.

This path keeps working because memory trees symlink or copy it (see the Makefile
and units/), integrations point at it, and PROTOCOL.md names it. It resolves the
package either from an installed distribution or from the repository / memory
tree next to it.
"""
import sys
from pathlib import Path

try:
    import shmem.search as _mod
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        import shmem.search as _mod
    except ImportError:
        sys.exit("shmem: the implementation moved to the 'shmem' package. Install it "
                 "(pip install shmem) or keep the package directory next to tools/ "
                 "(make new copies both).")

if __name__ == "__main__":
    _mod.main()
