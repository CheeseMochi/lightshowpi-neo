"""
Admin command line for the LightShowPi Neo API.

    python -m api.manage set-password admin            # prompts twice
    echo 'new-pass' | python -m api.manage set-password admin --stdin

``set-password`` creates the user if it doesn't exist, so it also covers a
first run without $ADMIN_PASSWORD and a forgotten password. Run it from the
repo root in the lightshowpi-neo conda env, as the user the API service runs
as (not with sudo, so the database stays owned by that user). The API doesn't
need to be stopped.
"""

import argparse
import getpass
import sys
from typing import List, Optional

from api.core.auth import AuthManager, password_problem
from api.core.config import get_api_config
from api.core.database import init_database


def read_password(use_stdin: bool) -> str:
    """Read the new password from stdin or an interactive prompt (twice)."""
    if use_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat password: ") != first:
        raise ValueError("Passwords don't match")
    return first


def set_password(db, username: str, password: str) -> str:
    """Set ``username``'s password, creating the user if needed. Returns "updated" or "created"."""
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    auth_manager = AuthManager(db)
    if auth_manager.set_password(username, password):
        return "updated"
    auth_manager.create_user(username, password)
    return "created"


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="python -m api.manage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("set-password", help="set (or create) a user's password")
    sp.add_argument("username")
    sp.add_argument("--stdin", action="store_true",
                    help="read the password from the first line of stdin")
    sp.add_argument("--db", help="database path (default: [api] db_path, else api/data)")
    args = parser.parse_args(argv)

    db_path = args.db or get_api_config().db_path or None
    try:
        password = read_password(args.stdin)
        result = set_password(init_database(db_path), args.username, password)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Password {result} for user '{args.username}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
