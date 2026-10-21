from __future__ import annotations

from pydantic import BaseModel


class MemoryRecord(BaseModel):
    memory_id: str
    agent_name: str
    department: str
    scope: str
    content: str
    created_at: str
    distance: float | None = None


class StructuredQueryResult(BaseModel):
    columns: list[str]
    rows: list[list[object]]


class S3ObjectRef(BaseModel):
    key: str
    size: int
    last_modified: str


class FileEntry(BaseModel):
    path: str
    is_dir: bool
    size: int


class TranscriptHit(BaseModel):

    chunk_id: str
    engagement_id: str
    customer: str = ""
    account_id: str = ""
    opportunity: str = ""
    opportunity_id: str = ""
    sales_stage: str = ""
    speaker_name: str
    speaker_type: str
    call_date: str
    time_start: float
    text: str
    distance: float
    table: str = ""  # VastDB table this row was read from


class SimilarCall(BaseModel):
    """A whole call ranked by its closest snippet to the query vector."""

    engagement_id: str
    customer: str = ""
    opportunity: str = ""
    sales_stage: str = ""
    best_distance: float
    matched_snippets: int


class MetadataFacet(BaseModel):
    """A distinct metadata value and how many chunks carry it."""

    value: str
    count: int


class CallMetadata(BaseModel):

    total_chunks: int
    calls: list[MetadataFacet]          # engagement_id -> chunk count
    customers: list[MetadataFacet]      # company name -> chunk count
    opportunities: list[MetadataFacet]  # quarter label -> chunk count
    sales_stages: list[MetadataFacet]   # sector -> chunk count
    speaker_types: list[MetadataFacet]  # company | analyst | operator
    call_dates: list[MetadataFacet]     # call_date -> chunk count
