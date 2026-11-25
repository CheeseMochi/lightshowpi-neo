"""
Authentication and authorization for LightShowPi Neo API.

Provides secure password hashing, JWT token generation, and
IP-based access control.
"""

import os
import secrets
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import json

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from fastapi import HTTPException, Request, Security, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

log = logging.getLogger(__name__)

# JWT configuration. SECRET_KEY is replaced by configure_secret_key() when the
# API starts; the import-time value only keeps imports (and tests) working.
SECRET_KEY = os.getenv("JWT_SECRET_KEY") or secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 8
# Passwords that were published as defaults and must never be accepted
FORBIDDEN_PASSWORDS = {"admin123"}

# auto_error=False so a missing token is a 401 (not FastAPI's version-dependent
# 403); the frontend and the button manager both treat 401 as "log in again".
security = HTTPBearer(auto_error=False)


def dev_mode() -> bool:
    """True when LSP_DEV=1: development conveniences (reload, Vite CORS, throwaway JWT key)."""
    return os.getenv("LSP_DEV", "").strip().lower() in ("1", "true", "yes")


def configure_secret_key() -> str:
    """
    Load the JWT signing key from $JWT_SECRET_KEY, called once at API startup.

    The key must be set (at least 32 characters) so that tokens survive a
    restart and can't be forged. Ansible writes it to /etc/lightshowpi/api.env.
    With LSP_DEV=1 a missing key is replaced by a random one, with a warning.

    Raises:
        RuntimeError: If the key is missing or too short outside dev mode
    """
    global SECRET_KEY
    key = os.getenv("JWT_SECRET_KEY", "").strip()
    if len(key) >= MIN_SECRET_LENGTH:
        SECRET_KEY = key
        return SECRET_KEY
    if dev_mode():
        log.warning("JWT_SECRET_KEY is not set (or shorter than %d characters); "
                    "LSP_DEV=1, so using a random key. Logins end when the API restarts.",
                    MIN_SECRET_LENGTH)
        SECRET_KEY = secrets.token_urlsafe(48)
        return SECRET_KEY
    raise RuntimeError(
        f"JWT_SECRET_KEY must be set to at least {MIN_SECRET_LENGTH} characters "
        "(e.g. in /etc/lightshowpi/api.env; generate one with "
        "`python -c 'import secrets; print(secrets.token_urlsafe(48))'`). "
        "For local development set LSP_DEV=1 instead.")


