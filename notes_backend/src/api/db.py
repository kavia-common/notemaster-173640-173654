"""SQLite persistence layer for the notes_backend.

This module is intentionally dependency-free (uses stdlib sqlite3) to keep the
container lightweight and aligned with the notes_database SQLite schema.

It reads the SQLite DB file path from the SQLITE_DB environment variable
(provided by the notes_database container).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple


def _dict_factory(cursor: sqlite3.Cursor, row: Tuple[Any, ...]) -> Dict[str, Any]:
    """Convert sqlite rows into dicts keyed by column name."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


# PUBLIC_INTERFACE
def get_db_path() -> str:
    """Return the configured SQLite database file path.

    Uses env var SQLITE_DB (as defined by the notes_database container). If unset,
    we fall back to a local DB file in the backend container (useful for local dev),
    but production should always set SQLITE_DB.

    Returns:
        Absolute or relative path to the SQLite DB file.
    """
    # NOTE: Orchestrator should set SQLITE_DB in notes_backend .env. Do not hardcode.
    return os.getenv("SQLITE_DB", "myapp.db")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Context manager that opens a SQLite connection with foreign keys enabled."""
    db_path = get_db_path()

    # Create parent directory if a path is provided (best effort).
    try:
        Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Ignore: if db_path is just "myapp.db" relative path, parent is workspace dir.
        pass

    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.row_factory = _dict_factory
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


# PUBLIC_INTERFACE
def init_schema_if_needed() -> None:
    """Ensure required notes app tables exist.

    The notes_database container should already create these tables. This is a
    safety net so the backend can run in isolation for development.
    """
    with _connect() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                user_id INTEGER NULL,
                is_archived INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (note_id, tag_id),
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
            """
        )

        # Indexes consistent with notes_database/init_db.py
        cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_archived ON notes(is_archived)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_note_tags_tag_id ON note_tags(tag_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_note_tags_note_id ON note_tags(note_id)")

        conn.commit()


def _placeholders(n: int) -> str:
    """Return SQL placeholders string like '(?,?,?)'."""
    return "(" + ",".join(["?"] * n) + ")"


def _fetch_note_tags(conn: sqlite3.Connection, note_id: int) -> List[Dict[str, Any]]:
    """Fetch tags for a note (id, name, color)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.id, t.name, t.color
        FROM tags t
        INNER JOIN note_tags nt ON nt.tag_id = t.id
        WHERE nt.note_id = ?
        ORDER BY LOWER(t.name) ASC
        """,
        (note_id,),
    )
    return list(cur.fetchall())


def _upsert_tags(conn: sqlite3.Connection, tag_names: Sequence[str]) -> List[int]:
    """Ensure tags exist (by name) and return their IDs.

    Tag names are normalized to trimmed strings. Empty strings are ignored.
    """
    normalized = []
    for t in tag_names:
        name = (t or "").strip()
        if name:
            normalized.append(name)

    if not normalized:
        return []

    cur = conn.cursor()

    # Insert missing tags (color is null by default; UI can manage later)
    for name in sorted(set(normalized), key=lambda x: x.lower()):
        cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))

    # Fetch ids
    # SQLite doesn't support array binding in a portable way; use IN with placeholders.
    uniq = sorted(set(normalized), key=lambda x: x.lower())
    cur.execute(
        f"SELECT id, name FROM tags WHERE name IN {_placeholders(len(uniq))}",
        tuple(uniq),
    )
    rows = list(cur.fetchall())
    by_name = {r["name"]: int(r["id"]) for r in rows}
    return [by_name[name] for name in uniq if name in by_name]


def _set_note_tags(conn: sqlite3.Connection, note_id: int, tag_names: Sequence[str]) -> None:
    """Replace note's tag associations with provided tag names."""
    cur = conn.cursor()
    tag_ids = _upsert_tags(conn, tag_names)

    # Clear existing
    cur.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))

    # Insert associations
    for tag_id in tag_ids:
        cur.execute(
            "INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)",
            (note_id, tag_id),
        )


