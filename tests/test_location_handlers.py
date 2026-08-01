import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from app.handlers import handle_location,location_callback,handle_text
from app.location.models import LocationCandidate
def ctx(service):
    agent=AsyncMock();agent.ask.return_value="answer"
    return SimpleNamespace(application=SimpleNamespace(bot_data={"location_service":service,"pending_locations":{},"agent":agent,"user_locks":{},"reminder_service":None,"config":SimpleNamespace(telegram_allowed_user_ids=frozenset({123}))}),bot=SimpleNamespace(send_chat_action=AsyncMock()))
def update():return SimpleNamespace(effective_user=SimpleNamespace(id=123),effective_chat=SimpleNamespace(id=456),effective_message=SimpleNamespace(location=SimpleNamespace(latitude=1,longitude=2),reply_text=AsyncMock()))
@patch("app.handlers.asyncio.to_thread",new_callable=AsyncMock)
def test_location_message_waits_for_consent(to_thread):
    service=Mock();candidate=LocationCandidate(1,2,"City","Country","Europe/Moscow");to_thread.return_value=candidate;c=ctx(service);u=update();asyncio.run(handle_location(u,c))
    to_thread.assert_awaited_once_with(service.resolve,1,2);service.save.assert_not_called();assert c.application.bot_data["pending_locations"][123][1] is candidate
@patch("app.handlers.asyncio.to_thread",new_callable=AsyncMock)
def test_save_uses_callback_owner(to_thread):
    service=Mock();c=ctx(service);candidate=LocationCandidate(1,2,None,None,"UTC");c.application.bot_data["pending_locations"]={123:("nonce123",candidate),999:object()}
    query=SimpleNamespace(data="location:save:nonce123",answer=AsyncMock(),edit_message_text=AsyncMock());asyncio.run(location_callback(SimpleNamespace(callback_query=query,effective_user=SimpleNamespace(id=123)),c))
    to_thread.assert_awaited_once_with(service.save,123,candidate);assert 999 in c.application.bot_data["pending_locations"]
def test_discard_never_saves():
    service=Mock();c=ctx(service);c.application.bot_data["pending_locations"][123]=("nonce123",object());query=SimpleNamespace(data="location:discard:nonce123",answer=AsyncMock(),edit_message_text=AsyncMock())
    asyncio.run(location_callback(SimpleNamespace(callback_query=query,effective_user=SimpleNamespace(id=123)),c));service.save.assert_not_called()
def test_stale_callback_cannot_save_replacement():
    service=Mock();c=ctx(service);new=LocationCandidate(3,4,None,None,"UTC");c.application.bot_data["pending_locations"][123]=("newnonce",new)
    query=SimpleNamespace(data="location:save:oldnonce",answer=AsyncMock(),edit_message_text=AsyncMock());asyncio.run(location_callback(SimpleNamespace(callback_query=query,effective_user=SimpleNamespace(id=123)),c))
    service.save.assert_not_called();assert c.application.bot_data["pending_locations"][123][1] is new
def test_text_flow_remains_unchanged():
    c=ctx(None);u=update();u.effective_message.text="Какая погода?";u.effective_message.message_id=None
    asyncio.run(handle_text(u,c));c.application.bot_data["agent"].ask.assert_awaited_once()
