from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

SECRET_PATH = Path("/etc/secrets/config.yaml")


class VastCredentials(BaseModel):
    """Static key for the shared, platform-owned VastDB buckets (memory/telemetry/RAG)."""

    access_key: str
    secret_key: str


class StsSettings(BaseModel):
    """Department temporary S3 credentials via AssumeRoleWithWebIdentity."""

    role_arn: str
    token_file: Path = Path("/var/run/secrets/vast/token")
    sts_endpoint: str = "https://sts.vastdata.com"


class VastDBSettings(BaseModel):
    endpoint: str = "http://localhost:9090"
    memory_bucket: str = "vastclaw-memory"
    memory_schema: str = "memory"
    telemetry_bucket: str = "vastclaw-telemetry"
    telemetry_schema: str = "telemetry"
    rag_bucket: str = "earnings-calls"
    rag_schema: str = "earnings21"
    rag_table: str = "call_chunks"
    rag_department_scope: str = ""
    ssl_verify: bool = False
    adbc_driver_path: str = "/opt/adbc-driver/libadbc_driver_vastdb.so"
    embedding_dim: int = 1024


class S3Settings(BaseModel):
    endpoint: str = "http://localhost:80"
    ssl_verify: bool = False


class SandboxSettings(BaseModel):
    root: Path = Path("/sandbox")
    max_read_bytes: int = 5_242_880


class Settings(BaseModel):
    vast: VastCredentials
    sts: StsSettings | None = None
    vdb: VastDBSettings = Field(default_factory=VastDBSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    mode: str = "sales_rag"

    @classmethod
    def from_yaml(cls, path: Path = SECRET_PATH) -> Settings:
        raw = yaml.safe_load(path.read_text())
        return cls.model_validate(raw)
