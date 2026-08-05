"""Django settings for the kitchen app.

The app runs in one container on a Synology NAS, behind that NAS's own reverse
proxy, and authenticates its users against the Synology SSO Server over OIDC.
Three consequences run through this file:

* **It never terminates TLS itself.** The proxy does, and talks plain HTTP to
  this process. So the HTTPS settings below are about *trusting the proxy's
  headers* rather than about redirecting — see the proxy block.
* **Everything it writes lives under DATA_DIR**, never beside the code, because
  the code is replaced wholesale by the next image and the database is the
  recipes. A checkout keeps DATA_DIR at the repository root, which is what a
  developer expects; the container points it at a bind mount.
* **Secrets come from the environment.** ``.env.example`` documents every one of
  them; nothing here loads a .env file automatically.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Everything written at runtime: db.sqlite3, media/ (recipe photographs), logs/,
# the development signing key. Anything a new feature writes belongs under here
# too — config/tests.py::TestWritablePathsStayUnderDataDir holds that line.
DATA_DIR = Path(os.environ.get('KITCHEN_DATA_DIR') or BASE_DIR)


def _env_bool(name, default=False):
    """Read a boolean from the environment ("1/true/yes/on" -> True)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_list(name):
    """Read a comma-separated list from the environment (empties dropped)."""
    return [item.strip() for item in os.environ.get(name, '').split(',') if item.strip()]


