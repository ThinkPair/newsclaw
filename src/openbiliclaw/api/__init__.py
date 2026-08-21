"""HTTP API surface for browser-extension integration."""

from .app import create_app

__all__ = ["create_app"]
