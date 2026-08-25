import os

try:
    from .pwd import mysql_pwd
except ImportError as exc:  # pragma: no cover - exercised on a fresh checkout
    # pwd.py is gitignored, so a fresh clone has no credentials and every
    # import of libraries.db died here with a bare ModuleNotFoundError that
    # said nothing about what to do. Fail with instructions instead.
    raise ImportError(
        "libraries/db/pwd.py is missing — it holds your MySQL password and is "
        "gitignored, so a fresh clone never has it.\n\n"
        "    cp libraries/db/pwd.py.example libraries/db/pwd.py\n"
        "    # then edit it and set mysql_pwd\n\n"
        "See deploy/README.md ('Setting up on a new machine') for the full "
        "checklist, including the database itself."
    ) from exc

dbcfg = {"user": os.environ.get("WAKE_DB_USER", "boone"),
         "password": mysql_pwd,
         "host": os.environ.get("WAKE_DB_HOST", "127.0.0.1"),
         "database": os.environ.get("WAKE_DB_NAME", "portfolio")}
