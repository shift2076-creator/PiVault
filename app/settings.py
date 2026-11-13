import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('PIVAULT_DATA', BASE_DIR / 'data'))
DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
SECRET_KEY = os.environ['PIVAULT_SECRET_KEY']
DEBUG = False
# The one public address of the portal (Caddy site https://192.168.4.90).
PUBLIC_ORIGIN = 'https://192.168.4.90'
ALLOWED_HOSTS = ['192.168.4.90', 'localhost', '127.0.0.1']
ROOT_URLCONF = 'urls'
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'portal',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'portal.middleware.PrivateResponseMiddleware',
]
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'portal' / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
    ]},
}]
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'users.sqlite3',
    'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'},
}}
AUTH_USER_MODEL = 'portal.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'America/New_York'
LOGIN_URL = '/users/sign-in'
SESSION_COOKIE_NAME = '__Host-pivault'
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_NAME = '__Host-pivault-csrf'
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = [PUBLIC_ORIGIN]
# Only use behind a private, trusted Caddy upstream which overwrites this header.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 131072
JOIN_URL = PUBLIC_ORIGIN + '/join'
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
PIVAULT_NOTIFICATION_ROOT = os.environ.get('PIVAULT_NOTIFICATION_ROOT', '/srv/pi-server/config/notifications')
PIVAULT_CLIENT_ROOT = os.environ.get('PIVAULT_CLIENT_ROOT', DATA_DIR / 'client-packages')
PIVAULT_NATIVE_CONFIG = os.environ.get('PIVAULT_NATIVE_CONFIG', '')

# Structured setup stages go directly to docker logs, without request bodies.
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'formatters': {'setup': {'format': '%(asctime)s %(levelname)s %(message)s'}},
    'handlers': {'setup_console': {'class': 'logging.StreamHandler', 'formatter': 'setup', 'stream': 'ext://sys.stderr'}},
    'loggers': {'portal.setup': {'handlers': ['setup_console'], 'level': 'INFO', 'propagate': False}},
}

SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365 * 10  # No practical inactivity expiry, as before.
SESSION_SAVE_EVERY_REQUEST = False
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
