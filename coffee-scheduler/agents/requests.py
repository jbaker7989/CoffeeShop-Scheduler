"""Shared calendar fields on the Scheduler and Forecast wire boundaries."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agents.a2a_transport import AgentError


class WindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    start: str
    end: str

    @classmethod
    def parse(cls, data: dict):
        try:
            request = cls.model_validate(data)
            if type(data["version"]) is not int:
                raise ValueError
            start, end = request.window
            if (start.isoformat() != request.start or end.isoformat() != request.end
                    or not date.min < start <= end < date.max or (end - start).days > 6):
                raise ValueError
        except (ValidationError, ValueError):
            raise AgentError("invalid_window") from None
        return request

    @property
    def window(self) -> tuple[date, date]:
        return date.fromisoformat(self.start), date.fromisoformat(self.end)
