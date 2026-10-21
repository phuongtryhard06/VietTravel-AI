"""
VietTravel AI - Core State Model (Phase 2)
Provides typed, structured state management for the multi-agent travel intelligence system.

Principles:
1. Strict type checking via Pydantic v2.
2. Clear provenance distinction: user-provided vs model-inferred vs unknown/unverified.
3. Selective partial updates: modifying one constraint (e.g. destination) preserves unaffected constraints.
4. No hallucinated factual claims or artificial defaults for missing data.
5. No sensitive credential leaks into trace records.
"""

from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field, field_validator, ConfigDict


class TripConstraints(BaseModel):
    """
    Extracted travel parameters and user constraints.
    Tracks whether each field was explicitly provided by the user,
    inferred by an agent, or remains unknown.
    """
    model_config = ConfigDict(extra="ignore")

    origin: Optional[str] = Field(default=None, description="Starting city or departure location")
    destination: Optional[str] = Field(default=None, description="Primary travel destination or candidate region")
    candidate_destinations: List[str] = Field(default_factory=list, description="Alternative candidate destinations")
    start_date: Optional[str] = Field(default=None, description="Trip start date (YYYY-MM-DD)")
    end_date: Optional[str] = Field(default=None, description="Trip end date (YYYY-MM-DD)")
    duration_days: Optional[int] = Field(default=None, description="Trip duration in days")
    group_size: int = Field(default=1, ge=1, le=50, description="Total number of travelers")
    budget: Optional[float] = Field(default=None, ge=0.0, description="Total budget in specified currency")
    currency: str = Field(default="VND", description="Currency code (e.g., VND, USD)")
    travel_style: Optional[str] = Field(default=None, description="Travel style: nghỉ dưỡng, khám phá, phượt, gia đình, v.v.")
    interests: List[str] = Field(default_factory=list, description="Specific interests: văn hóa, ẩm thực, check-in, lịch sử, v.v.")
    food_preferences: List[str] = Field(default_factory=list, description="Food choices, allergies, or culinary styles")
    transportation: Optional[str] = Field(default=None, description="Preferred mode of transit: xe máy, ô tô, máy bay, tàu hỏa")
    accessibility_requirements: List[str] = Field(default_factory=list, description="Accessibility or special care requirements")
    explicit_constraints: List[str] = Field(default_factory=list, description="Mandatory constraints stated by user")
    missing_fields: List[str] = Field(default_factory=list, description="Critical information missing for complete itinerary")
    field_provenance: Dict[str, str] = Field(
        default_factory=dict,
        description="Provenance of each field: 'user_provided', 'model_inferred', or 'default'"
    )

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value_str = str(value).split("T")[0].strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value_str):
            raise ValueError(f"Date '{value}' must follow format YYYY-MM-DD")
        try:
            datetime.strptime(value_str, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"Invalid calendar date '{value_str}': {e}")
        return value_str


class EvidenceItem(BaseModel):
    """
    Structured factual claim or retrieved evidence with provenance metadata.
    """
    model_config = ConfigDict(extra="ignore")

    content: str = Field(..., description="Fact or observation content")
    source_type: str = Field(
        ...,
        description="Source category: 'internal_database', 'web_search', 'routing_service', 'deterministic_calc', 'unverified_estimate'"
    )
    source_url: Optional[str] = Field(default=None, description="Canonical URL if fetched from web")
    source_title: Optional[str] = Field(default=None, description="Title of page, document, or record identifier")
    retrieved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO UTC retrieval timestamp"
    )
    verified: bool = Field(default=False, description="Whether this fact has been cross-checked and verified")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context or raw fields")


class AgentTaskTrace(BaseModel):
    """
    Observability audit record for an individual agent execution step.
    """
    model_config = ConfigDict(extra="ignore")

    agent_name: str = Field(..., description="Logical agent name (e.g., 'coordinator', 'intent_agent')")
    status: str = Field(default="pending", description="'pending' | 'running' | 'completed' | 'failed' | 'skipped'")
    input_summary: Optional[str] = Field(default=None, description="Redacted, concise input summary")
    output_summary: Optional[str] = Field(default=None, description="Concise output summary")
    tools_called: List[str] = Field(default_factory=list, description="Names of tools executed during this task")
    error: Optional[str] = Field(default=None, description="Error message if task failed")
    execution_time_ms: float = Field(default=0.0, ge=0.0, description="Duration in milliseconds")
    started_at: Optional[str] = Field(default=None, description="ISO timestamp of task start")
    ended_at: Optional[str] = Field(default=None, description="ISO timestamp of task completion")


