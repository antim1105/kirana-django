"""
Django settings for the Kirana Wholesale Rate Manager.

Reads configuration from environment variables so the same code runs on
SQLite locally and on MySQL/PostgreSQL in production. Copy `.env.example`
to `.env` and edit it, or export the variables directly.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# --- Core -------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "antimjaiswal.pythonanywhere.com,localhost,127.0.0.1"
)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "manager",
]

try:  # WhiteNoise serves static files in production; it is optional locally.
    import whitenoise  # noqa: F401

    HAS_WHITENOISE = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_WHITENOISE = False

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *(["whitenoise.middleware.WhiteNoiseMiddleware"] if HAS_WHITENOISE else []),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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
                # Puts `settings_obj` and `nav_items` on every page so the
                # sidebar and currency symbol are always available.
                "manager.context_processors.shop_context",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------
#
# SQLite by default, with the file at `db.sqlite3` next to manage.py. Set
# DATABASE_ENGINE to "postgresql" or "mysql" to switch; the tables mirror the
# original TypeScript interfaces either way.
#
# SQLITE_PATH overrides where the file lives. A relative value is resolved
# against the project directory, so `SQLITE_PATH=data/rates.sqlite3` works.
# DATABASE_NAME is deliberately not used here: it names a *database* on a
# server, and reusing it for a filename means a stray value left over from a
# Postgres setup would silently create a junk file.

_engine = env("DATABASE_ENGINE", "sqlite").lower()

if _engine.startswith("post"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DATABASE_NAME", "kirana"),
            "USER": env("DATABASE_USER", "postgres"),
            "PASSWORD": env("DATABASE_PASSWORD", ""),
            "HOST": env("DATABASE_HOST", "127.0.0.1"),
            "PORT": env("DATABASE_PORT", "5432"),
        }
    }
elif _engine.startswith("mysql"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("DATABASE_NAME", "kirana"),
            "USER": env("DATABASE_USER", "root"),
            "PASSWORD": env("DATABASE_PASSWORD", ""),
            "HOST": env("DATABASE_HOST", "127.0.0.1"),
            "PORT": env("DATABASE_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }
else:
    SQLITE_PATH = Path(env("SQLITE_PATH", "db.sqlite3"))
    if not SQLITE_PATH.is_absolute():
        SQLITE_PATH = BASE_DIR / SQLITE_PATH
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(SQLITE_PATH),
            "OPTIONS": {
                # Wait rather than fail if another window is mid-write.
                "timeout": 20,
                "init_command": (
                    # Write-ahead logging lets the shop read on a phone while
                    # a rate is being saved on the desktop, instead of one
                    # blocking the other.
                    "PRAGMA journal_mode=WAL;"
                    # SQLite only enforces foreign keys when asked, and the
                    # cascade from a deleted product depends on it.
                    "PRAGMA foreign_keys=ON;"
                    "PRAGMA synchronous=NORMAL;"
                ),
            },
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth -------------------------------------------------------------------
#
# The app is private to one shop. Set REQUIRE_LOGIN=true to put every page
# behind the Django login screen; leave it off for single-user local use.

REQUIRE_LOGIN = env_bool("REQUIRE_LOGIN", False)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "dashboard"

# --- Locale -----------------------------------------------------------------

LANGUAGE_CODE = "en-in"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# Dates are typed and displayed day-first, like the original app.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"]

# --- Static files -----------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
_static_backend = "django.contrib.staticfiles.storage.StaticFilesStorage"
if HAS_WHITENOISE and not DEBUG:
    _static_backend = "whitenoise.storage.CompressedManifestStaticFilesStorage"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _static_backend},
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# --- Security (only bites when DEBUG is off) --------------------------------

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False
    X_FRAME_OPTIONS = "DENY"
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
    SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
    CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
