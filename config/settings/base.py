"""
Base Django settings shared across all environments.
"""

import os
from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    SECRET_KEY=(str, "django-insecure-dev-only-change-before-production"),
    DATABASE_ENGINE=(str, "django.db.backends.sqlite3"),
    DATABASE_NAME=(str, str(BASE_DIR / "db.sqlite3")),
    DATABASE_USER=(str, ""),
    DATABASE_PASSWORD=(str, ""),
    DATABASE_HOST=(str, ""),
    DATABASE_PORT=(str, ""),
)

settings_module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
if settings_module.endswith(".local"):
    env.read_env(BASE_DIR / ".env.local")
elif settings_module.endswith(".production"):
    env.read_env(BASE_DIR / ".env.production")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "departments",
    "core",
    "sync",
    "drivers",
    "trucks",
    "relay",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": env("DATABASE_ENGINE"),
        "NAME": env("DATABASE_NAME"),
        "USER": env("DATABASE_USER"),
        "PASSWORD": env("DATABASE_PASSWORD"),
        "HOST": env("DATABASE_HOST"),
        "PORT": env("DATABASE_PORT"),
    }
}

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    db_name = DATABASES["default"]["NAME"]
    if not os.path.isabs(str(db_name)):
        db_name = BASE_DIR / db_name
    DATABASES["default"] = {
        "ENGINE": DATABASES["default"]["ENGINE"],
        "NAME": db_name,
    }
else:
    DATABASES["default"] = {
        key: value
        for key, value in DATABASES["default"].items()
        if value or key in {"ENGINE", "NAME"}
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Belgrade"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "relay:board"
LOGOUT_REDIRECT_URL = "accounts:login"
