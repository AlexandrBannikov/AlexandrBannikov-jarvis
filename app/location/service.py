import logging
from zoneinfo import ZoneInfo
from app.location.models import LocationCandidate
from app.location.resolver import ReverseGeocoder,TimezoneResolver,validate_coordinates
logger=logging.getLogger(__name__)
class LocationService:
    def __init__(self,storage,geocoder=None,timezone_resolver=None): self.storage=storage; self.geocoder=geocoder or ReverseGeocoder(); self.timezone_resolver=timezone_resolver or TimezoneResolver()
    def resolve(self,latitude,longitude):
        latitude,longitude=validate_coordinates(latitude,longitude); timezone=self.timezone_resolver.resolve(latitude,longitude)
        try: city,country=self.geocoder.resolve(latitude,longitude)
        except Exception as error: logger.warning("Reverse geocoding failed: %s",type(error).__name__); city,country=None,None
        return LocationCandidate(latitude,longitude,city,country,timezone)
    def save(self,user_id,item): ZoneInfo(item.timezone); return self.storage.save(user_id,item)
    def get(self,user_id): return self.storage.get(user_id)
    def clear(self,user_id): return self.storage.clear(user_id)
    def context(self,user_id):
        item=self.get(user_id)
        if not item:return None
        place=", ".join(x for x in (item.city,item.country) if x) or "не определено"
        return f"Confirmed user location: {place}. IANA timezone: {item.timezone}. Use it for local time and location-dependent requests."
    def reminder_location(self,user_id,location_type="current"): return self.storage.get(user_id,location_type)
