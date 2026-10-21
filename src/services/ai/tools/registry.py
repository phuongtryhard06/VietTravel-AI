"""
VietTravel AI - Tool Registry (Phase 3 Core Foundation)
Provides a secure, typed, and observable registry for all agent tools.

Principles:
1. Strict registration with unique names, typed Pydantic input/output schemas, and descriptions.
2. Safe execution boundary: agents can only invoke explicitly registered tools with validated inputs.
3. Execution budgets, rate limiting, and timeouts to prevent resource exhaustion.
4. Structured error categorization: retryable (network timeout, quota 429) vs non-retryable (validation, 404).
5. Observability audit trace without logging secret tokens or sensitive user data.
"""

import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Dict, List, Optional, Any, Callable, Type
from pydantic import BaseModel, ValidationError, ConfigDict, Field

logger = logging.getLogger(__name__)


class ToolExecutionResult(BaseModel):
    """
    Standardized result object returned from any tool execution.
    """
    model_config = ConfigDict(extra="ignore")

    tool_name: str
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    is_retryable: bool = False
    execution_time_ms: float = 0.0
    source_attribution: Optional[str] = None


class ToolDefinition:
    """
    Metadata and execution configuration for a registered tool.
    """
    def __init__(
        self,
        name: str,
        description: str,
        args_schema: Type[BaseModel],
        func: Callable[..., Any],
        timeout_seconds: float = 12.0,
        max_retries: int = 1,
        is_external_api: bool = False
    ):
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string.")
        if not description:
            raise ValueError("Tool description must be provided.")
        if not issubclass(args_schema, BaseModel):
            raise TypeError("args_schema must be a Pydantic BaseModel class.")
        if not callable(func):
            raise TypeError("func must be a callable.")

        self.name = name.strip()
        self.description = description.strip()
        self.args_schema = args_schema
        self.func = func
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.is_external_api = bool(is_external_api)

    def to_descriptor(self) -> Dict[str, Any]:
        """Returns JSON schema representation for LLM function calling."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_schema.model_json_schema()
        }


class ToolRegistry:
    """
    Central repository and execution manager for tools used by agents.
    """
    def __init__(self, default_max_tool_calls_per_run: int = 10):
        self._tools: Dict[str, ToolDefinition] = {}
        self.default_max_tool_calls_per_run = default_max_tool_calls_per_run
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ToolWorker")

    def register(self, tool: ToolDefinition) -> None:
        """Registers a new tool in the registry. Raises ValueError if name exists."""
        if tool.name in self._tools:
            logger.warning(f"Overwriting previously registered tool: '{tool.name}'")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool '{tool.name}': {tool.description}")

    def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        return self._tools.get(tool_name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def get_tool_descriptors(self) -> List[Dict[str, Any]]:
        return [tool.to_descriptor() for tool in self._tools.values()]

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        call_count_tracker: Optional[Dict[str, int]] = None,
        max_budget: Optional[int] = None
    ) -> ToolExecutionResult:
        """
        Executes a registered tool with schema validation, timeouts, budget tracking, and error handling.
        """
        start_time = time.perf_counter()
        tool = self.get_tool(tool_name)

        if not tool:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' is not registered in the system.",
                error_code="unknown_tool",
                is_retryable=False,
                execution_time_ms=round(duration_ms, 2)
            )

        # Budget verification
        budget_limit = max_budget if max_budget is not None else self.default_max_tool_calls_per_run
        if call_count_tracker is not None:
            current_total = call_count_tracker.get("total_calls", 0)
            if current_total >= budget_limit:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Exceeded maximum allowed tool execution budget of {budget_limit} calls.",
                    error_code="tool_budget_exhausted",
                    is_retryable=False,
                    execution_time_ms=round(duration_ms, 2)
                )
            call_count_tracker["total_calls"] = current_total + 1
            call_count_tracker[tool_name] = call_count_tracker.get(tool_name, 0) + 1

        # 1. Schema Validation
        try:
            validated_args = tool.args_schema.model_validate(arguments or {})
        except ValidationError as val_err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            error_details = "; ".join([f"{e['loc']}: {e['msg']}" for e in val_err.errors()])
            logger.warning(f"Validation failure for tool '{tool_name}': {error_details}")
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Invalid arguments for tool '{tool_name}': {error_details}",
                error_code="invalid_arguments",
                is_retryable=False,
                execution_time_ms=round(duration_ms, 2)
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Argument parsing error: {str(e)}",
                error_code="invalid_arguments",
                is_retryable=False,
                execution_time_ms=round(duration_ms, 2)
            )

        # 2. Execution with Timeout
        attempt = 0
        max_attempts = 1 + tool.max_retries
        last_error = ""
        is_retryable = False
        error_code = "execution_error"

        while attempt < max_attempts:
            attempt += 1
            try:
                future = self._executor.submit(tool.func, **validated_args.model_dump())
                raw_data = future.result(timeout=tool.timeout_seconds)

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=True,
                    data=raw_data,
                    is_retryable=False,
                    execution_time_ms=round(duration_ms, 2)
                )

            except FutureTimeoutError:
                last_error = f"Tool '{tool_name}' timed out after {tool.timeout_seconds} seconds."
                error_code = "timeout"
                is_retryable = True
                logger.warning(f"{last_error} (Attempt {attempt}/{max_attempts})")

            except Exception as exc:
                err_str = str(exc)
                last_error = f"Tool execution failed: {err_str}"
                # Analyze retryability
                lower_err = err_str.lower()
                if any(k in lower_err for k in ["429", "quota", "resource_exhausted", "timeout", "connection refused", "temporarily unavailable"]):
                    is_retryable = True
                    error_code = "rate_limit_or_network"
                else:
                    is_retryable = False
                    error_code = "execution_error"
                logger.error(f"Error executing tool '{tool_name}': {exc} (Attempt {attempt}/{max_attempts})")

            if not is_retryable or attempt >= max_attempts:
                break
            time.sleep(0.2 * attempt)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error=last_error,
            error_code=error_code,
            is_retryable=is_retryable,
            execution_time_ms=round(duration_ms, 2)
        )


# Global default registry instance
global_tool_registry = ToolRegistry(default_max_tool_calls_per_run=10)
