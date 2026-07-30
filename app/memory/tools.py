"""Tool-call interfaces for persistent project memory."""

from __future__ import annotations

from typing import Any

from app.memory.manager import MemoryManager
from app.memory.models import MEMORY_TYPES
from app.tools.base import Tool
from app.tools.registry import ToolRegistry


def _strict_schema(
    properties: dict[str, dict[str, Any]], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


class RememberTool(Tool):
    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store one useful, non-secret fact in local project memory. "
            "Use only when the user explicitly asks to remember or states "
            "a durable project decision, fact, preference, todo or config."
        )

    def parameters(self) -> dict[str, Any]:
        return _strict_schema(
            {
                "memory_type": {
                    "type": "string",
                    "enum": sorted(MEMORY_TYPES),
                },
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {
                    "type": "string",
                    "description": "Comma-separated non-secret tags.",
                },
                "project": {"type": "string"},
                "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                "source": {"type": "string"},
            },
            [
                "memory_type",
                "title",
                "content",
                "tags",
                "project",
                "importance",
                "source",
            ],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner_id = int(kwargs.pop("trusted_owner_id", 0))
        record = self.manager.remember(
            memory_type=str(kwargs["memory_type"]),
            title=str(kwargs["title"]),
            content=str(kwargs["content"]),
            tags=_tags(str(kwargs["tags"])),
            project=str(kwargs["project"]),
            importance=int(kwargs["importance"]),
            source=str(kwargs["source"]),
            owner_id=owner_id,
        )
        return {"memory": record.public_dict()}


class ForgetMemoryTool(Tool):
    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "forget"

    @property
    def description(self) -> str:
        return "Soft-delete one memory by its id after identifying it."

    def parameters(self) -> dict[str, Any]:
        return _strict_schema(
            {"memory_id": {"type": "integer", "minimum": 1}},
            ["memory_id"],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner_id = int(kwargs.pop("trusted_owner_id", 0))
        memory_id = int(kwargs["memory_id"])
        return {
            "memory_id": memory_id,
            "forgotten": self.manager.forget(memory_id, owner_id=owner_id),
        }


class UpdateMemoryTool(Tool):
    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "update_memory"

    @property
    def description(self) -> str:
        return "Replace the content of one identified non-secret memory."

    def parameters(self) -> dict[str, Any]:
        return _strict_schema(
            {
                "memory_id": {"type": "integer", "minimum": 1},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "string"},
                "importance": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["memory_id", "title", "content", "tags", "importance"],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner_id = int(kwargs.pop("trusted_owner_id", 0))
        record = self.manager.update(
            int(kwargs["memory_id"]),
            title=str(kwargs["title"]),
            content=str(kwargs["content"]),
            tags=_tags(str(kwargs["tags"])),
            importance=int(kwargs["importance"]),
            owner_id=owner_id,
        )
        return {"memory": record.public_dict()}


class SearchMemoryTool(Tool):
    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return "Search a bounded set of relevant local project memories."

    def parameters(self) -> dict[str, Any]:
        return _strict_schema(
            {
                "query": {"type": "string"},
                "project": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query", "project", "max_results"],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner_id = int(kwargs.pop("trusted_owner_id", 0))
        records = self.manager.search(
            str(kwargs["query"]),
            project=str(kwargs["project"]),
            max_results=int(kwargs["max_results"]),
            owner_id=owner_id,
        )
        return {"memories": [record.public_dict() for record in records]}


class ListProjectMemoryTool(Tool):
    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "list_project_memory"

    @property
    def description(self) -> str:
        return "List a bounded set of active memories for one project."

    def parameters(self) -> dict[str, Any]:
        return _strict_schema(
            {
                "project": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["project", "max_results"],
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner_id = int(kwargs.pop("trusted_owner_id", 0))
        records = self.manager.list_project(
            project=str(kwargs["project"]),
            max_results=int(kwargs["max_results"]),
            owner_id=owner_id,
        )
        return {"memories": [record.public_dict() for record in records]}


class RememberFactTool(RememberTool):
    @property
    def name(self) -> str:
        return "remember_fact"


class RecallMemoryTool(SearchMemoryTool):
    @property
    def name(self) -> str:
        return "recall_memory"


class ForgetMemoryOwnedTool(ForgetMemoryTool):
    @property
    def name(self) -> str:
        return "forget_memory"


class UpdateProjectMemoryTool(Tool):
    def __init__(self, manager: MemoryManager) -> None:
        self.manager=manager
    @property
    def name(self) -> str: return "update_project_memory"
    @property
    def description(self) -> str:
        return "Update durable non-secret project metadata for the current owner."
    def parameters(self) -> dict[str, Any]:
        props={name:{"type":"string"} for name in
               ("project_key","name","description","repository","path",
                "server_name","status","current_milestone")}
        return _strict_schema(props,list(props))
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner=int(kwargs.pop("trusted_owner_id",0))
        key=str(kwargs.pop("project_key"))
        return {"project":self.manager.service.update_project(owner,key,**kwargs).public_dict()}


class GetProjectStatusTool(Tool):
    def __init__(self, manager: MemoryManager) -> None: self.manager=manager
    @property
    def name(self) -> str: return "get_project_memory_status"
    @property
    def description(self) -> str: return "Read stored status for one project."
    def parameters(self) -> dict[str, Any]:
        return _strict_schema({"project_key":{"type":"string"}},["project_key"])
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        owner=int(kwargs.pop("trusted_owner_id",0))
        return self.manager.service.get_project_context(owner,str(kwargs["project_key"]))


def register_memory_tools(
    registry: ToolRegistry, manager: MemoryManager
) -> None:
    for tool in (
        RememberTool(manager),
        ForgetMemoryTool(manager),
        UpdateMemoryTool(manager),
        SearchMemoryTool(manager),
        ListProjectMemoryTool(manager),
        RememberFactTool(manager),
        RecallMemoryTool(manager),
        ForgetMemoryOwnedTool(manager),
        UpdateProjectMemoryTool(manager),
        GetProjectStatusTool(manager),
    ):
        registry.register(tool)
