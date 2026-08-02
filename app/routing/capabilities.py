"""Closed capability registry: the model cannot invent executable tools."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str
    external: bool = False
    personal: bool = False


CAPABILITIES: dict[str, Capability] = {
    item.name: item for item in (
        Capability("general_llm", "Knowledge, reasoning, writing and translation"),
        Capability("web_search", "Current public information", external=True),
        Capability("location", "Owned saved place and timezone", personal=True),
        Capability("ssh", "Allowlisted read-only server runtime", personal=True),
        Capability("crypto_control", "Read-only crypto-bot runtime", personal=True),
        Capability("documents", "Owned uploaded document context", personal=True),
        Capability("memory", "Owned stored facts and preferences", personal=True),
        Capability("reminders", "Owned reminder operations", personal=True),
    )
}


def validate_capabilities(names: tuple[str, ...] | list[str]) -> bool:
    return all(name in CAPABILITIES for name in names)
