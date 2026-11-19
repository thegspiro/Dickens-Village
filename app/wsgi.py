"""WSGI entry point for gunicorn: ``gunicorn app.wsgi:app``."""

from . import create_app

app = create_app()
