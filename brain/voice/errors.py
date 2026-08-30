"""Voice endpoint errors — CONVENTIONS.md shape."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class VoiceError(Exception):
    code: str
    message: str
    retryable: bool = False
    http_status: int = 400

    def to_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        }


def voice_error_response(exc: VoiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_body())
