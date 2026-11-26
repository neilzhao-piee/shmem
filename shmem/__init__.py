"""shmem — shared memory for agents: markdown on disk, search at query time.

The protocol is the product (see PROTOCOL.md); this package is its reference
implementation. Zero runtime dependencies, standard library only.
"""

# Single source of truth for the version. pyproject.toml reads this attribute
# dynamically (it is not duplicated there), so an installed distribution's
# metadata and the source in a git clone can never drift apart.
__version__ = "0.6.0"


def get_version():
    """Version of the code actually running.

    An installed distribution reports its own metadata (built from the literal
    above); a git clone has no metadata and reports the literal.
    """
    try:
        from importlib.metadata import version
        return version("shmem")
    except Exception:
        return __version__
