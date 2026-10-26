"""Cost-Negative Yield Scanner: what a DeFi yield pays after the costs of using it."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("defi-risk-tools")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
