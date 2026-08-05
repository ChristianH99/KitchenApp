from django.apps import AppConfig
from django.db.backends.signals import connection_created


class RecipesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recipes"
    label = "recipes"

    def ready(self):
        # Connected here rather than with an @receiver decorator: ready() is
        # the one point Django guarantees runs once, after the app registry is
        # populated. dispatch_uid so an autoreload that re-imports this module
        # does not stack a second copy of the handler onto every connection.
        connection_created.connect(_configure_sqlite, dispatch_uid="recipes.sqlite_pragmas")


def _configure_sqlite(sender, connection, **kwargs):
    """WAL mode and a busy timeout, per connection.

    Both are per-*connection* PRAGMAs in SQLite, so setting them once at
    migrate time does nothing for the connections the server actually serves
    from. WAL is what lets somebody reading a recipe on a phone not block
    somebody saving one on a laptop; without it the reader holds a shared lock
    and the writer comes back as "database is locked", which on a NAS under a
    slow disk is not as rare as it sounds.

    ``synchronous=NORMAL`` is the WAL-mode recommendation: an OS crash can cost
    the last transaction, a *process* crash cannot. On a NAS with a UPS and a
    nightly Hyper Backup, that is the right trade for not fsyncing on every
    write.
    """
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=20000;")
        # Off by default in SQLite, which means an on_delete=CASCADE that
        # Django did not itself emit is silently not enforced.
        cursor.execute("PRAGMA foreign_keys=ON;")
