"""Ownership-safe model tools; identity is injected by the agent."""

from app.tools.base import Tool
from .formatter import provenance

def schema(properties):return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}

class DocumentTool(Tool):
    def __init__(self,service,name,description,properties):self.service=service;self._name=name;self._description=description;self._properties=properties
    @property
    def name(self):return self._name
    @property
    def description(self):return self._description
    def parameters(self):return schema(self._properties)
    def _owner(self,kw):return kw.pop("trusted_user_id"),kw.pop("trusted_chat_id")

class ListDocuments(DocumentTool):
    def execute(self,**kw):
        u,c=self._owner(kw);return {"documents":[{"id":x.id,"filename":x.safe_filename,"type":x.document_type,"created_at":x.created_at.isoformat()} for x in self.service.list_documents(u,c)]}
class Metadata(DocumentTool):
    def execute(self,document_id,**kw):
        u,c=self._owner(kw);x=self.service.storage.get(document_id,u,c)
        return {"document":None if not x else {"id":x.id,"filename":x.safe_filename,"type":x.document_type,"size":x.file_size,"pages":x.extracted_page_count,"expires_at":x.expires_at.isoformat()}}
class Search(DocumentTool):
    def execute(self,query,**kw):
        u,c=self._owner(kw);x=self.service.storage.active(u,c)
        if not x:return {"matches":[]}
        chunks=self.service.chunker.search(self.service.storage.chunks(x.id,u,c),query,limit=self.service.max_chunks)
        return {"matches":[{"index":z.index,"text":z.text,"source":provenance(z)} for z in chunks]}
class Chunks(DocumentTool):
    def execute(self,document_id,start_index,count,**kw):
        u,c=self._owner(kw);count=min(count,self.service.max_chunks);chunks=self.service.storage.chunks(document_id,u,c)[start_index:start_index+count]
        return {"chunks":[{"index":z.index,"text":z.text,"source":provenance(z)} for z in chunks]}
class Compare(DocumentTool):
    def execute(self,first_document_id,second_document_id,query,**kw):
        u,c=self._owner(kw);return {"first":self.service.context(u,c,query,first_document_id),"second":self.service.context(u,c,query,second_document_id)}
class Forget(DocumentTool):
    def execute(self,document_id,**kw):u,c=self._owner(kw);return {"forgotten":self.service.forget(u,c,document_id or None)}

def register_document_tools(registry,service):
    registry.register(ListDocuments(service,"list_documents","List the current Telegram user's active documents.",{}))
    registry.register(Metadata(service,"get_document_metadata","Get metadata for an owned document.",{"document_id":{"type":"string"}}))
    registry.register(Search(service,"search_document","Search the active owned document locally.",{"query":{"type":"string"}}))
    registry.register(Chunks(service,"get_document_chunks","Read a bounded range of owned document chunks.",{"document_id":{"type":"string"},"start_index":{"type":"integer"},"count":{"type":"integer"}}))
    registry.register(Compare(service,"compare_documents","Compare two owned documents.",{"first_document_id":{"type":"string"},"second_document_id":{"type":"string"},"query":{"type":"string"}}))
    registry.register(Forget(service,"forget_document","Delete and deactivate an owned document.",{"document_id":{"type":"string"}}))
