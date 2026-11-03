"""
threads.py - Feature 3: save every trip as a "thread" per user (like Gemini's Recent list).

A thread = one trip: the request, the draft plan, the final plan and a status.
Saving never breaks the app: if a save fails it only prints a message.
"""

import re

from psycopg.rows import dict_row

from auth import get_connection

TITLE_MAX_LENGTH = 50


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------
def init_threads_table() -> None:
    """Create the trip_threads table if it does not exist yet (safe on every start)."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_threads (
                thread_id    TEXT PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title        TEXT NOT NULL,
                user_request TEXT NOT NULL,
                draft        TEXT NOT NULL DEFAULT '',
                final_plan   TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'awaiting_approval',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trip_threads_user
            ON trip_threads (user_id, updated_at DESC)
            """
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_title(request_text: str) -> str:
    """Short title from the first characters of the request (costs zero AI tokens)."""
    clean = re.sub(r"\s+", " ", request_text).strip()
    if len(clean) <= TITLE_MAX_LENGTH:
        return clean or "New trip"
    return clean[:TITLE_MAX_LENGTH].rstrip() + "..."


def _row_to_dict(row: dict) -> dict:
    """Make a database row safe to send as JSON (dates become text)."""
    result = dict(row)
    for key in ("created_at", "updated_at"):
        if key in result and result[key] is not None:
            result[key] = result[key].isoformat()
    return result


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def save_draft(thread_id: str, user_id: int, request_text: str, draft_text: str) -> bool:
    """Save (or update) a thread when a draft plan has been generated."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO trip_threads (thread_id, user_id, title, user_request, draft, status)
                VALUES (%s, %s, %s, %s, %s, 'awaiting_approval')
                ON CONFLICT (thread_id) DO UPDATE
                SET draft = EXCLUDED.draft,
                    status = 'awaiting_approval',
                    updated_at = NOW()
                WHERE trip_threads.user_id = EXCLUDED.user_id
                """,
                (thread_id, user_id, make_title(request_text), request_text, draft_text),
            )
        return True
    except Exception as exc:
        print(f"THREAD SAVE ERROR (draft): {type(exc).__name__}: {exc}")
        return False


def save_final(thread_id: str, user_id: int, final_text: str) -> bool:
    """Save the final plan after the user approved (or revised) the draft."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE trip_threads
                SET final_plan = %s, status = 'finished', updated_at = NOW()
                WHERE thread_id = %s AND user_id = %s
                """,
                (final_text, thread_id, user_id),
            )
        return True
    except Exception as exc:
        print(f"THREAD SAVE ERROR (final): {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def list_threads(user_id: int, limit: int = 50) -> list[dict]:
    """The user's threads, newest first (only what the sidebar list needs)."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT thread_id, title, status, updated_at
                FROM trip_threads
                WHERE user_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            return [_row_to_dict(row) for row in cur.fetchall()]


def get_thread(thread_id: str, user_id: int) -> dict | None:
    """One full thread, only if it belongs to this user."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT thread_id, title, user_request, draft, final_plan, status,
                       created_at, updated_at
                FROM trip_threads
                WHERE thread_id = %s AND user_id = %s
                """,
                (thread_id, user_id),
            )
            row = cur.fetchone()
    return _row_to_dict(row) if row else None


def delete_thread(thread_id: str, user_id: int) -> bool:
    """Delete one of the user's threads. Returns True if something was deleted."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM trip_threads WHERE thread_id = %s AND user_id = %s",
            (thread_id, user_id),
        )
        return cur.rowcount > 0