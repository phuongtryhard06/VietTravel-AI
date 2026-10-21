"""Validated, serializable contracts for the travel-agent runtime.

The runtime intentionally uses standard-library dataclasses instead of coupling the
Flask application to a particular Pydantic major version.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4


def utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _clean_text(value: Any, max_length: int = 240) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] if text else None


def _clean_string_list(values: Any, max_items: int = 12) -> List[str]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, dict)):
        return []
    cleaned: List[str] = []
    for value in values:
        text = _clean_text(value, 100)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


@dataclass
class TravelConstraints:
    """Only explicit user constraints are persisted in this object."""

    destination: Optional[str] = None
    departure_location: Optional[str] = None
    start_date: Optional[str] = None
    duration_days: Optional[int] = None
    travelers: Optional[int] = None
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None
    interests: List[str] = field(default_factory=list)
    food_preferences: List[str] = field(default_factory=list)
    transport_preferences: List[str] = field(default_factory=list)
    accessibility_requirements: List[str] = field(default_factory=list)
    explicit_constraints: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TravelConstraints":
        constraints = cls()
        constraints.apply(data or {})
        return constraints

    def apply(self, updates: Dict[str, Any]) -> List[str]:
        """Apply a partial update and return validation errors without guessing values."""
        if not isinstance(updates, dict):
            return ["Constraint updates must be an object."]

        errors: List[str] = []
        for field_name in ("destination", "departure_location", "start_date", "budget_currency"):
            if field_name in updates:
                value = _clean_text(updates[field_name])
                if field_name == "budget_currency" and value:
                    value = value.upper()
                    if len(value) > 8:
                        errors.append("budget_currency is invalid.")
                        continue
                setattr(self, field_name, value)

        for field_name, minimum, maximum in (
            ("duration_days", 1, 60),
            ("travelers", 1, 100),
        ):
            if field_name in updates and updates[field_name] is not None:
                try:
                    value = int(updates[field_name])
                except (TypeError, ValueError):
                    errors.append(f"{field_name} must be an integer.")
                    continue
                if not minimum <= value <= maximum:
                    errors.append(f"{field_name} must be between {minimum} and {maximum}.")
                    continue
                setattr(self, field_name, value)

        if "budget_amount" in updates and updates["budget_amount"] is not None:
            try:
                value = float(Decimal(str(updates["budget_amount"])))
            except (InvalidOperation, ValueError, TypeError):
                errors.append("budget_amount must be numeric.")
            else:
                if value < 0:
                    errors.append("budget_amount cannot be negative.")
                else:
                    self.budget_amount = value

        for field_name in (
            "interests",
            "food_preferences",
            "transport_preferences",
            "accessibility_requirements",
            "explicit_constraints",
        ):
            if field_name in updates:
                raw_value = updates[field_name]
                if isinstance(raw_value, str):
                    raw_value = [part.strip() for part in raw_value.split(",")]
                setattr(self, field_name, _clean_string_list(raw_value))
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Evidence:
    source_type: str
    source_id: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    retrieved_at: str = field(default_factory=utcnow_iso)
    fields: Dict[str, Any] = field(default_factory=dict)
    verification_status: str = "retrieved"
    evidence_id: str = field(default_factory=lambda: new_id("evidence"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentResult:
    agent: str
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    requested_actions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "success": self.success,
            "data": self.data,
            "warnings": self.warnings,
            "errors": self.errors,
            "evidence": [item.to_dict() for item in self.evidence],
            "requested_actions": self.requested_actions,
        }


@dataclass
class TraceEvent:
    event_type: str
    component: str
    status: str
    duration_ms: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentState:
    """Explicit state kept separately from raw conversation history and prompts."""

    session_id: str = field(default_factory=lambda: new_id("trip"))
    user_id: Optional[int] = None
    conversation_id: Optional[int] = None
    constraints: TravelConstraints = field(default_factory=TravelConstraints)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    current_trip_plan: Optional[Dict[str, Any]] = None
    task_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    intermediate_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    traces: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=utcnow_iso)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "AgentState":
        data = data or {}
        evidence = [
            Evidence(**item)
            for item in data.get("evidence", [])
            if isinstance(item, dict) and item.get("source_type")
        ]
        return cls(
            session_id=data.get("session_id") or new_id("trip"),
            user_id=data.get("user_id"),
            conversation_id=data.get("conversation_id"),
            constraints=TravelConstraints.from_dict(data.get("constraints")),
            conversation_history=[item for item in data.get("conversation_history", []) if isinstance(item, dict)][-30:],
            current_trip_plan=data.get("current_trip_plan") if isinstance(data.get("current_trip_plan"), dict) else None,
            task_status=data.get("task_status") if isinstance(data.get("task_status"), dict) else {},
            intermediate_outputs=data.get("intermediate_outputs") if isinstance(data.get("intermediate_outputs"), dict) else {},
            tool_results=[item for item in data.get("tool_results", []) if isinstance(item, dict)][-30:],
            evidence=evidence[-100:],
            unresolved_questions=_clean_string_list(data.get("unresolved_questions"), max_items=20),
            traces=[item for item in data.get("traces", []) if isinstance(item, dict)][-30:],
            updated_at=data.get("updated_at") or utcnow_iso(),
        )

    def add_history(self, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            return
        text = _clean_text(content, 6000)
        if text:
            self.conversation_history.append({"role": role, "content": text, "timestamp": utcnow_iso()})
            self.conversation_history = self.conversation_history[-30:]

    def to_dict(self) -> Dict[str, Any]:
        self.updated_at = utcnow_iso()
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "constraints": self.constraints.to_dict(),
            "conversation_history": self.conversation_history,
            "current_trip_plan": self.current_trip_plan,
            "task_status": self.task_status,
            "intermediate_outputs": self.intermediate_outputs,
            "tool_results": self.tool_results,
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolved_questions": self.unresolved_questions,
            "traces": self.traces,
            "updated_at": self.updated_at,
        }
