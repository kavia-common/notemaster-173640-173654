"""notes_backend FastAPI application.

Exposes a REST API for a fullstack notes application with SQLite persistence.

Endpoints:
- GET/POST/PUT/DELETE /notes
- GET /tags
- GET /search

CORS is configured to allow the React frontend (typically http://localhost:3000).
"""

from __future__ import annotations

import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from src.api import db
from src.api.schemas import (
    DeleteOut,
    ErrorOut,
    NoteCreateIn,
    NoteOut,
    NoteUpdateIn,
    NotesListOut,
    TagWithCountOut,
)

openapi_tags = [
    {"name": "Health", "description": "Service health and basic diagnostics."},
    {"name": "Notes", "description": "Create, list, update, and delete notes with tags."},
    {"name": "Tags", "description": "List tags and usage counts."},
    {"name": "Search", "description": "Search notes by keyword, optionally filtered by tag."},
]

app = FastAPI(
    title="Notemaster Notes API",
    description=(
        "Backend API for the Notemaster fullstack notes application.\n\n"
        "Uses SQLite persistence and supports CRUD notes, tagging, and search."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)


def _get_allowed_origins() -> List[str]:
    """Compute allowed CORS origins from environment.

    Returns:
        A list of origins to allow. If empty, FastAPI/Starlette will treat it as
        "no CORS allowed", so we always provide safe defaults for local dev.

    Notes:
        Preview manifests set ALLOWED_ORIGINS to a comma-separated list.
    """
    env_val = (os.getenv("ALLOWED_ORIGINS") or "").strip()
    if env_val:
        return [o.strip() for o in env_val.split(",") if o.strip()]

    # Fallback for local development.
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


allowed_origins = _get_allowed_origins()
allow_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX")  # optional

# CORS: allow the React frontend in preview and local development.
# We do not use cookies, so allow_credentials stays False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    """Initialize schema if needed at application startup."""
    db.init_schema_if_needed()


# PUBLIC_INTERFACE
@app.get(
    "/",
    tags=["Health"],
    summary="Health check",
    description="Simple health-check endpoint.",
)
def health_check() -> dict:
    """Health check.

    Returns:
        JSON object confirming service is reachable.
    """
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/notes",
    tags=["Notes"],
    response_model=NotesListOut,
    responses={400: {"model": ErrorOut}},
    summary="List notes",
    description="List notes, optionally filtered by tag. Archived notes are excluded by default.",
)
def get_notes(
    tag: Optional[str] = Query(None, description="Filter notes by tag name"),
    include_archived: bool = Query(False, description="Include archived notes"),
    limit: int = Query(200, ge=1, le=500, description="Max notes to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> NotesListOut:
    """List notes with optional tag filter."""
    items = db.list_notes(tag=tag, include_archived=include_archived, limit=limit, offset=offset)
    return NotesListOut(items=items, total=len(items))


# PUBLIC_INTERFACE
@app.post(
    "/notes",
    tags=["Notes"],
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorOut}},
    summary="Create note",
    description="Create a new note with optional tags (tags are upserted by name).",
)
def post_note(payload: NoteCreateIn) -> NoteOut:
    """Create a note.

    Args:
        payload: NoteCreateIn with title/content/tags.

    Returns:
        The created note with its tag objects.
    """
    note = db.create_note(title=payload.title, content=payload.content, tags=payload.tags)
    return NoteOut.model_validate(note)


# PUBLIC_INTERFACE
@app.put(
    "/notes",
    tags=["Notes"],
    response_model=NoteOut,
    responses={404: {"model": ErrorOut}, 400: {"model": ErrorOut}},
    summary="Update note",
    description="Update an existing note and replace its tags (tags are upserted by name).",
)
def put_note(payload: NoteUpdateIn) -> NoteOut:
    """Update a note by id."""
    try:
        note = db.update_note(
            note_id=payload.id,
            title=payload.title,
            content=payload.content,
            is_archived=payload.is_archived,
            tags=payload.tags,
        )
        return NoteOut.model_validate(note)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")


# PUBLIC_INTERFACE
@app.delete(
    "/notes",
    tags=["Notes"],
    response_model=DeleteOut,
    responses={404: {"model": ErrorOut}},
    summary="Delete note",
    description="Delete a note by id.",
)
def delete_note(
    note_id: int = Query(..., ge=1, description="ID of the note to delete"),
) -> DeleteOut:
    """Delete a note by id."""
    try:
        db.delete_note(note_id=note_id)
        return DeleteOut(ok=True)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")


# PUBLIC_INTERFACE
@app.get(
    "/tags",
    tags=["Tags"],
    response_model=List[TagWithCountOut],
    summary="List tags",
    description="List tags with usage counts (how many notes are associated).",
)
def get_tags() -> List[TagWithCountOut]:
    """Return all tags sorted by name with usage counts."""
    tags = db.list_tags()
    return [TagWithCountOut.model_validate(t) for t in tags]


# PUBLIC_INTERFACE
@app.get(
    "/search",
    tags=["Search"],
    response_model=NotesListOut,
    responses={400: {"model": ErrorOut}},
    summary="Search notes",
    description="Search notes by substring match on title/content, optionally filtered by tag.",
)
def search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query string"),
    tag: Optional[str] = Query(None, description="Optional tag filter"),
    include_archived: bool = Query(False, description="Include archived notes"),
    limit: int = Query(200, ge=1, le=500, description="Max notes to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> NotesListOut:
    """Search notes by keyword."""
    items = db.search_notes(
        q=q, tag=tag, include_archived=include_archived, limit=limit, offset=offset
    )
    return NotesListOut(items=items, total=len(items))
