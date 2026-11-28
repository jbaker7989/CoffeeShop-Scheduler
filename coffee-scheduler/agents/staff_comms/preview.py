"""Versioned, deterministic staff notification previews."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError

from agents.a2a_transport import AgentError
from contracts.forecast import CANONICAL_TZ


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    template_id: Literal["call_out_cover"]
    location_id: PositiveInt
    starts_at: str


def preview(data: dict) -> dict:
    try:
        request = PreviewRequest.model_validate(data)
        if type(data["version"]) is not int or len(request.starts_at) > 40:
            raise ValueError
        instant = datetime.fromisoformat(request.starts_at)
        if instant.utcoffset() is None or "T" not in request.starts_at:
            raise ValueError
        local = instant.astimezone(ZoneInfo(CANONICAL_TZ))
    except (ValidationError, ValueError, OverflowError):
        raise AgentError("invalid_preview") from None
    return {"template_version": 1, "text": (
        f"Coverage is needed at store {request.location_id} for the shift starting "
        f"{local:%a %b %d at %I:%M %p %Z}. Contact your manager if you are available."
    )}
