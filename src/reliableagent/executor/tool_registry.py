"""Tool Registry: schema-validated, type-safe tool registration and lookup.

Per the roadmap: "Tool Registry manages schema, description, and safe
execution." Tools are plain Python callables with explicit, typed
argument and result schemas (Pydantic models) so that:

    1. The Planner can be given an accurate, structured description of
       every available tool (name, description, argument schema) to
       include in its prompt.
    2. Arguments supplied by the Planner can be validated *before* the
       tool ever runs (see `ToolArgumentValidationError` in
       `reliableagent.exceptions`), turning a whole class of
       hallucinated-argument failures into a guardrail/replanning
       signal instead of a runtime crash.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from reliableagent._compat import BaseModel
from reliableagent.exceptions import ToolArgumentValidationError, ToolNotFoundError


@dataclass(frozen=True)
class ToolSpec:
    """Metadata describing a single registered tool.

    Captures everything the Planner needs to know about a tool without
    needing to inspect the underlying function directly, and
    everything the Executor needs to validate and invoke it safely.
    """

    name: str
    description: str
    func: Callable[..., Any]
    argument_model: type[BaseModel] | None
    timeout_seconds: float = 30.0
    is_async: bool = field(default=False)
    result_validator: object = None

    def to_prompt_schema(self) -> dict[str, Any]:
        """Render a compact, planner-friendly description of this tool.

        Intentionally simple (name/description/argument field names)
        rather than a full JSON Schema dump — keeps planner prompts
        small and readable, which matters for both cost and the
        Planner's ability to reliably produce valid calls.
        """
        fields: list[str] = []
        if self.argument_model is not None:
            hints = get_type_hints(self.argument_model)
            fields = [name for name in hints if name != "model_config"]
        return {
            "name": self.name,
            "description": self.description,
            "arguments": fields,
        }


class ToolRegistry:
    """A registry of callable tools available to the Executor.

    Example:
        >>> registry = ToolRegistry()
        >>> @registry.register(name="add", description="Add two numbers")
        ... def add(a: int, b: int) -> int:
        ...     return a + b
        >>> registry.get("add").func(a=1, b=2)
        3
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str = "",
        argument_model: type[BaseModel] | None = None,
        timeout_seconds: float = 30.0,
        result_validator=None,
    ) -> Callable[..., Any]:
        """Register a tool, usable either as a decorator or a direct call.

        Example (decorator form)::

            @tools.register(description="Search the web")
            def search(query: str) -> str: ...

        Example (direct call form, e.g. for an already-defined function)::

            tools.register(my_search_fn, name="search", description="...")
        """

        def _do_register(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            if tool_name in self._tools:
                raise ValueError(f"Tool '{tool_name}' is already registered.")
            spec = ToolSpec(
                name=tool_name,
                description=description or (inspect.getdoc(fn) or ""),
                func=fn,
                argument_model=argument_model,
                timeout_seconds=timeout_seconds,
                is_async=inspect.iscoroutinefunction(fn),
                result_validator=result_validator,
            )
            self._tools[tool_name] = spec
            return fn

        if func is not None:
            return _do_register(func)
        return _do_register

    def get(self, name: str) -> ToolSpec:
        """Look up a tool spec by name, raising if it doesn't exist."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(
                f"No tool registered with name '{name}'. "
                f"Available tools: {sorted(self._tools)}",
                context={"requested_tool": name, "available_tools": sorted(self._tools)},
            ) from None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def list_specs(self) -> list[ToolSpec]:
        """Return all registered tool specs, for prompt construction."""
        return list(self._tools.values())

    def validate_result(self, name: str, result) -> bool:
        """Run the registered result_validator (if any) against a successful call's output.
        Returns True when no validator is registered (trust-by-default), or False when
        the validator rejects the output or itself raises."""
        spec = self.get(name)
        if spec.result_validator is None:
            return True
        try:
            return bool(spec.result_validator(result))
        except Exception:
            return False

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate `arguments` against the tool's declared argument model, if any.

        Returns the validated (and possibly coerced) arguments dict.
        If the tool has no `argument_model`, arguments pass through
        unchanged (the function's own signature will raise a TypeError
        on a true mismatch, surfaced by the Executor as a
        `ToolExecutionError`).
        """
        spec = self.get(name)
        if spec.argument_model is None:
            return arguments
        try:
            validated = spec.argument_model(**arguments)
        except Exception as exc:  # noqa: BLE001 - normalize any validation error
            raise ToolArgumentValidationError(
                f"Arguments for tool '{name}' failed validation: {exc}",
                context={"tool_name": name, "arguments": arguments},
            ) from exc
        return validated.model_dump(mode="python")
