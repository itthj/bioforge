"""ML models that back tool predictions.

Sub-packages here wrap third-party trained models. Each one is responsible for
its own licensing posture, fetch/cache mechanics, and inference shim. Tools
that depend on these models import their public API and never reach upstream
artifacts directly.
"""
