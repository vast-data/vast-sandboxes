from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_INGEST_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(
    os.environ.get("VASTCLAW_CONFIG", str(_INGEST_DIR / "config" / "config.yaml"))
)


class NimSettings(BaseModel):
    base_url: str = "https://integrate.api.nvidia.com/v1"
    api_key: str = ""
    embed_model: str = "nvidia/nv-embedqa-e5-v5"
    embed_dim: int = 1024
    batch_size: int = 64
    request_timeout_seconds: float = 60.0


class VastCredentials(BaseModel):
    access_key: str = ""
    secret_key: str = ""


class VastDBSettings(BaseModel):
    endpoint: str = "http://localhost:9090"
    bucket: str = "earnings-calls"
    schema_name: str = Field(default="earnings21", alias="schema")
    table: str = "call_chunks"
    ssl_verify: bool = False
    adbc_driver_path: str = "/opt/adbc-driver/libadbc_driver_vastdb.so"

    model_config = {"populate_by_name": True}


class S3Settings(BaseModel):
    endpoint: str = "http://localhost:80"
    ssl_verify: bool = False


class IngestSettings(BaseModel):
    department: str = "sales"
    data_dir: str = "data/calls"


class Settings(BaseModel):
    nim: NimSettings = Field(default_factory=NimSettings)
    vast: VastCredentials = Field(default_factory=VastCredentials)
    vdb: VastDBSettings = Field(default_factory=VastDBSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "Settings":
        raw = yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}
        settings = cls.model_validate(raw or {})
        settings._apply_env()
        return settings

    def _apply_env(self) -> None:
        if v := os.environ.get("NIM_API_KEY"):
            self.nim.api_key = v
        if v := os.environ.get("NIM_BASE_URL"):
            self.nim.base_url = v
        if v := os.environ.get("VAST_ACCESS_KEY"):
            self.vast.access_key = v
        if v := os.environ.get("VAST_SECRET_KEY"):
            self.vast.secret_key = v
        if v := os.environ.get("VASTDB_ENDPOINT"):
            self.vdb.endpoint = v
