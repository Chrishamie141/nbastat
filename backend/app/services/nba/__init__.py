"""NBA-only prediction, replay, and production-lifecycle services.

Nothing in this package imports the NFL prediction implementation.  That
boundary is deliberate: NBA and NFL model versions, features, snapshots, and
promotion decisions must evolve independently.
"""

# Keep package import side-effect free.  The model imports the legacy NBA data
# adapter, and that adapter imports season helpers; eager re-exports here would
# create a circular import.  Callers import the concrete submodule they need.
__all__: list[str] = []
