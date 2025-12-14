"""
Authentication and authorization for LightShowPi Neo API.

Provides secure password hashing, JWT token generation, and
IP-based access control.
"""

import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, List
import json

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from fastapi import HTTPException, Security, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

log = logging.getLogger(__name__)

# JWT configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

security = HTTPBearer()


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
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security)
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


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency for requiring admin access.

    For now, all authenticated users are admins (as per requirements).
    This can be extended in the future for role-based access.

    Args:
        current_user: Current authenticated user

    Returns:
        dict: Current user data
    """
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
