import math
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.location.models import LocationCandidate
from app.location.resolver import ReverseGeocoder,TimezoneResolver,validate_coordinates
from app.location.service import LocationService
from app.location.storage import LocationStorage
from app.location.tool import GetUserLocationTool

def item(city="Тюмень"):return LocationCandidate(57.15,65.53,city,"Россия","Asia/Yekaterinburg")
def service(tmp_path):return LocationService(LocationStorage(tmp_path/"location.db"),SimpleNamespace(),SimpleNamespace())
def test_storage_create_update_delete_and_permissions(tmp_path:Path):
    s=LocationStorage(tmp_path/"location.db"); first=s.save(1,item()); second=s.save(1,item("Москва"))
    assert first.id==second.id and s.get(1).city=="Москва" and s.path.stat().st_mode&0o777==0o600
    assert s.clear(1) and s.get(1) is None
def test_user_isolation_and_tool_ownership(tmp_path):
    svc=service(tmp_path);svc.save(1,item("A"));svc.save(2,item("B"));assert svc.get(1).city=="A" and svc.get(2).city=="B"
    tool=GetUserLocationTool(svc);assert tool.execute(trusted_owner_id=2)["city"]=="B"
    with pytest.raises(ValueError):tool.execute()
    svc.clear(1);assert svc.get(2).city=="B"
@pytest.mark.parametrize("lat,lon",[(math.nan,0),(0,math.inf),(91,0),(0,-181)])
def test_invalid_coordinates(lat,lon):
    with pytest.raises(ValueError):validate_coordinates(lat,lon)
def test_timezone_is_iana():assert TimezoneResolver().resolve(40.7128,-74.006)=="America/New_York"
def test_geocoder_failure_is_safe(tmp_path):
    geo=SimpleNamespace(resolve=lambda *_:(_ for _ in()).throw(OSError()))
    svc=LocationService(LocationStorage(tmp_path/"l.db"),geo,SimpleNamespace(resolve=lambda *_:"Europe/Moscow"))
    resolved=svc.resolve(55.7,37.6);assert resolved.city is None and resolved.timezone=="Europe/Moscow"
def test_context_is_owner_scoped_and_has_no_coordinates(tmp_path):
    svc=service(tmp_path);svc.save(1,item());context=svc.context(1)
    assert "Тюмень" in context and "Asia/Yekaterinburg" in context and "57.15" not in context and svc.context(2) is None
def test_reverse_geocoder_has_timeout_user_agent_cache_and_rate_limit():
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self):return b'{"address":{"city":"A","country":"B"}}'
    calls=[]; sleeps=[]; times=iter((2.0,2.0,2.0,3.0))
    def opener(request,timeout):calls.append((request,timeout));return Response()
    geo=ReverseGeocoder(opener=opener,clock=lambda:next(times),sleeper=sleeps.append)
    assert geo.resolve(1,2)==("A","B");assert geo.resolve(1,2)==("A","B");assert geo.resolve(2,3)==("A","B")
    assert len(calls)==2 and all(call[1]==5.0 for call in calls);assert calls[0][0].get_header("User-agent")=="JarvisLocation/1.0";assert sleeps==[1.0]
