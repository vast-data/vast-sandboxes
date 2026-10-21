"""NGC / hosted NVIDIA NIM embeddings client (OpenAI-compatible /v1/embeddings)."""

from __future__ import annotations

import time
from typing import Literal

import requests
from requests.exceptions import HTTPError

from temp_ingest.config import NimSettings

InputType = Literal["passage", "query"]

_RETRYABLE = {429, 500, 502, 503, 504}
# NIM rejects empty/whitespace-only strings ("Input list must be ... non-empty").
_EMPTY_SNIPPET = "[no transcript text]"


class NimEmbedder:
    def __init__(self, settings: NimSettings) -> None:
        self._s = settings
        if settings.api_key and "REPLACE_ME" in settings.api_key:
            raise ValueError("nim.api_key is not set (put your NGC key in config.yaml or NIM_API_KEY)")

    def _post(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if not texts:
            return []

        url = f"{self._s.base_url.rstrip('/')}/embeddings"
        payload = {
            "model": self._s.embed_model,
            "input": texts,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",  # long snippets are truncated rather than rejected
        }
        headers = {"accept": "application/json"}
        if self._s.api_key:
            headers["authorization"] = f"Bearer {self._s.api_key}"
        for attempt in range(4):
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._s.request_timeout_seconds,
            )
            if resp.ok:
                break
            detail = resp.text[:500]
            if resp.status_code in _RETRYABLE and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 400 and len(texts) > 1:
                # Transient NIM errors and occasional bad batch items both surface as 400.
                mid = len(texts) // 2
                return self._post(texts[:mid], input_type) + self._post(texts[mid:], input_type)
            preview = texts[0][:120].replace("\n", " ")
            raise HTTPError(
                f"{resp.status_code} from NIM embeddings — body={detail!r}; "
                f"batch_size={len(texts)}; first_text={preview!r}",
                response=resp,
            )
        else:
            raise RuntimeError("NIM embeddings request failed after retries")

        data = resp.json()["data"]
        # Preserve request order (NIM returns an `index` per item).
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        vectors = [item["embedding"] for item in ordered]
        for v in vectors:
            if len(v) != self._s.embed_dim:
                raise ValueError(
                    f"embedding width {len(v)} != configured embed_dim {self._s.embed_dim}; "
                    f"fix nim.embed_dim / the vector column width"
                )
        return vectors

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        cleaned = [t.strip() if (t and t.strip()) else _EMPTY_SNIPPET for t in texts]
        out: list[list[float]] = []
        step = max(1, self._s.batch_size)
        for i in range(0, len(cleaned), step):
            out.extend(self._post(cleaned[i : i + step], input_type))
        return out