def _note_row_to_api(conn: sqlite3.Connection, note_row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a notes table row to API representation (including tags)."""
    note_id = int(note_row["id"])
    tags = _fetch_note_tags(conn, note_id)
    return {
        "id": note_id,
        "title": note_row["title"],
        "content": note_row["content"],
        "is_archived": bool(note_row.get("is_archived", 0)),
        "created_at": note_row.get("created_at"),
        "updated_at": note_row.get("updated_at"),
        "tags": tags,
    }


# PUBLIC_INTERFACE
def list_notes(
    *,
    tag: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List notes optionally filtered by tag, with pagination.

    Args:
        tag: Optional tag name filter.
        include_archived: Whether to include archived notes.
        limit: Max number of notes to return.
        offset: Pagination offset.

    Returns:
        List of notes with tags.
    """
    with _connect() as conn:
        cur = conn.cursor()

        where_clauses: List[str] = []
        params: List[Any] = []

        if not include_archived:
            where_clauses.append("n.is_archived = 0")

        join_clause = ""
        if tag:
            join_clause = "INNER JOIN note_tags nt ON nt.note_id = n.id INNER JOIN tags t ON t.id = nt.tag_id"
            where_clauses.append("LOWER(t.name) = LOWER(?)")
            params.append(tag.strip())

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        cur.execute(
            f"""
            SELECT DISTINCT n.*
            FROM notes n
            {join_clause}
            {where_sql}
            ORDER BY n.updated_at DESC, n.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        )

        rows = list(cur.fetchall())
        return [_note_row_to_api(conn, r) for r in rows]


# PUBLIC_INTERFACE
def create_note(*, title: str, content: str, tags: Sequence[str]) -> Dict[str, Any]:
    """Create a note and associate tags (upsert tags by name)."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO notes (title, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (title, content),
        )
        note_id = int(cur.lastrowid)

        _set_note_tags(conn, note_id, tags)

        cur.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        note_row = cur.fetchone()
        conn.commit()
        return _note_row_to_api(conn, note_row)


# PUBLIC_INTERFACE
def update_note(
    *,
    note_id: int,
    title: str,
    content: str,
    is_archived: bool,
    tags: Sequence[str],
) -> Dict[str, Any]:
    """Update a note and replace its tag associations."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM notes WHERE id = ?", (note_id,))
        exists = cur.fetchone()
        if not exists:
            raise KeyError("Note not found")

        cur.execute(
            """
            UPDATE notes
            SET title = ?, content = ?, is_archived = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, content, 1 if is_archived else 0, note_id),
        )

        _set_note_tags(conn, note_id, tags)

        cur.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        note_row = cur.fetchone()
        conn.commit()
        return _note_row_to_api(conn, note_row)


# PUBLIC_INTERFACE
def delete_note(*, note_id: int) -> None:
    """Delete a note (note_tags will cascade if FK constraints are enabled)."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        if cur.rowcount == 0:
            raise KeyError("Note not found")
        conn.commit()


# PUBLIC_INTERFACE
def list_tags() -> List[Dict[str, Any]]:
    """List tags including usage counts (number of notes associated)."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                t.id,
                t.name,
                t.color,
                COUNT(nt.note_id) AS note_count
            FROM tags t
            LEFT JOIN note_tags nt ON nt.tag_id = t.id
            GROUP BY t.id
            ORDER BY LOWER(t.name) ASC
            """
        )
        return list(cur.fetchall())


# PUBLIC_INTERFACE
def search_notes(
    *,
    q: str,
    tag: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Search notes by substring in title/content, optionally filtered by tag."""
    q_norm = (q or "").strip()
    if not q_norm:
        return []

    with _connect() as conn:
        cur = conn.cursor()

        where_clauses: List[str] = []
        params: List[Any] = []

        # Basic LIKE search; acceptable for SQLite small apps.
        where_clauses.append("(n.title LIKE ? OR n.content LIKE ?)")
        like = f"%{q_norm}%"
        params.extend([like, like])

        if not include_archived:
            where_clauses.append("n.is_archived = 0")

        join_clause = ""
        if tag:
            join_clause = "INNER JOIN note_tags nt ON nt.note_id = n.id INNER JOIN tags t ON t.id = nt.tag_id"
            where_clauses.append("LOWER(t.name) = LOWER(?)")
            params.append(tag.strip())

        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        cur.execute(
            f"""
            SELECT DISTINCT n.*
            FROM notes n
            {join_clause}
            {where_sql}
            ORDER BY n.updated_at DESC, n.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        )

        rows = list(cur.fetchall())
        return [_note_row_to_api(conn, r) for r in rows]
