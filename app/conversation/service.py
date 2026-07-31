from __future__ import annotations
import re
from app.conversation.models import ConversationKey, ConversationState, PendingQuestion
from app.conversation.storage import ConversationStorage
_CANCEL=re.compile(r"(?i)^(?:ладно|неважно|забудь|отмени|начн[её]м заново|давай о другом)")
_CORRECTION=re.compile(r"(?i)^(?:нет,?\s*)?(?:я ошиб|не так|ошибка|исправ)")
_NEW_TOPIC=re.compile(r"(?i)(?:погода|роутер|новый вопрос|другая тема|а какая|а что насч[её]т)")
class ConversationManager:
    def __init__(self,storage:ConversationStorage)->None:self.storage=storage
    def key(self,owner_id:int,chat_id:int,thread_id:int|None=None)->ConversationKey:return ConversationKey(owner_id,chat_id,thread_id)
    def record_user(self,key:ConversationKey,text:str,*,message_id:int|None=None,reply_to:int|None=None)->str:
        state=self.storage.get_state(key); intent=self.classify(text,state); self.storage.append_message(key,"user",text,telegram_message_id=message_id,reply_to_message_id=reply_to)
        if state is None or state.status in {"expired","closed"}:state=ConversationState(key)
        if not state.active_topic and intent not in {"ANSWER_TO_PENDING", "CORRECTION"}:
            state.active_topic = text[:300]
            state.user_goal = text[:1000]
        state.last_user_intent=intent
        if intent == "ANSWER_TO_PENDING" and state.pending_question:
            fields = state.pending_question.expected_fields
            if len(fields) >= 2:
                match = re.search(r"([0-9]+(?:[.,][0-9]+)?\s*л\.?)[,; ]+([0-9]+\s*л\.?с\.?)", text, re.I)
                if match:
                    state.collected_facts[fields[0]] = match.group(1)
                    state.collected_facts[fields[1]] = match.group(2)
            elif fields:
                state.collected_facts[fields[0]] = text[:300]
            state.pending_question = None
            state.active_topic = state.active_topic or "текущая задача"
        if intent in {"CANCEL","NEW_TOPIC"}:state.pending_question=None; state.active_topic=state.active_topic if intent=="CANCEL" else ""; state.user_goal=state.user_goal if intent=="CANCEL" else ""; state.collected_facts={} if intent=="NEW_TOPIC" else state.collected_facts
        self.storage.save_state(state); return intent
    def record_assistant(self,key:ConversationKey,text:str,*,pending:PendingQuestion|None=None)->None:
        self.storage.append_message(key,"assistant",text); state=self.storage.get_state(key) or ConversationState(key); state.last_assistant_action="ask" if pending else "answer"; state.pending_question=pending or state.pending_question; self.storage.save_state(state)
    def classify(self,text:str,state:ConversationState|None)->str:
        text=text.strip()
        if _CANCEL.search(text):return "CANCEL"
        if _CORRECTION.search(text):return "CORRECTION"
        if state and state.status=="active" and state.pending_question and not state.is_expired():
            if _NEW_TOPIC.search(text) and len(text)>12:return "NEW_TOPIC"
            if len(text)<=180 or re.search(r"\b(?:да|нет|год|л\.?с|кв|тыс)\b",text,re.I):return "ANSWER_TO_PENDING"
            return "CONTINUATION"
        return "NEW_TOPIC" if _NEW_TOPIC.search(text) else "CONTINUATION"
    def context(self,key:ConversationKey,current_message:str)->list[dict[str,str]]:
        state=self.storage.get_state(key); blocks=[]
        if state and state.status=="active" and not state.is_expired():blocks.append({"role":"system","content":"ACTIVE_CONVERSATION_STATE\n"+str(state.compact()),"provenance":"PENDING_QUESTION"})
        blocks.extend(self.storage.recent_messages(key)); return blocks
    def summary(self,key:ConversationKey)->str:
        state=self.storage.get_state(key)
        if not state or state.status!="active":return "Текущая тема не сохранена."
        facts=", ".join(f"{k}: {v}" for k,v in state.collected_facts.items()); pending=state.pending_question.text if state.pending_question else "нет"
        return f"Тема: {state.active_topic or 'не определена'}\nЦель: {state.user_goal or 'не определена'}\nОжидается: {pending}\nСобрано: {facts or 'нет'}"
