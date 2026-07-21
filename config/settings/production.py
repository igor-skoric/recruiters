"""
Production settings.
"""

from .base import *  # noqa: F403

DEBUG = env.bool("DEBUG", default=False)  # noqa: F405
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])  # noqa: F405

STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405
STATIC_URL = "/static/"

# Serve collected static files (admin CSS/JS, app assets) via Django/Passenger
MIDDLEWARE = [  # noqa: F405
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],  # noqa: F405
]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
