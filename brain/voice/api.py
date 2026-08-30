"""FastAPI routes for /v1/stt and /v1/tts."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from brain.voice.errors import VoiceError, voice_error_response
from brain.voice.service import VoiceService


class SttResponse(BaseModel):
    text: str
    language: str | None = None


class TtsRequest(BaseModel):
    text: str
    locale: Literal["nl", "en"] | None = None


def register_voice_routes(application: FastAPI) -> None:
    @application.post("/v1/stt", response_model=SttResponse, responses={400: {}, 413: {}, 503: {}})
    async def stt(
        request: Request,
        language: Literal["nl", "en"] | None = None,
    ) -> SttResponse | JSONResponse:
        voice: VoiceService | None = getattr(request.app.state, "voice_service", None)
        if voice is None:
            return voice_error_response(
                VoiceError(
                    code="unavailable",
                    message="Speech recognition is not available.",
                    retryable=True,
                    http_status=503,
                )
            )

        body = await request.body()
        content_type = request.headers.get("content-type", "application/octet-stream")

        try:
            result = await asyncio.to_thread(
                voice.transcribe,
                body,
                content_type=content_type,
                language_hint=language,
            )
        except VoiceError as exc:
            return voice_error_response(exc)

        return SttResponse(text=result.text, language=result.language)

    @application.post("/v1/tts", response_model=None, responses={200: {"content": {"audio/wav": {}}}})
    async def tts(body: TtsRequest, request: Request) -> Response | JSONResponse:
        voice: VoiceService | None = getattr(request.app.state, "voice_service", None)
        if voice is None:
            return voice_error_response(
                VoiceError(
                    code="unavailable",
                    message="Speech synthesis is not available.",
                    retryable=True,
                    http_status=503,
                )
            )

        try:
            wav = await asyncio.to_thread(
                voice.synthesize,
                body.text,
                locale=body.locale,
            )
        except VoiceError as exc:
            return voice_error_response(exc)

        return Response(content=wav, media_type="audio/wav")
