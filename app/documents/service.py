"""Orchestration for validation, extraction, retrieval and lifecycle."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import os
import uuid

from .chunking import DocumentChunker
from .extractors import ExtractionError, SpreadsheetExtractor, TextExtractor
from .formatter import provenance, redact
from .models import DocumentSession
from .storage import DocumentSessionStorage
from .validators import DocumentValidator, ValidationError

class DocumentError(ValueError):
    def __init__(self,code,message):super().__init__(message);self.code=code;self.user_message=message

class DocumentService:
    def __init__(self,storage:DocumentSessionStorage,*,max_file_size_mb=20,max_text_chars=500000,max_pdf_pages=300,max_docx_paragraphs=20000,max_spreadsheet_cells=200000,max_image_pixels=25000000,ttl_hours=24,max_active_per_user=20,max_context_chars=50000,max_chunks_per_request=12):
        self.storage=storage;self.validator=DocumentValidator(max_file_size_mb*1048576)
        self.text=TextExtractor(max_chars=max_text_chars,max_pdf_pages=max_pdf_pages,max_docx_paragraphs=max_docx_paragraphs)
        self.sheet=SpreadsheetExtractor(max_cells=max_spreadsheet_cells,max_chars=max_text_chars);self.chunker=DocumentChunker()
        self.max_image_pixels=max_image_pixels;self.ttl_hours=ttl_hours;self.max_active=max_active_per_user;self.max_context=max_context_chars;self.max_chunks=max_chunks_per_request;self.last_error_code=None;self.cleanup_running=False
    def ingest(self,path:Path,*,user_id:int,chat_id:int,message_id:int,file_id:str,filename:str|None,mime_type:str,file_size:int):
        target=None
        try:
            existing=self.storage.by_message(user_id,chat_id,message_id)
            if existing:return existing
            if len(self.storage.list(user_id,None,self.max_active+1))>=self.max_active:raise DocumentError("ACTIVE_LIMIT","Достигнут лимит активных документов. Забудьте один из старых документов.")
            validated=self.validator.validate_metadata(filename,mime_type,file_size);self.validator.validate_content(path,validated.extension)
            digest=hashlib.sha256(path.read_bytes()).hexdigest();identifier=uuid.uuid4().hex
            target=self.storage.storage_path/f"{identifier}{validated.extension}";os.replace(path,target);os.chmod(target,0o600)
            parts=[]
            if validated.document_type=="xlsx":parts=self.sheet.extract(target)
            elif validated.document_type=="image":self._validate_image(target)
            else:parts=self.text.extract(target,validated.document_type)
            chunks=self.chunker.chunk(parts);now=datetime.now(timezone.utc)
            session=DocumentSession(identifier,user_id,chat_id,message_id,hashlib.sha256(file_id.encode()).hexdigest(),filename or validated.safe_filename,validated.safe_filename,mime_type,file_size,digest,"ready",validated.document_type,sum(len(p.text) for p in parts),max((p.provenance.page or 0 for p in parts),default=0),now,now+timedelta(hours=self.ttl_hours),now,None,True,target)
            self.storage.insert(session,chunks);return session
        except (ValidationError,ExtractionError,DocumentError) as error:
            self.last_error_code=error.code
            try:path.unlink(missing_ok=True)
            except OSError:pass
            if target is not None:
                try:target.unlink(missing_ok=True)
                except OSError:pass
            if isinstance(error,DocumentError):raise
            raise DocumentError(error.code,error.user_message) from error
    def _validate_image(self,path):
        from PIL import Image
        try:
            with Image.open(path) as image:
                image.verify();pixels=image.width*image.height
            if pixels>self.max_image_pixels:raise DocumentError("IMAGE_TOO_LARGE","Изображение превышает допустимое число пикселей.")
        except DocumentError:raise
        except Exception as error:raise DocumentError("MALFORMED_IMAGE","Изображение повреждено.") from error
    def context(self,user_id,chat_id,query,document_id=None,page=None,sheet=None):
        session=self.storage.get(document_id,user_id,chat_id) if document_id else self.storage.active(user_id,chat_id)
        if not session:return ""
        chunks=self.storage.chunks(session.id,user_id,chat_id)
        selected=self.chunker.search(chunks,query,page=page,sheet=sheet,limit=self.max_chunks)
        if not selected and chunks:selected=chunks[:min(3,self.max_chunks)]
        rendered=[];used=0
        for chunk in selected:
            block=f"[{provenance(chunk)}]\n{chunk.text}"
            if used+len(block)>self.max_context:break
            rendered.append(block);used+=len(block)
        return "DOCUMENT CONTEXT (private; never web-search or persist in memory):\n"+"\n\n".join(rendered) if rendered else ""
    def list_documents(self,user_id,chat_id):return self.storage.list(user_id,chat_id,self.max_active)
    def forget(self,user_id,chat_id,document_id=None):
        session=self.storage.get(document_id,user_id,chat_id) if document_id else self.storage.active(user_id,chat_id)
        return bool(session and self.storage.forget(session.id,user_id,chat_id))
    def cleanup(self,apply=False):
        self.cleanup_running=True
        try:return self.storage.cleanup(apply)
        finally:self.cleanup_running=False
    @staticmethod
    def redact(text):return redact(text)
