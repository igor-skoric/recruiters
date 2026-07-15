"""
Production settings.
"""

from .base import *  # noqa: F403

DEBUG = env.bool("DEBUG", default=False)  # noqa: F405
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])  # noqa: F405

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
