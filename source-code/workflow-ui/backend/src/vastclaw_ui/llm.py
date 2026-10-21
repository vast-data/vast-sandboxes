from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from vastclaw_ui.config import ModelSettings

_log = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MAX_BACKOFF_SECONDS = 30.0


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int


class ModelError(RuntimeError):
    pass


class EmbeddingError(RuntimeError):
    pass


def _backoff_delay(attempt: int, base: float, retry_after: str | None) -> float:
    """Server-provided Retry-After wins; otherwise exponential backoff with jitter
    so parallel sub-agents don't retry in lockstep."""
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(base * (2**attempt), _MAX_BACKOFF_SECONDS) * (0.5 + random.random())


def _request_json(
    request: urllib.request.Request,
    *,
    timeout: float,
    attempts: int,
    backoff: float,
    error_cls: type[Exception],
    what: str,
) -> dict:
    attempts = max(1, attempts)
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_STATUS or last:
                raise error_cls(f"HTTP Error {exc.code}: {exc.reason}") from exc
            delay = _backoff_delay(attempt, backoff, exc.headers.get("retry-after"))
            _log.warning(
                "%s got HTTP %s; retrying in %.1fs (attempt %d/%d)",
                what,
                exc.code,
                delay,
                attempt + 1,
                attempts,
            )
            time.sleep(delay)
        except Exception as exc:  # network / TLS / timeout
            if last:
                raise error_cls(str(exc)) from exc
            delay = _backoff_delay(attempt, backoff, None)
            _log.warning(
                "%s failed (%s); retrying in %.1fs (attempt %d/%d)",
                what,
                exc,
                delay,
                attempt + 1,
                attempts,
            )
            time.sleep(delay)
    raise error_cls(f"{what} exhausted {attempts} attempts")


class NimEmbedder:
    """Query-side embeddings (OpenAI-compatible /v1/embeddings)."""

    def __init__(self, settings: ModelSettings) -> None:
        self._s = settings

    @property
    def enabled(self) -> bool:
        if not self._s.enabled:
            return False
        return bool(self._s.effective_embed_api_key) or self._s.allow_anonymous

    def embed_query(self, text: str) -> list[float]:
        if not self.enabled:
            raise EmbeddingError("model disabled or embeddings api_key not configured")
        url = f"{self._s.effective_embed_base_url.rstrip('/')}/embeddings"
        payload = json.dumps(
            {
                "model": self._s.embed_model,
                "input": [text or " "],
                "input_type": "query",
                "encoding_format": "float",
                "truncate": "END",
            }
        ).encode("utf-8")
        headers = {"content-type": "application/json"}
        key = self._s.effective_embed_api_key
        if key:
            headers["authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            url=url,
            data=payload,
            method="POST",
            headers=headers,
        )
        body = _request_json(
            request,
            timeout=self._s.request_timeout_seconds,
            attempts=self._s.max_retries,
            backoff=self._s.retry_backoff_seconds,
            error_cls=EmbeddingError,
            what="NIM embeddings",
        )
        try:
            vec = body["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingError(f"unexpected embeddings response: {body}") from exc
        if len(vec) != self._s.embed_dim:
            raise EmbeddingError(
                f"embedding width {len(vec)} != configured embed_dim {self._s.embed_dim}"
            )
        return vec


class NimClient:
    """Minimal OpenAI-compatible client for NVIDIA NIM — hosted (https://integrate.api.nvidia.com/v1) or self-hosted."""

    def __init__(self, settings: ModelSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        if not self._settings.enabled:
            return False
        return bool(self._settings.api_key) or self._settings.allow_anonymous

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> Completion:
        if not self.enabled:
            raise ModelError("model disabled or api_key not configured")
        url = f"{self._settings.base_url.rstrip('/')}/chat/completions"
        payload = json.dumps(
            {
                "model": self._settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self._settings.temperature,
                "max_tokens": max_tokens or self._settings.max_tokens,
            }
        ).encode("utf-8")
        headers = {"content-type": "application/json"}
        if self._settings.api_key:
            headers["authorization"] = f"Bearer {self._settings.api_key}"
        request = urllib.request.Request(
            url=url,
            data=payload,
            method="POST",
            headers=headers,
        )
        attempts = max(1, int(self._settings.max_retries))
        detail = "no content"
        for attempt in range(1, attempts + 1):
            body = _request_json(
                request,
                timeout=self._settings.request_timeout_seconds,
                attempts=self._settings.max_retries,
                backoff=self._settings.retry_backoff_seconds,
                error_cls=ModelError,
                what="NIM chat completion",
            )
            try:
                choice = body["choices"][0]
                text = choice["message"]["content"]
                usage = body.get("usage", {})
            except (KeyError, IndexError, TypeError) as exc:
                raise ModelError(f"unexpected NIM response: {body}") from exc
            if text and text.strip():
                return Completion(
                    text=text,
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                )
            detail = (
                f"empty content (finish_reason={choice.get('finish_reason')!r}, "
                f"completion_tokens={usage.get('completion_tokens')})"
            )
            _log.warning(
                "NIM returned %s on attempt %d/%d for model %s",
                detail, attempt, attempts, self._settings.model,
            )
            if attempt < attempts:
                time.sleep(min(_MAX_BACKOFF_SECONDS, self._settings.retry_backoff_seconds * attempt))
        raise ModelError(f"NIM chat completion returned {detail} after {attempts} attempt(s)")
