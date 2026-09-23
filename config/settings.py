"""Django settings for the MaterialInventory project."""

from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = config('SECRET_KEY', default='django-insecure-m$#m4d@5-56f+y6cs#3f5yi-$1%-vl#o0uy^=fiw2w5z9ek^wj')

DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Required behind a TLS-terminating proxy, scheme included (`https://host`),
# or every POST fails CSRF with "Origin checking failed".
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())


# HTTPS settings default to off: the test runner forces DEBUG=False, so a
# hardcoded SECURE_SSL_REDIRECT would turn every test request into a 301.
# Production opts in via .env.production.
#
# SECURE_PROXY_SSL_HEADER is safe unconditionally only while `web` publishes no
# port and Caddy overwrites X-Forwarded-Proto; otherwise the header is forgeable.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=False, cast=bool)

# One variable for both cookies; a half-set pair shows up as 403s on every form.
SESSION_COOKIE_SECURE = config('SECURE_COOKIES', default=False, cast=bool)
CSRF_COOKIE_SECURE = config('SECURE_COOKIES', default=False, cast=bool)

# Browsers honour HSTS for the full max-age whatever the server later sends, so
# start short. INCLUDE_SUBDOMAINS on an apex domain covers every company subdomain.
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False, cast=bool)

# Off on purpose (a company-domain decision); `check --deploy` reports it as W021.
SECURE_HSTS_PRELOAD = config('SECURE_HSTS_PRELOAD', default=False, cast=bool)


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'materials',
    'machines',
    'locations',
    'workorders',
    'axes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # 404s /admin/ unless the session is an admin's, so the admin login form is
    # never served on the public internet. Below SessionMiddleware, which it
    # reads the user through, and deliberately *above* CommonMiddleware, whose
    # APPEND_SLASH would otherwise 301 /admin to /admin/ and confirm the admin
    # is there. It resolves the user itself; see the module docstring.
    'accounts.middleware.AdminSessionRequiredMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Must be last: it swaps the login response for the lockout page.
    'axes.middleware.AxesMiddleware',
]

AUTH_USER_MODEL = 'accounts.User'

# Axes first: it only refuses locked-out requests. It defines no `get_user`, so
# the tests' `force_login()` resolves to ModelBackend. Don't use `client.login()`,
# which axes rejects.
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'transform_create'
LOGOUT_REDIRECT_URL = 'login'

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='materialinventory'),
        'USER': config('DB_USER', default='materialinventory'),
        'PASSWORD': config('DB_PASSWORD', default='materialinventory'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# django-axes: login rate limiting.
#
# Lock by username, not IP: the depot shares one NAT address and, behind the
# proxy, unconfigured clients all look alike — IP locking would lock out everyone.
AXES_LOCKOUT_PARAMETERS = ['username']

# W006 objects to not locking by IP (see above). Silenced so `check --deploy`
# reports only W021.
SILENCED_SYSTEM_CHECKS = ['axes.W006']

AXES_FAILURE_LIMIT = 5

# templates/registration/lockout.html quotes "30 minut" — change both together.
AXES_COOLOFF_TIME = timedelta(minutes=30)

AXES_RESET_ON_SUCCESS = True

AXES_LOCKOUT_TEMPLATE = 'registration/lockout.html'
AXES_COOLOFF_MESSAGE = 'Účet je dočasně uzamčen po opakovaných neúspěšných přihlášeních. Zkuste to prosím později.'
AXES_PERMALOCK_MESSAGE = 'Účet je uzamčen po opakovaných neúspěšných přihlášeních. Obraťte se na správce.'

# Axes has no Czech translation. Read attempts with `manage.py axes_list_attempts`.
AXES_ENABLE_ADMIN = False

# Record the client IP from X-Forwarded-For instead of the proxy's. Audit log
# only — locking is by username. Off without a proxy, where the header is forgeable.
BEHIND_PROXY = config('BEHIND_PROXY', default=False, cast=bool)
if BEHIND_PROXY:
    AXES_IPWARE_PROXY_COUNT = 1
    AXES_IPWARE_META_PRECEDENCE_ORDER = ('HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR')


# Makes Django's own strings Czech; app copy is hardcoded. No LocaleMiddleware,
# so the language is fixed rather than negotiated.
LANGUAGE_CODE = 'cs'

# Czech for Django strings its own catalogs lack. Read from the compiled .mo.
LOCALE_PATHS = [BASE_DIR / 'locale']

# Rendering only; the database stores UTC.
TIME_ZONE = 'Europe/Prague'

USE_I18N = True

USE_TZ = True


STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Plain storage by default: manifest storage raises without `collectstatic`, and
# tests run with DEBUG=False. The Dockerfile sets STATICFILES_BACKEND for production.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': config('STATICFILES_BACKEND', default='django.contrib.staticfiles.storage.StaticFilesStorage'),
    },
}

# With DEBUG=False and no ADMINS, Django logs an unhandled 500 nowhere. ERROR,
# not WARNING, so the suite's many expected 403s stay quiet.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
