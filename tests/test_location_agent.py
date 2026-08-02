import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from app.ai.agent import JarvisAgent
from app.tools import create_default_tool_manager

async def immediate(function,*args,**kwargs):return function(*args,**kwargs)
class Provider:
    def __init__(self):self.request=None
    def create_response(self,**kwargs):self.request=kwargs;return SimpleNamespace(id="r",output=[],output_text="ok")
def test_confirmed_location_is_added_to_context_for_same_user(tmp_path):
    provider=Provider();location=Mock();location.context.return_value="Confirmed location: Tyumen. IANA timezone: Asia/Yekaterinburg."
    manager=create_default_tool_manager(str(tmp_path/"missing.yaml"),include_legacy_remote=False)
    answer=asyncio.run(JarvisAgent(provider,manager,run_sync=immediate,location_service=location,web_search_enabled=True).ask("Какая погода?",user_id=123))
    assert answer!="ok" and "IANA timezone: Asia/Yekaterinburg" in provider.request["instructions"]
    location.context.assert_called_once_with(123)
