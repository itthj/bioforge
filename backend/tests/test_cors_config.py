"""CORS origins are configurable via BIOFORGE_CORS_ORIGINS (default = local dev ports).

A misconfigured CORS policy is a real deploy footgun, so this pins the default (unchanged
local behavior) and the parametrized + wildcard paths.
"""

from __future__ import annotations

from bioforge.config import settings
from starlette.middleware.cors import CORSMiddleware


def _cors_mw(app):
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return mw
    raise AssertionError("CORSMiddleware not installed")


def test_default_origins_are_local(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173,http://localhost:3000")
    from bioforge.main import create_app

    mw = _cors_mw(create_app())
    assert mw.kwargs["allow_origins"] == ["http://localhost:5173", "http://localhost:3000"]
    assert mw.kwargs["allow_credentials"] is True


def test_custom_origins_parsed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cors_origins", "https://app.example.com, https://admin.example.com")
    from bioforge.main import create_app

    mw = _cors_mw(create_app())
    assert mw.kwargs["allow_origins"] == ["https://app.example.com", "https://admin.example.com"]
    assert mw.kwargs["allow_credentials"] is True


def test_wildcard_drops_credentials(monkeypatch) -> None:
    """'*' + credentials is rejected by browsers; we drop credentials so responses are usable."""
    monkeypatch.setattr(settings, "cors_origins", "*")
    from bioforge.main import create_app

    mw = _cors_mw(create_app())
    assert mw.kwargs["allow_origins"] == ["*"]
    assert mw.kwargs["allow_credentials"] is False
