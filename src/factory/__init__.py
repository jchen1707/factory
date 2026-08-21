"""Layer D — the control plane.

The factory decides what runs, when, in what order, and what happens when it dies.
It holds no gate command and no review prompt: those it reads from the target
repository's `harness.config.json` and from the vendored layer-A tree.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
