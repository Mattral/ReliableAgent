"""Compatibility layer: use real Pydantic v2 if installed, else a fallback.

ReliableAgent's declared dependency is `pydantic>=2.6,<3.0` (see
`pyproject.toml`) and that is what should be used in any normal
installation (`pip install reliableagent`). This module exists solely
so the framework also runs correctly in network-restricted
environments where `pip install pydantic` is not possible — see
`reliableagent._compat._fallback` for the full rationale.

Every other module in the codebase should import `BaseModel`,
`ConfigDict`, `Field`, `field_validator`, and `model_validator` from
`reliableagent._compat`, never from `pydantic` directly.
"""

from __future__ import annotations

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

    PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without pydantic installed
    from reliableagent._compat._fallback import (
        BaseModel,
        ConfigDict,
        Field,
        field_validator,
        model_validator,
    )

    PYDANTIC_AVAILABLE = False

__all__ = [
    "BaseModel",
    "ConfigDict",
    "Field",
    "PYDANTIC_AVAILABLE",
    "field_validator",
    "model_validator",
]