def _env_int(name, default):
    """Read an integer from the environment, falling back on anything unreadable."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# --- Core ---------------------------------------------------------------

DEBUG = _env_bool('DJANGO_DEBUG', default=True)

# Which build this is, baked into the image by deploy/Dockerfile from the
# release tag. Empty in a checkout, and empty is shown as nothing at all rather
# than as "dev" or "unknown" — a version string on a page is a claim, and the
# only honest claim a working copy can make is silence.
#
# Deliberately *not* part of /healthz, which is unauthenticated: the version of
# the software you are running is exactly the sort of thing an unauthenticated
# endpoint should not volunteer. It is shown in the sidebar, behind the login.
KITCHEN_VERSION = os.environ.get('KITCHEN_VERSION', '').strip()
if KITCHEN_VERSION in ('dev', 'unknown'):
    KITCHEN_VERSION = ''

DEV_SECRET_KEY_FILE = DATA_DIR / '.secret_key'


def _development_secret_key():
    """A signing key for *this checkout*, generated once and kept in DATA_DIR.

    Deliberately not a literal in this file. A key committed to a repository is
    a public key: anyone who can read it can forge a session cookie for any
    account. It is a *development* key either way and is never consulted with
    DEBUG off — a deployment must set DJANGO_SECRET_KEY, enforced below.
    """
    try:
        existing = DEV_SECRET_KEY_FILE.read_text(encoding='utf-8').strip()
        if existing:
            return existing
    except OSError:
        pass
    # stdlib only: this runs while the settings module is still executing, so
    # nothing here may reach back into django.conf.
    import secrets as _secrets

    key = _secrets.token_urlsafe(48)
    try:
        DEV_SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEV_SECRET_KEY_FILE.write_text(key, encoding='utf-8')
        os.chmod(DEV_SECRET_KEY_FILE, 0o600)  # best effort; a no-op on Windows
    except OSError:
        # A read-only checkout: a key that lasts one process still beats a
        # shared one. Sessions won't survive a restart, which is a development
        # problem and nothing more.
        pass
    return key


_ENV_SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '').strip()

if _ENV_SECRET_KEY:
    SECRET_KEY = _ENV_SECRET_KEY
elif DEBUG:
    SECRET_KEY = _development_secret_key()
else:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is not set and DEBUG is False. Generate one with:\n'
        '  python -c "from django.core.management.utils import '
        'get_random_secret_key as g; print(g())"'
    )

# The hostnames this site answers to — "kitchen.haeusslerr.de" in the real
# deployment. Required once DEBUG is off; refused at startup (config/wsgi.py)
# rather than here, because collectstatic legitimately runs with DEBUG off and
# no hosts at all.
ALLOWED_HOSTS = _env_list('DJANGO_ALLOWED_HOSTS')
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']

# Origins allowed to send authenticated POSTs, e.g. "https://kitchen.haeusslerr.de".
# Django compares the Origin header against this list for every unsafe method, so
# without it every form on the real host comes back as a CSRF failure.
CSRF_TRUSTED_ORIGINS = _env_list('DJANGO_CSRF_TRUSTED_ORIGINS')


# --- Applications -------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'mozilla_django_oidc',
    'apps.accounts',
    'apps.recipes',
]

# Send the Content-Security-Policy as Report-Only (nothing blocked, violations
# logged to the browser console). For finding out what a new page broke without
# breaking it — not a setting to deploy with. See config/csp.py.
CSP_REPORT_ONLY = _env_bool('DJANGO_CSP_REPORT_ONLY', default=False)

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serves everything under STATIC_ROOT from this process. `runserver` stops
    # serving /static/ once DEBUG is off, so without this the container renders
    # every page unstyled. Must sit directly below SecurityMiddleware.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # Below WhiteNoise (a static file needs no policy and WhiteNoise answers it
    # without going further), above everything that renders a page.
    'config.csp.ContentSecurityPolicyMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # Resolves the active language (session -> cookie -> Accept-Language ->
    # LANGUAGE_CODE). After SessionMiddleware, before CommonMiddleware.
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Refreshes the OIDC access token in the background and signs the user out
    # once the provider says the session is over. Must sit after
    # AuthenticationMiddleware, which is where request.user comes from.
    'apps.accounts.middleware.OIDCSessionRefresh',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Login required everywhere except the handful of URLs in apps/accounts/pages.py.
    'apps.accounts.middleware.LoginRequiredMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

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
                # Which sidebar entry is the current page (apps/nav.py) — worked
                # out from the resolved (app, url_name) pair rather than by
                # comparing url_name in the template, which marks two entries at
                # once as soon as two apps use the same name.
                'apps.nav.context',
                # Which build is running, printed in the sidebar footer. The
                # first question after every update, and the one the NAS cannot
                # answer — see apps/version.py.
                'apps.version.context',
            ],
        },
    },
]


# --- Behind the Synology reverse proxy ----------------------------------
#
# The proxy terminates TLS and forwards plain HTTP to this container. Django
# therefore sees `http` on every request unless it is told to believe the
# proxy's headers — and *that* is what breaks an OIDC login rather than merely
# looking untidy: the redirect_uri this app sends to the SSO server is built
# from the request, so without these two settings it goes out as
# `http://kitchen.haeusslerr.de/...`, which the SSO server rejects as an
# unregistered callback. The symptom is a login loop with no error message.
#
# Both require the proxy to actually set the headers. In DSM the rule needs
# custom headers `X-Forwarded-Proto: $scheme` and `Host: $host` — see
# DEPLOYMENT.md §2. Turn this off only if something else terminates TLS
# differently; on a direct connection a forwarded header is a client-supplied
# value and must not be trusted.
TRUST_PROXY_HEADERS = _env_bool('DJANGO_TRUST_PROXY_HEADERS', default=not DEBUG)

if TRUST_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

if not DEBUG:
    # No SECURE_SSL_REDIRECT here: the proxy is the only way in and it already
    # speaks HTTPS, so a redirect issued by this process would only ever fire
    # for the container's own health check on 127.0.0.1 and turn a healthy
    # server into a failing probe.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False  # the recipe pages read the CSRF cookie
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    X_FRAME_OPTIONS = 'DENY'
    # HSTS pins the hostname to HTTPS in every browser that saw the header and
    # cannot be revoked before it expires. Short by default; raise it once the
    # certificate renewal has survived a cycle or two unattended.
    SECURE_HSTS_SECONDS = _env_int('DJANGO_HSTS_SECONDS', 3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('DJANGO_HSTS_INCLUDE_SUBDOMAINS', False)
    SECURE_HSTS_PRELOAD = _env_bool('DJANGO_HSTS_PRELOAD', False)
    if not SECURE_HSTS_PRELOAD:
        # security.W021 asks for preloading; on a domain whose other subdomains
        # are DSM and Home Assistant that is a decision to take deliberately,
        # not a default. Silenced so `check --deploy` stays clean.
        SILENCED_SYSTEM_CHECKS = ['security.W021']


# --- Authentication -----------------------------------------------------
#
# Synology SSO is the way in. A local password is the way *back* in when SSO,
# its certificate or the container's DNS is unhappy — an OIDC-only app locks
# out its own administrator at exactly the moment somebody needs to fix it. The
# local backend is second, so an account that exists in both places is resolved
# by the provider first.
AUTHENTICATION_BACKENDS = [
    'apps.accounts.oidc.SynologyOIDCBackend',
    'django.contrib.auth.backends.ModelBackend',
]

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = 'accounts:login'

# Whether the "Sign in with Synology" button is offered at all. Off by default
# so a fresh checkout runs on the local login with nothing configured; the
# deployment sets it along with the three OIDC values below.
OIDC_ENABLED = _env_bool('OIDC_ENABLED', default=False)

OIDC_RP_CLIENT_ID = os.environ.get('OIDC_RP_CLIENT_ID', '')
OIDC_RP_CLIENT_SECRET = os.environ.get('OIDC_RP_CLIENT_SECRET', '')
OIDC_RP_SIGN_ALGO = os.environ.get('OIDC_RP_SIGN_ALGO', 'RS256')
OIDC_RP_SCOPES = os.environ.get('OIDC_RP_SCOPES', 'openid profile email')

# The four provider endpoints. Synology publishes them at
# <sso>/webman/sso/.well-known/openid-configuration — but the exact paths have
# moved between DSM versions, so they are configured rather than guessed, and
# DEPLOYMENT.md §3 tells you to read them off the discovery document first
# instead of trusting anything written here.
OIDC_OP_BASE = os.environ.get('OIDC_OP_BASE', '').rstrip('/')
OIDC_OP_AUTHORIZATION_ENDPOINT = os.environ.get(
    'OIDC_OP_AUTHORIZATION_ENDPOINT', f'{OIDC_OP_BASE}/webman/sso/SSOOauth.cgi' if OIDC_OP_BASE else '')
OIDC_OP_TOKEN_ENDPOINT = os.environ.get(
    'OIDC_OP_TOKEN_ENDPOINT', f'{OIDC_OP_BASE}/webman/sso/SSOAccessToken.cgi' if OIDC_OP_BASE else '')
OIDC_OP_USER_ENDPOINT = os.environ.get(
    'OIDC_OP_USER_ENDPOINT', f'{OIDC_OP_BASE}/webman/sso/SSOUserInfo.cgi' if OIDC_OP_BASE else '')
OIDC_OP_JWKS_ENDPOINT = os.environ.get('OIDC_OP_JWKS_ENDPOINT', '')

# Where the provider sends the browser back to. Registered in the SSO server's
# client configuration; the two must match exactly, including the trailing slash.
OIDC_AUTHENTICATION_CALLBACK_URL = 'oidc_authentication_callback'

# How often the SessionRefresh middleware re-checks the provider. 15 minutes:
# often enough that an account disabled in DSM loses access the same afternoon,
# rare enough that a browser left open on the recipe list is not talking to the
# SSO server all day.
OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS = _env_int('OIDC_RENEW_SECONDS', 15 * 60)

# Synology's DSM certificate. Left as True (verify), because the whole point of
# putting these hosts behind Let's Encrypt is that verification works. Set to
# the path of a CA bundle if the SSO host is reached over an internal name with
# a private CA; never set it to False.
OIDC_VERIFY_SSL = _env_bool('OIDC_VERIFY_SSL', default=True)

# Which DSM users may sign in, by claim. Empty means "anybody the SSO server
# authenticates", which is right for a household and wrong the moment the SSO
# server also serves guests — see apps/accounts/oidc.py.
OIDC_ALLOWED_GROUPS = _env_list('OIDC_ALLOWED_GROUPS')
# The claim those groups are read from. Synology has moved this between DSM
# versions and some builds omit it entirely; the app works either way and this
# is why the check above is opt-in.
OIDC_GROUPS_CLAIM = os.environ.get('OIDC_GROUPS_CLAIM', 'groups')
# A member of this group becomes a Django staff user (Django admin access).
OIDC_STAFF_GROUP = os.environ.get('OIDC_STAFF_GROUP', '')

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --- Login throttling (apps/accounts/throttle.py) ---
# Only the *local* login is throttled; the SSO login is Synology's to rate-limit.
LOGIN_MAX_ATTEMPTS = _env_int('DJANGO_LOGIN_MAX_ATTEMPTS', 10)
LOGIN_MAX_ATTEMPTS_PER_HOST = _env_int('DJANGO_LOGIN_MAX_ATTEMPTS_PER_HOST', 50)
LOGIN_LOCKOUT_SECONDS = _env_int('DJANGO_LOGIN_LOCKOUT_SECONDS', 300)

# A week. Long enough that the family is not signing in every day, short enough
# that a phone left on a train is not a permanent session.
SESSION_COOKIE_AGE = _env_int('DJANGO_SESSION_DAYS', 7) * 24 * 3600

# Local-memory cache, stated rather than left to the default: the login throttle
# keeps its counters here. One process per container, so per-process is
# per-system; a restart forgets the counters, which is acceptable.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'kitchenapp',
    }
}


# --- Database -----------------------------------------------------------
#
# SQLite, deliberately. A household's recipe collection is a few thousand rows
# read by a handful of people; Postgres would cost a second container and its
# RAM on a NAS whose whole brief is being frugal, and moving to it later is a
# dumpdata/loaddata, not a rewrite. WAL mode is set per connection in
# apps/recipes/apps.py so a reader never blocks the writer.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DATA_DIR / 'db.sqlite3',
        'OPTIONS': {'timeout': 20},
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Internationalisation -----------------------------------------------

from django.utils.translation import gettext_lazy as _  # noqa: E402

# The household speaks German, so German is what an unconfigured visitor gets.
# English is offered from the topbar globe (django.views.i18n.set_language).
LANGUAGE_CODE = 'de'

LANGUAGES = [
    ('de', _('German')),
    ('en', _('English')),
]

LOCALE_PATHS = [BASE_DIR / 'locale']

USE_I18N = True
USE_TZ = True


def _local_time_zone():
    """The zone this machine keeps, because that is the clock people read.

    Every timestamp shown — when a recipe was added, when it was last changed —
    is read against a wall clock in the same house, so a default of UTC is
    simply wrong by an hour or two all summer in a way that looks right.
    Windows has no IANA key (it reports a localised display name), which is what
    tzlocal is a dependency for. DJANGO_TIME_ZONE overrides it outright.
    """
    if (explicit := os.environ.get('DJANGO_TIME_ZONE')):
        return explicit
    try:
        import tzlocal

        return tzlocal.get_localzone_name() or 'UTC'
    except Exception:  # noqa: BLE001 - no zone is worth failing a boot over
        return 'UTC'


TIME_ZONE = _local_time_zone()


# --- Static and media ---------------------------------------------------

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = Path(os.environ.get('DJANGO_STATIC_ROOT') or BASE_DIR / 'staticfiles')

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Hashes each file's contents into its name, so a changed stylesheet can be
    # cached forever and still never go stale in somebody's browser. The
    # manifest is also strict: a {% static %} pointing at a file that does not
    # exist becomes a failed test rather than a dead page. That is why
    # `collectstatic` is a prerequisite of the test suite here, not only of a
    # deployment — see README.md.
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# Recipe photographs. Written at runtime, so WhiteNoise (which indexes
# STATIC_ROOT once at startup) cannot serve them — config/media.py does, behind
# the login, because a photograph of somebody's kitchen is not public.
MEDIA_URL = 'media/'
MEDIA_ROOT = Path(os.environ.get('DJANGO_MEDIA_ROOT') or DATA_DIR / 'media')
SERVE_MEDIA = _env_bool('DJANGO_SERVE_MEDIA', default=True)

# The longest edge an uploaded photograph is resized to before it is stored, and
# the cap on the upload itself. A phone camera produces 4-6 MB files that are
# displayed at 800px; storing them whole fills a NAS share with pixels nobody
# sees and makes the recipe list slow over mobile data.
IMAGE_MAX_EDGE = _env_int('KITCHEN_IMAGE_MAX_EDGE', 1600)
IMAGE_MAX_UPLOAD_BYTES = _env_int('KITCHEN_IMAGE_MAX_UPLOAD_BYTES', 12 * 1024 * 1024)


# --- Logging ------------------------------------------------------------
#
# A rotating file under DATA_DIR (which survives an image update) alongside the
# console, because a container's stdout is gone as soon as it is recreated and
# "who signed in and who was refused" is the one thing worth still having.

LOG_DIR = Path(os.environ.get('DJANGO_LOG_DIR') or DATA_DIR / 'logs')
LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO').upper()

try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_file_ok = True
except OSError:
    # An unwritable data directory must not stop the server coming up; the
    # console handler still works.
    _log_file_ok = False

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'app': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'app'},
        **({'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'kitchenapp.log'),
            'maxBytes': 2 * 1024 * 1024,
            'backupCount': 3,
            'encoding': 'utf-8',
            'formatter': 'app',
        }} if _log_file_ok else {}),
    },
    'root': {
        'handlers': ['console', *(['file'] if _log_file_ok else [])],
        'level': LOG_LEVEL,
    },
    'loggers': {
        # Django's request logger is noisy about 404s and says nothing this app
        # needs; its own errors still reach the root handlers.
        'django': {'level': 'WARNING'},
        # Who signed in, who was refused, and every OIDC exchange that failed —
        # the three questions asked when somebody cannot get in.
        'apps.accounts': {'level': 'INFO'},
        'mozilla_django_oidc': {'level': 'INFO'},
    },
}
