"""
Local development settings.
"""

from .base import *  # noqa: F403

DEBUG = env.bool("DEBUG", default=True)  # noqa: F405
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])  # noqa: F405
