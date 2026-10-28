"""Django settings for the Mailbox project. All deploy-specific values come from env vars."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")

# Key used to encrypt stored IMAP/SMTP passwords. Falls back to a key derived from SECRET_KEY.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "mail",
    "panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
                "mail.context_processors.brand",
                "mail.context_processors.mailbox",
                "panel.context_processors.panel",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "mailbox"),
        "USER": env("POSTGRES_USER", "mailbox"),
        "PASSWORD": env("POSTGRES_PASSWORD", "mailbox"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
import sys  # noqa: E402

if len(sys.argv) > 1 and sys.argv[1] == "test":  # tests run without collectstatic
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Users sign in with their e-mail address (see mail/accounts.py)
AUTHENTICATION_BACKENDS = ["mail.accounts.EmailBackend"]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---- Mailbox behaviour ----
# Branding shown in the top bar, login page and browser tab
BRAND = {
    "NAME": env("BRAND_NAME", "easybillo"),
    "PRODUCT": env("BRAND_PRODUCT", "Mail"),
    "LOGO": env("BRAND_LOGO", "mail/brand/logo-mark.svg"),  # path inside static files
}

MAILBOX = {
    "ALLOW_SIGNUP": env_bool("MAILBOX_ALLOW_SIGNUP", True),
    # How many of the newest messages per folder to download on the first sync.
    "INITIAL_SYNC_LIMIT": int(env("MAILBOX_INITIAL_SYNC_LIMIT", "500")),
    # Background worker interval in seconds.
    "SYNC_INTERVAL": int(env("MAILBOX_SYNC_INTERVAL", "60")),
    "PAGE_SIZE": int(env("MAILBOX_PAGE_SIZE", "50")),
    "IMAP_TIMEOUT": int(env("MAILBOX_IMAP_TIMEOUT", "30")),
    "MAX_ATTACHMENT_MB": int(env("MAILBOX_MAX_ATTACHMENT_MB", "25")),
}

# ---- Creating Linux users on the Ubuntu (mail) server from the admin panel ----
# The app connects over SSH and runs the restricted helper tools/server/mailbox-user through sudo.
LINUX_USERS = {
    "ENABLED": env_bool("LINUX_USERS_ENABLED", False),
    "SSH_HOST": env("LINUX_SSH_HOST", ""),
    "SSH_PORT": int(env("LINUX_SSH_PORT", "22")),
    "SSH_USER": env("LINUX_SSH_USER", "mailboxadm"),
    # Use a private key file (recommended) or a password.
    "SSH_KEY_FILE": env("LINUX_SSH_KEY_FILE", ""),
    "SSH_KEY_PASSPHRASE": env("LINUX_SSH_KEY_PASSPHRASE", ""),
    "SSH_PASSWORD": env("LINUX_SSH_PASSWORD", ""),
    # Server identity check: a known_hosts file, or the SHA256 fingerprint shown by
    # `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server. If neither is set the
    # key is trusted on first use and the admin panel shows a warning.
    "SSH_KNOWN_HOSTS": env("LINUX_SSH_KNOWN_HOSTS", ""),
    "SSH_HOST_FINGERPRINT": env("LINUX_SSH_HOST_FINGERPRINT", ""),
    "HELPER": env("LINUX_HELPER", "/usr/local/sbin/mailbox-user"),
    "USE_SUDO": env_bool("LINUX_USE_SUDO", True),
    "DEFAULT_SHELL": env("LINUX_DEFAULT_SHELL", "/usr/sbin/nologin"),
    "DEFAULT_GROUPS": env("LINUX_DEFAULT_GROUPS", ""),
    "TIMEOUT": int(env("LINUX_SSH_TIMEOUT", "20")),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
