"""Schematy wyjscia narzedzi MCP (`outputSchema` i `structuredContent`).

Tam, gdzie narzedzie odpowiada jak trasa v1, uzywany jest schemat v1 (`UploadOut`, `RunDetail`, `EvidenceOut`,
`UsageReport`) — jeden kontrakt dla obu interfejsow. Listy sa opakowane w obiekt `{"items": [...]}`: protokol MCP
wymaga, by `structuredContent` bylo obiektem JSON.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.v1.schemas import JobStatus, PipelineId, UploadKind


class WhoAmIOut(BaseModel):
    org_id: str
    org_name: str
    mode: Literal["cloud", "selfhost"]
    auth: Literal["api_key", "oauth", "deployment_key"] = Field(
        ..., description="credential of this request: an organization API key, an OAuth access token of a "
                         "connector (ChatGPT, Claude, ...) or the selfhost deployment key")
    key_prefix: str | None = Field(default=None, description="visible key prefix; null for the deployment key and "
                                                             "OAuth tokens")
    client_id: str | None = Field(default=None, description="OAuth client (connector) the token was issued to; "
                                                            "null for API keys")
    scopes: list[str]
    server_version: str
    protocol: str = Field(..., description="MCP protocol version of this request")


class PipelineInput(BaseModel):
    kind: Literal["csv", "xlsx", "json"] | None = Field(
        ..., description="file kind the pipeline reads; null = synthetic data only")
    required_columns: list[str] = Field(default_factory=list,
                                        description="canonical columns the column mapper must find")


class PipelineOut(BaseModel):
    name: PipelineId
    description: str
    input: PipelineInput
    example: str


class PipelineList(BaseModel):
    items: list[PipelineOut]


class UploadItem(BaseModel):
    id: str
    filename: str
    kind: UploadKind
    bytes: int
    sha256: str
    created_at: datetime
    suggested_pipeline: PipelineId
    retention_until: datetime


class UploadList(BaseModel):
    items: list[UploadItem]


class StartRunOut(BaseModel):
    job_id: str
    status: JobStatus
    run_id: str | None = None
    reused: bool = Field(default=False, description="true when a queued or running job for the same upload and "
                                                    "pipeline already existed (e.g. auto-run after upload_csv) and "
                                                    "no new job was created")


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    run_id: str | None = None
    error_code: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class RunItem(BaseModel):
    run_id: str
    pipeline: str
    status: Literal["SUCCESS", "PARTIAL", "FAILED"]
    data_status: str | None = None
    created_at: datetime
    period_from: date | None = None
    period_to: date | None = None
    report_sha256: dict[str, str | None] = Field(default_factory=dict, description="{html, md, json}")


class RunList(BaseModel):
    items: list[RunItem]


class ReportOut(BaseModel):
    run_id: str
    format: Literal["markdown", "json"]
    sha256: str = Field(..., description="SHA-256 of the full stored report file (also when `content` is truncated)")
    generated_at: datetime
    content: str
    truncated: bool


class VerifyReportOut(BaseModel):
    run_id: str
    match: bool
    expected_sha256: str
    format: Literal["markdown", "json", "html"]
