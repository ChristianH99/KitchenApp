"""Encrypting the one secret this app stores.

The OIDC client secret lives in the database so it can be edited from a page
(apps/accounts/models.py says why that trade was made). Encrypting it at rest
buys exactly one thing, and it is worth being precise about which:

**A copy of the database is not enough.** `/data` is backed up nightly by Hyper
Backup to wherever that share goes, and a `db.sqlite3` sitting in a backup — or
handed to somebody to look at a bug — would otherwise carry a credential that
lets its holder impersonate this app to the SSO server. With this, opening it
also needs ``DJANGO_SECRET_KEY``, which is only ever in the environment.

**It is not protection from someone who has the running system.** They have the
environment, so they have the key. Anyone who can read `/proc` or run
`docker exec` can read the secret, and no amount of key derivation changes that.
Encryption at rest is about the copies, not about the original.

The key is *derived* from ``DJANGO_SECRET_KEY`` rather than being a second thing
to configure, because a second secret is a second thing to lose — and losing it
would mean an SSO login that fails with no way to fix it except re-typing the
secret, which is exactly what happens here anyway when the key changes. That
failure is handled rather than hidden: ``decrypt`` returns "" and the settings
page says the stored secret can no longer be read and asks for it again.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

log = logging.getLogger(__name__)

# Mixed into the derivation so this key cannot collide with any other use of
# SECRET_KEY (session signing, password reset tokens). Changing it would make
# every stored secret unreadable, which is a migration, not an edit.
PURPOSE = "kitchen.sso.client-secret.v1"


def _fernet():
    digest = hashlib.sha256(f"{PURPOSE}:{settings.SECRET_KEY}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(raw):
    """A Fernet token for ``raw``, or "" for an empty value."""
    if not raw:
        return ""
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt(token):
    """The secret behind ``token``, or "" when it cannot be read.

    Deliberately not an exception. The realistic cause of failure is a rotated
    ``DJANGO_SECRET_KEY``, and this is called while rendering the login page —
    so raising would turn "SSO needs reconfiguring" into "the app is down",
    including for the local account that exists to fix it.
    """
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        log.warning(
            "The stored OIDC client secret cannot be decrypted — DJANGO_SECRET_KEY "
            "has almost certainly changed. Re-enter it on the SSO settings page."
        )
        return ""
