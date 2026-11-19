"""Generate ADMIN_PASSWORD_HASH for the .env file.

    python -m app.hashpw
    docker compose run --rm --no-deps app python -m app.hashpw

The password is read without echo and never stored. The output is single-quoted
because the hash contains '$', which Docker Compose would otherwise try to
expand as a variable.
"""

from __future__ import annotations

import getpass
import sys

from werkzeug.security import generate_password_hash

MIN_LENGTH = 12


def main() -> int:
    password = getpass.getpass("New admin password: ")
    if len(password) < MIN_LENGTH:
        print(f"Password must be at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords did not match.", file=sys.stderr)
        return 1
    print(f"ADMIN_PASSWORD_HASH='{generate_password_hash(password)}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
