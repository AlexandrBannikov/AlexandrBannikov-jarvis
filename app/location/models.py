from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class LocationCandidate:
    latitude: float; longitude: float; city: str | None; country: str | None
    timezone: str; timezone_source: str = "location"; source: str = "telegram"
    location_type: str = "current"

@dataclass(frozen=True, slots=True)
class UserLocation:
    id: int; user_id: int; latitude: float; longitude: float
    city: str | None; country: str | None; timezone: str
    timezone_source: str; source: str; location_type: str
    created_at: str; updated_at: str; active: bool
