"""ASGI entry point: uvicorn api:app"""

from news_api.app import app

__all__ = ["app"]
