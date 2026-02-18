"""Pydantic schemas for notes_backend REST API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TagOut(BaseModel):
    """Tag representation returned by API."""

    id: int = Field(..., description="Tag ID")
    name: str = Field(..., description="Tag name (unique, case-insensitive in practice)")
    color: Optional[str] = Field(None, description="Optional hex color for the tag")


class TagWithCountOut(TagOut):
    """Tag representation that includes usage count."""

    note_count: int = Field(..., description="Number of notes associated with this tag")


class NoteBase(BaseModel):
    """Base note fields."""

    title: str = Field(..., min_length=1, max_length=200, description="Note title")
    content: str = Field(..., min_length=1, max_length=20000, description="Note content/body")
    tags: List[str] = Field(default_factory=list, description="List of tag names")

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, v: List[str]) -> List[str]:
        """Normalize tags by trimming and removing empties/duplicates (case-insensitive)."""
        seen_lower = set()
        out: List[str] = []
        for t in v or []:
            name = (t or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            out.append(name)
        return out


class NoteCreateIn(NoteBase):
    """Request body for creating a note."""

    pass


class NoteUpdateIn(NoteBase):
    """Request body for updating a note."""

    id: int = Field(..., ge=1, description="Note ID")
    is_archived: bool = Field(False, description="Whether note is archived")


class NoteOut(BaseModel):
    """Note representation returned by API."""

    id: int = Field(..., description="Note ID")
    title: str = Field(..., description="Note title")
    content: str = Field(..., description="Note content/body")
    is_archived: bool = Field(..., description="Whether note is archived")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp (if available)")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp (if available)")
    tags: List[TagOut] = Field(default_factory=list, description="Tags attached to note")


class NotesListOut(BaseModel):
    """List wrapper for notes responses."""

    items: List[NoteOut] = Field(..., description="Notes list")
    total: int = Field(..., ge=0, description="Total items in this response (not global count)")


class DeleteOut(BaseModel):
    """Standard delete response."""

    ok: bool = Field(True, description="Whether delete succeeded")


class ErrorOut(BaseModel):
    """Error response body."""

    detail: str = Field(..., description="Human-readable error message")