class ItineraryStopDraft(BaseModel):
    """
    Individual stop within a draft day itinerary.
    """
    model_config = ConfigDict(extra="ignore")

    order_index: int = Field(..., ge=1, description="1-based stop position in day")
    poi_id: Optional[str] = Field(default=None, description="System POI identifier")
    osm_id: Optional[int] = Field(default=None, description="OpenStreetMap ID if from staging")
    name: str = Field(..., description="Stop or attraction name")
    category: Optional[str] = Field(default=None, description="Category / fclass (e.g., attraction, cafe)")
    address: Optional[str] = Field(default=None, description="Physical address")
    latitude: Optional[float] = Field(default=None, description="WGS84 latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="WGS84 longitude coordinate")
    google_maps_url: Optional[str] = Field(default=None, description="Direct verified Google Maps link")
    image_url: Optional[str] = Field(default=None, description="Reliable image URL or None")
    start_time: Optional[str] = Field(default=None, description="HH:MM start time")
    end_time: Optional[str] = Field(default=None, description="HH:MM end time")
    estimated_duration_minutes: int = Field(default=60, ge=5, le=480, description="Estimated duration in minutes")
    estimated_ticket_price: Optional[float] = Field(default=None, ge=0.0, description="Ticket price if verified")
    opening_hours: Optional[str] = Field(default=None, description="Opening hours from verified source or None")
    notes: Optional[str] = Field(default=None, description="Activity description or rationale")
    verified_source: Optional[str] = Field(default=None, description="Source proving stop existence")


class ItineraryDayDraft(BaseModel):
    """
    A single day within an itinerary draft.
    """
    model_config = ConfigDict(extra="ignore")

    day_number: int = Field(..., ge=1, description="Day index (1, 2, ...)")
    date: Optional[str] = Field(default=None, description="Calendar date YYYY-MM-DD")
    theme: Optional[str] = Field(default=None, description="Day theme (e.g., Khám phá phố cổ và ẩm thực)")
    stops: List[ItineraryStopDraft] = Field(default_factory=list, description="Ordered stops for the day")
    notes: Optional[str] = Field(default=None, description="Day summary or logistics notes")


class ItineraryPlanDraft(BaseModel):
    """
    Full draft itinerary plan across one or multiple days.
    """
    model_config = ConfigDict(extra="ignore")

    draft_id: str = Field(..., description="Unique draft identifier (e.g. draft_abc123)")
    title: str = Field(default="Lịch trình khám phá", description="Itinerary title")
    selected_date: Optional[str] = Field(default=None, description="Base starting date YYYY-MM-DD")
    days: List[ItineraryDayDraft] = Field(default_factory=list, description="Day-by-day plans")
    total_stops: int = Field(default=0, ge=0, description="Total number of stops across all days")
    total_estimated_distance_km: Optional[float] = Field(default=None, ge=0.0, description="Total road or spatial km")
    distance_type: str = Field(default="đường chim bay ước tính", description="How distance was calculated")
    warnings: List[str] = Field(default_factory=list, description="Validation warnings or missing details")
    is_valid: bool = Field(default=False, description="Whether this plan passed constraint verification")


class BudgetBreakdown(BaseModel):
    """
    Deterministic cost breakdown calculated in Python.
    """
    model_config = ConfigDict(extra="ignore")

    currency: str = Field(default="VND", description="Currency (default VND)")
    transportation_cost: float = Field(default=0.0, ge=0.0, description="Transit, taxi, flight, or rental cost")
    accommodation_cost: float = Field(default=0.0, ge=0.0, description="Hotel/homestay cost")
    food_cost: float = Field(default=0.0, ge=0.0, description="Estimated meals and beverages")
    activities_cost: float = Field(default=0.0, ge=0.0, description="Admission tickets and activity fees")
    contingency_cost: float = Field(default=0.0, ge=0.0, description="Contingency buffer (e.g. 10%)")
    total_cost: float = Field(default=0.0, ge=0.0, description="Sum of all categories")
    cost_per_person: float = Field(default=0.0, ge=0.0, description="Total divided by group size")
    group_size: int = Field(default=1, ge=1, description="Travelers count")
    is_estimated: bool = Field(default=True, description="True if any components are estimates rather than confirmed prices")
    calculation_basis: List[str] = Field(default_factory=list, description="Explanation of calculation assumptions")
    exceeds_budget: bool = Field(default=False, description="True if total_cost > budget_limit")
    budget_limit: Optional[float] = Field(default=None, ge=0.0, description="User budget ceiling if defined")


class TravelSessionState(BaseModel):
    """
    Shared working memory and execution state for a user session or trip planning run.
    """
    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(..., description="Unique session or conversation identifier")
    user_id: Optional[int] = Field(default=None, description="Authenticated user ID if available")
    conversation_id: Optional[int] = Field(default=None, description="Database conversation ID")
    language: str = Field(default="vietnamese", description="Detected language code or name")
    user_intent: Optional[str] = Field(default=None, description="Interpreted high-level goal")
    history_ref: List[Dict[str, Any]] = Field(default_factory=list, description="Recent conversation turns")
    constraints: TripConstraints = Field(default_factory=TripConstraints, description="Current accumulated constraints")
    current_plan: Optional[ItineraryPlanDraft] = Field(default=None, description="Current working itinerary draft")
    budget_breakdown: Optional[BudgetBreakdown] = Field(default=None, description="Current budget calculation")
    evidence_items: List[EvidenceItem] = Field(default_factory=list, description="Retrieved factual evidence")
    agent_traces: List[AgentTaskTrace] = Field(default_factory=list, description="Execution audit logs")
    missing_information: List[str] = Field(default_factory=list, description="Critical unresolved items")
    unresolved_questions: List[str] = Field(default_factory=list, description="Questions pending user clarification")
    execution_status: str = Field(default="idle", description="'idle' | 'planning' | 'verifying' | 'completed' | 'error'")
    last_error: Optional[str] = Field(default=None, description="Last runtime or agent error message")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def update_constraints(self, updates: Dict[str, Any], source: str = "user_provided") -> Set[str]:
        """
        Partially updates constraints while preserving all unaffected values.
        Returns the set of fields actually modified.
        """
        if not isinstance(updates, dict):
            return set()

        modified_fields: Set[str] = set()
        allowed_fields = set(TripConstraints.model_fields.keys()) - {"field_provenance"}

        current_dict = self.constraints.model_dump()
        provenance = dict(self.constraints.field_provenance)

        for key, val in updates.items():
            if key in allowed_fields and val is not None:
                # Type validation through partial instantiation
                if key in ("interests", "food_preferences", "accessibility_requirements", "explicit_constraints", "candidate_destinations"):
                    if isinstance(val, str):
                        val = [item.strip() for item in val.split(",") if item.strip()]
                    elif not isinstance(val, list):
                        continue
                if current_dict.get(key) != val:
                    current_dict[key] = val
                    provenance[key] = source
                    modified_fields.add(key)

        current_dict["field_provenance"] = provenance
        # Re-validate complete constraints
        validated_constraints = TripConstraints.model_validate(current_dict)
        self.constraints = validated_constraints
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return modified_fields

    def add_evidence(self, evidence: EvidenceItem) -> None:
        """Adds a verified or unverified evidence item to the session state."""
        self.evidence_items.append(evidence)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_trace(self, trace: AgentTaskTrace) -> None:
        """Records an agent execution step, sanitizing potential credentials."""
        # Sanitization against API keys, passwords, JWTs in trace summaries
        sanitized_trace = trace.model_copy()
        if sanitized_trace.input_summary:
            sanitized_trace.input_summary = self._sanitize_text(sanitized_trace.input_summary)
        if sanitized_trace.output_summary:
            sanitized_trace.output_summary = self._sanitize_text(sanitized_trace.output_summary)
        if sanitized_trace.error:
            sanitized_trace.error = self._sanitize_text(sanitized_trace.error)
        self.agent_traces.append(sanitized_trace)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Strips potential API keys, JWT tokens or passwords from observability strings."""
        if not text:
            return text
        # Redact bearer tokens
        text = re.sub(r"Bearer\s+[A-Za-z0-9\-_\.]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
        # Redact API keys matching AIza... or gsk_... or sk-...
        text = re.sub(r"(AIza[0-9A-Za-z-_]{35}|sk-[A-Za-z0-9]{32,})", "[REDACTED_API_KEY]", text)
        # Redact password fields in JSON
        text = re.sub(r'("password"\s*:\s*)"[^"]+"', r'\1"[REDACTED]"', text, flags=re.IGNORECASE)
        return text
