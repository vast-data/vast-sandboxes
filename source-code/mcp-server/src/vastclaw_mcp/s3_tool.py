from __future__ import annotations

import os
from typing import Any

from vastclaw_mcp.config import Settings
from vastclaw_mcp.identity import AgentIdentity, require_bucket_scope
from vastclaw_mcp.models import S3ObjectRef


class S3Tool:
    """S3 access to the department bucket."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    def _s3(self) -> Any:
        if self._client is None:
            import boto3

            from botocore.client import Config

            s3_cfg = Config(s3={"addressing_style": "path"}, signature_version="s3v4")
            if self._settings.sts is not None:
                # Exports the well-known AWS_* env vars botocore's built-in
                os.environ["AWS_ROLE_ARN"] = self._settings.sts.role_arn
                os.environ["AWS_WEB_IDENTITY_TOKEN_FILE"] = str(self._settings.sts.token_file)
                os.environ["AWS_ENDPOINT_URL_STS"] = self._settings.sts.sts_endpoint
                self._client = boto3.client(
                    "s3",
                    endpoint_url=self._settings.s3.endpoint,
                    verify=self._settings.s3.ssl_verify,
                    config=s3_cfg,
                )
            else:
                self._client = boto3.client(
                    "s3",
                    endpoint_url=self._settings.s3.endpoint,
                    aws_access_key_id=self._settings.vast.access_key,
                    aws_secret_access_key=self._settings.vast.secret_key,
                    verify=self._settings.s3.ssl_verify,
                    config=s3_cfg,
                )
        return self._client

    def list_objects(
        self, identity: AgentIdentity, prefix: str = "", limit: int = 100
    ) -> list[S3ObjectRef]:
        require_bucket_scope(identity, identity.bucket)
        response = self._s3().list_objects_v2(Bucket=identity.bucket, Prefix=prefix, MaxKeys=limit)
        return [
            S3ObjectRef(
                key=item["Key"],
                size=item["Size"],
                last_modified=item["LastModified"].isoformat(),
            )
            for item in response.get("Contents", [])
        ]

    def get_object(self, identity: AgentIdentity, key: str) -> bytes:
        require_bucket_scope(identity, identity.bucket)
        response = self._s3().get_object(Bucket=identity.bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def put_object(self, identity: AgentIdentity, key: str, data: bytes) -> S3ObjectRef:
        require_bucket_scope(identity, identity.bucket)
        self._s3().put_object(Bucket=identity.bucket, Key=key, Body=data)
        return S3ObjectRef(key=key, size=len(data), last_modified="")
