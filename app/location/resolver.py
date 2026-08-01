"""Cached reverse geocoder and local IANA timezone resolver."""
from functools import lru_cache
import json, math, threading, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

def validate_coordinates(latitude,longitude):
    latitude,longitude=float(latitude),float(longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude): raise ValueError("coordinates must be finite")
    if not -90<=latitude<=90 or not -180<=longitude<=180: raise ValueError("coordinates outside bounds")
    return latitude,longitude
class TimezoneResolver:
    def __init__(self,finder=None):
        if finder is None:
            from timezonefinder import TimezoneFinder
            finder=TimezoneFinder(in_memory=True)
        self.finder=finder
    def resolve(self,latitude,longitude):
        latitude,longitude=validate_coordinates(latitude,longitude)
        name=self.finder.timezone_at(lat=latitude,lng=longitude)
        if not name: raise LookupError("timezone not found")
        return str(name)
class ReverseGeocoder:
    def __init__(self,endpoint="https://nominatim.openstreetmap.org/reverse",timeout=5.0,opener=urlopen,clock=time.monotonic,sleeper=time.sleep):
        self.endpoint=endpoint; self.timeout=timeout; self.opener=opener
        self.clock=clock; self.sleeper=sleeper; self._last_request=0.0; self._lock=threading.Lock()
    def resolve(self,latitude,longitude):
        latitude,longitude=validate_coordinates(latitude,longitude); return self._cached(round(latitude,5),round(longitude,5))
    @lru_cache(maxsize=512)
    def _cached(self,latitude,longitude):
        with self._lock:
            delay=max(0.0,1.0-(self.clock()-self._last_request))
            if delay:self.sleeper(delay)
            query=urlencode({"lat":latitude,"lon":longitude,"format":"jsonv2","addressdetails":1})
            request=Request(f"{self.endpoint}?{query}",headers={"User-Agent":"JarvisLocation/1.0"})
            with self.opener(request,timeout=self.timeout) as response: payload=json.loads(response.read().decode())
            self._last_request=self.clock()
        address=payload.get("address",{}); city=next((address.get(k) for k in ("city","town","village","municipality","county") if address.get(k)),None)
        return city,address.get("country")