def password_problem(password: str) -> Optional[str]:
    """Return why ``password`` is unacceptable, or None if it is fine."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if password.lower() in FORBIDDEN_PASSWORDS:
        return "That password is a published default; choose another"
    return None


class AuthManager:
    """Manages authentication and authorization."""

    def __init__(self, db):
        """
        Initialize auth manager.

        Args:
            db: Database instance
        """
        self.db = db

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            str: Hashed password
        """
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.

        Args:
            plain_password: Plain text password
            hashed_password: Hashed password from database

        Returns:
            bool: True if password matches
        """
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )

    def create_user(self, username: str, password: str, allowed_ips: Optional[List[str]] = None) -> int:
        """
        Create a new user.

        Args:
            username: Unique username
            password: Plain text password (will be hashed)
            allowed_ips: Optional list of allowed IP addresses

        Returns:
            int: User ID

        Raises:
            ValueError: If username already exists
        """
        password_hash = self.hash_password(password)
        allowed_ips_json = json.dumps(allowed_ips) if allowed_ips else None

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO users (username, password_hash, allowed_ips)
                    VALUES (?, ?, ?)
                """, (username, password_hash, allowed_ips_json))
                user_id = cursor.lastrowid
                log.info(f"Created user: {username} (ID: {user_id})")
                return user_id
            except sqlite3.IntegrityError:
                raise ValueError(f"Username '{username}' already exists")

    def set_password(self, username: str, new_password: str) -> bool:
        """
        Replace a user's password (no check of the old one: callers do that).

        Args:
            username: Existing username
            new_password: New plain text password (will be hashed)

        Returns:
            bool: True if the user existed and was updated

        Raises:
            ValueError: If the new password fails password_problem()
        """
        problem = password_problem(new_password)
        if problem:
            raise ValueError(problem)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                           (self.hash_password(new_password), username))
            updated = cursor.rowcount > 0
        if updated:
            log.info(f"Password changed for user: {username}")
        return updated

    def authenticate_user(self, username: str, password: str, client_ip: Optional[str] = None) -> Optional[dict]:
        """
        Authenticate a user.

        Args:
            username: Username
            password: Plain text password
            client_ip: Optional client IP for IP whitelist check

        Returns:
            dict: User data if authenticated, None otherwise
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, username, password_hash, allowed_ips
                FROM users
                WHERE username = ?
            """, (username,))
            user = cursor.fetchone()

        if not user:
            log.warning(f"Authentication failed: user not found: {username}")
            return None

        if not self.verify_password(password, user['password_hash']):
            log.warning(f"Authentication failed: invalid password for user: {username}")
            return None

        # Check IP whitelist if configured
        if user['allowed_ips']:
            allowed_ips = json.loads(user['allowed_ips'])
            if client_ip and client_ip not in allowed_ips:
                log.warning(f"Authentication failed: IP {client_ip} not in whitelist for user: {username}")
                return None

        log.info(f"User authenticated: {username}")
        return {
            'id': user['id'],
            'username': user['username'],
            'allowed_ips': json.loads(user['allowed_ips']) if user['allowed_ips'] else None
        }

    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a JWT access token.

        Args:
            data: Data to encode in token
            expires_delta: Optional expiration time

        Returns:
            str: JWT token
        """
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt

    @staticmethod
    def decode_token(token: str) -> dict:
        """
        Decode and validate a JWT token.

        Args:
            token: JWT token

        Returns:
            dict: Token payload

        Raises:
            HTTPException: If token is invalid or expired
        """
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:
            log.error(f"Unexpected error decoding token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @staticmethod
    def generate_client_key() -> str:
        """
        Generate a secure random key for client authentication.

        Returns:
            str: Random key
        """
        return secrets.token_urlsafe(32)


def ensure_admin_user(auth_manager: "AuthManager", db) -> Optional[str]:
    """
    Create the first user (``admin``) on a fresh database.

    The password comes from $ADMIN_PASSWORD and is never logged. There is no
    built-in fallback: with no (or an unacceptable) ADMIN_PASSWORD no user is
    created and an error explains how to set one, so the API never runs with a
    published default password.

    Returns:
        str: "created", "exists" or "missing" (no user could be created)
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] > 0:
            return "exists"

    password = os.getenv("ADMIN_PASSWORD", "")
    problem = password_problem(password) if password else "ADMIN_PASSWORD is not set"
    if problem:
        log.error("No API users exist and no admin was created: %s. Set ADMIN_PASSWORD "
                  "(e.g. in /etc/lightshowpi/api.env) and restart, or run "
                  "`python -m api.manage set-password admin`.", problem)
        return "missing"

    auth_manager.create_user("admin", password)
    log.warning("Created the admin user from ADMIN_PASSWORD "
                "(it is only read when the database has no users)")
    return "created"


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> dict:
    """
    Dependency for getting the current authenticated user.

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        dict: Current user data

    Raises:
        HTTPException: If authentication fails
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = AuthManager.decode_token(token)  # Use static method
    username = payload.get("sub")

    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {"username": username, "id": payload.get("user_id")}


AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Per-command test mode routes fire many times a minute; test mode enter/exit
# are recorded as test_mode events instead
AUDIT_SKIP_PATHS = ("/api/test/channel", "/api/test/all", "/api/test/pattern")


def record_api_action(request: Optional[Request], user: Optional[dict]) -> None:
    """Write an ``api_action`` event (user, method, route) for an admin change.

    Reads are not recorded. Never raises: the event log drops failed writes.
    """
    try:
        if request is None or request.method not in AUDITED_METHODS:
            return
        if request.url.path.rstrip("/") in AUDIT_SKIP_PATHS:
            return
        import event_log  # py/ is on sys.path (api/core/config.py, pytest pythonpath)
        route = request.scope.get("route")
        event_log.record("api_action", source="api",
                         user=(user or {}).get("username"),
                         method=request.method,
                         route=getattr(route, "path", None) or request.url.path,
                         path=request.url.path)
    except Exception as e:
        log.debug(f"api_action not recorded: {e}")


def require_admin(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency for requiring admin access.

    For now, all authenticated users are admins (as per requirements).
    This can be extended in the future for role-based access.

    Admin changes (POST/PUT/PATCH/DELETE) are recorded in the event log as
    ``api_action`` (user, method, route).

    Args:
        request: The current request (for the audit record)
        current_user: Current authenticated user

    Returns:
        dict: Current user data
    """
    record_api_action(request, current_user)
    # All users are admins for now
    return current_user


if __name__ == "__main__":
    # Test password hashing
    password = "test_password_123"
    hashed = AuthManager.hash_password(password)
    print(f"Hashed: {hashed}")
    print(f"Verified: {AuthManager.verify_password(password, hashed)}")
    print(f"Wrong password: {AuthManager.verify_password('wrong', hashed)}")

    # Test key generation
    key = AuthManager.generate_client_key()
    print(f"Client key: {key}")
