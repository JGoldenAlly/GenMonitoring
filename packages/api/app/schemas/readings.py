from datetime import datetime

from pydantic import BaseModel

__all__ = ["ReadingOut", "LatestReadingOut"]


class ReadingOut(BaseModel):
    time: datetime
    device_key: str
    register_address: int
    register_type: int
    register_friendly_name: str | None
    value: float | None
    unit: str | None


class LatestReadingOut(BaseModel):
    register_address: int
    register_type: int
    register_friendly_name: str | None
    value: float | None
    unit: str | None
    time: datetime
