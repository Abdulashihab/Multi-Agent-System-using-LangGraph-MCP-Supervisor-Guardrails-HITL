"""
auth.py - Step 1: users table + password hashing.

Later steps will add signup, login and token logic to this same file.
"""

import os
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
def get_database_url() -> str:
    """Same DATABASE_URL your backend.py uses (Render PostgreSQL)."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError("DATABASE_URL is missing. Please add it to your .env file.")

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


def get_connection() -> psycopg.Connection:
    """Open a short-lived connection. Each auth action opens its own."""
    return psycopg.connect(get_database_url(), autocommit=True)


# ---------------------------------------------------------------------------
# users table
# ---------------------------------------------------------------------------
def init_users_table() -> None:
    """Create the users table if it does not exist yet (safe to run every start)."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                name          TEXT NOT NULL,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Which user owns which LangGraph thread (so users can't open each
        # other's travel plans)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_owners (
                thread_id  TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------
MAX_PASSWORD_BYTES = 72  # bcrypt cannot use more than 72 bytes


def hash_password(plain_password: str) -> str:
    """Turn a real password into a scrambled one-way hash for storing."""
    password_bytes = plain_password.encode("utf-8")

    if len(password_bytes) > MAX_PASSWORD_BYTES:
        raise ValueError("Password is too long (maximum 72 bytes).")

    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Check a typed password against the stored hash. Returns True/False."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
    except ValueError:
        # Malformed hash or too-long password: treat as "does not match"
        return False


# ---------------------------------------------------------------------------
# Signup helpers (Step 2)
# ---------------------------------------------------------------------------
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


def validate_signup(name: str, email: str, password: str) -> str | None:
    """Return an error message if the signup details are bad, else None."""
    if not name.strip():
        return "Please enter your name."

    if not EMAIL_PATTERN.match(email.strip()):
        return "Please enter a valid email address."

    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return "Password is too long (maximum 72 characters)."

    return None


def get_user_by_email(email: str) -> dict | None:
    """Find a user by email. Returns a dict, or None if not found."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, name, email, password_hash FROM users WHERE email = %s",
                (email.strip().lower(),),
            )
            return cur.fetchone()


def create_user(name: str, email: str, password: str) -> dict:
    """Save a new user. Raises ValueError if the email is already registered."""
    clean_name = name.strip()
    clean_email = email.strip().lower()
    password_hash = hash_password(password)

    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO users (name, email, password_hash)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (clean_name, clean_email, password_hash),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise ValueError("An account with this email already exists.")

    return {"id": row[0], "name": clean_name, "email": clean_email}


# ---------------------------------------------------------------------------
# Login + token (Step 4)
# ---------------------------------------------------------------------------
COOKIE_NAME = "tripmate_token"
TOKEN_ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))

# Used so that "unknown email" takes as long as "wrong password"
# (stops people from guessing which emails are registered by timing).
_DUMMY_HASH = hash_password("not-a-real-password")


def _get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret or len(secret) < 16:
        raise ValueError(
            "JWT_SECRET is missing or too short. Add a long random value to .env"
        )
    return secret


def authenticate_user(email: str, password: str) -> dict | None:
    """Check email + password. Returns the user dict if correct, else None."""
    user = get_user_by_email(email)

    if user is None:
        verify_password(password, _DUMMY_HASH)  # waste the same time
        return None

    if not verify_password(password, user["password_hash"]):
        return None

    return {"id": user["id"], "name": user["name"], "email": user["email"]}


def create_access_token(user_id: int, name: str, email: str) -> str:
    """Create a signed 'ID card' (JWT) that proves who the user is."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "name": name,
        "email": email,
        "exp": expires_at,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=TOKEN_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Check the signature and expiry. Returns the data inside, or None if bad."""
    try:
        data = jwt.decode(token, _get_jwt_secret(), algorithms=[TOKEN_ALGORITHM])
    except jwt.PyJWTError:
        return None  # tampered, expired or malformed

    return {"id": int(data["sub"]), "name": data.get("name", ""), "email": data.get("email", "")}


# ---------------------------------------------------------------------------
# Thread ownership (Step 5)
# ---------------------------------------------------------------------------
def claim_thread(thread_id: str, user_id: int) -> bool:
    """Mark a thread as owned by this user (first one to claim it wins).
    Returns True if the thread belongs to this user, False if someone else owns it."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO thread_owners (thread_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT (thread_id) DO NOTHING
            """,
            (thread_id, user_id),
        )
        row = conn.execute(
            "SELECT user_id FROM thread_owners WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()

    return row is not None and row[0] == user_id


def user_owns_thread(thread_id: str, user_id: int) -> bool:
    """True only if this thread is recorded as belonging to this user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM thread_owners WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()

    return row is not None and row[0] == user_id


# ---------------------------------------------------------------------------
# Quick self-test: run `python auth.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("1) Creating users table (if missing)...")
    init_users_table()

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print(f"   OK. users table exists, rows in it: {count}")

    print("2) Testing password hashing...")
    hashed = hash_password("hello123")
    print(f"   Hash of 'hello123': {hashed}")
    print(f"   Correct password matches : {verify_password('hello123', hashed)}")
    print(f"   Wrong password matches   : {verify_password('wrong999', hashed)}")

    print("3) Testing login token...")
    if os.getenv("JWT_SECRET"):
        token = create_access_token(1, "Test User", "test@example.com")
        print(f"   Token decodes correctly  : {decode_access_token(token) is not None}")
        print(f"   Tampered token rejected  : {decode_access_token(token + 'x') is None}")
    else:
        print("   Skipped: add JWT_SECRET to .env first")