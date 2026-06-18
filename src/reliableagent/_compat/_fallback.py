"""Pydantic-v2-compatible fallback used only when `pydantic` is not installed.

ReliableAgent is designed to depend on real Pydantic v2 (see
`pyproject.toml`). However, sandboxed/offline environments without
package-registry access cannot `pip install pydantic`. Rather than
writing the framework against loosely-typed dicts in that situation
(which would betray the project's "Explicit Contracts & Modularity"
principle), this module provides a minimal, dependency-free
implementation of the small slice of the Pydantic v2 API that
ReliableAgent actually uses:

    - `BaseModel` with `model_config`, `model_dump()`, `model_dump_json()`,
      `model_copy()`, and constructor-time validation.
    - `Field(...)` for defaults, `default_factory`, and constraint
      metadata (min_length, ge, le, gt, etc.) which are enforced at
      construction time.
    - `field_validator` decorator with the same `(cls, value, info)`
      calling convention used by real Pydantic v2 "after" validators.
    - `ConfigDict` as a passthrough dict-like marker.

Every module in this codebase imports these names from
`reliableagent._compat` rather than `pydantic` directly. The import at
the bottom of this file attempts the real library first:

    try:
        from pydantic import BaseModel, ConfigDict, Field, field_validator
    except ImportError:
        from reliableagent._compat._fallback import (...)

This means: the moment a real `pydantic>=2.6` is available in the
environment (e.g. via `pip install reliableagent`), the framework
transparently uses it with zero code changes elsewhere, and gains
pydantic's full validation, JSON schema, and performance benefits.
The fallback below exists purely to keep the project runnable,
testable, and honest in network-restricted sandboxes.

This module is intentionally NOT a general-purpose Pydantic
replacement. It implements only what ReliableAgent's own models need
and is covered by ReliableAgent's own test suite.

Known gap vs. real Pydantic (documented here rather than left
implicit): this shim does NOT perform strict primitive type
coercion/validation — e.g. assigning a `str` to a field annotated
`int` will NOT raise here, whereas real Pydantic v2 would reject it
(or coerce it, depending on mode). Numeric/length *constraints*
declared via `Field(ge=..., le=..., min_length=..., ...)` ARE enforced
(see `_check_constraints`), which covers every constraint actually
used in this codebase, but a bare type mismatch on an untyped-default
field is not caught by the shim alone. This is acceptable for this
project's purposes (every field that matters has either an
enum/nested-model type, which IS coerced/validated, or an explicit
constraint), but is called out explicitly here so it's never mistaken
for a guarantee the shim doesn't actually provide.
"""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

_MISSING = object()


class ConfigDict(dict):
    """Passthrough stand-in for pydantic.ConfigDict (just a typed dict)."""


@dataclass
class FieldInfo:
    """Holds metadata supplied via `Field(...)`, enforced at construction time."""

    default: Any = _MISSING
    default_factory: Callable[[], Any] | None = None
    description: str = ""
    ge: float | None = None
    le: float | None = None
    gt: float | None = None
    lt: float | None = None
    min_length: int | None = None
    max_length: int | None = None

    @property
    def has_default(self) -> bool:
        return self.default is not _MISSING or self.default_factory is not None

    def get_default(self) -> Any:
        if self.default_factory is not None:
            return self.default_factory()
        if self.default is not _MISSING:
            return self.default
        raise ValueError("FieldInfo has no default")


def Field(  # noqa: N802 (matches pydantic's casing intentionally)
    default: Any = _MISSING,
    *,
    default_factory: Callable[[], Any] | None = None,
    description: str = "",
    ge: float | None = None,
    le: float | None = None,
    gt: float | None = None,
    lt: float | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    **_ignored: Any,
) -> Any:
    """Stand-in for pydantic.Field; returns a FieldInfo consumed by BaseModel."""
    if default is Ellipsis:
        default = _MISSING
    return FieldInfo(
        default=default,
        default_factory=default_factory,
        description=description,
        ge=ge,
        le=le,
        gt=gt,
        lt=lt,
        min_length=min_length,
        max_length=max_length,
    )


class ValidationError(ValueError):
    """Raised when constructing or validating a model fails."""


def field_validator(  # noqa: N802
    field_name: str, *_more_fields: str
) -> Callable[[Callable[..., Any]], classmethod[Any, Any, Any]]:
    """Stand-in for pydantic.field_validator (only supports 'after'-style validators).

    Mirrors real Pydantic v2 usage where `@field_validator` is the outer
    decorator and `@classmethod` is the inner one, e.g.::

        @field_validator("name")
        @classmethod
        def _check(cls, v, info): ...

    so `func` received here may already be a `classmethod` object (when
    `@classmethod` was applied first/innermost) or a plain function.
    """

    field_names = (field_name, *_more_fields)

    def decorator(func: Callable[..., Any]) -> classmethod[Any, Any, Any]:
        plain_func = func.__func__ if isinstance(func, classmethod) else func
        wrapped = classmethod(plain_func)
        wrapped.__validator_fields__ = field_names  # type: ignore[attr-defined]
        return wrapped

    return decorator


class _ValidationInfo:
    """Minimal stand-in for pydantic's ValidationInfo, exposing `.data`."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


def _is_optional(tp: Any) -> tuple[bool, Any]:
    """Return (is_optional, inner_type) for `X | None` / `Optional[X]` annotations.

    Handles both `typing.Union[X, None]` and the PEP 604 `X | None`
    spelling, which produces a `types.UnionType` origin rather than
    `typing.Union` under `get_origin()` — a distinction that's easy to
    miss and silently breaks Optional-field coercion if not handled.
    """
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1 and type(None) in get_args(tp):
            return True, args[0]
    return False, tp


def _coerce_value(value: Any, annotation: Any) -> Any:
    """Best-effort coercion/validation of a single field's value against its annotation.

    This intentionally covers only the patterns actually used in
    ReliableAgent's models: Optional[...], list[Model], plain nested
    BaseModel, Enum, datetime/date, and primitives. It is not a
    general-purpose type system.
    """
    if value is None:
        return None

    is_opt, inner = _is_optional(annotation)
    if is_opt:
        return _coerce_value(value, inner)

    origin = get_origin(annotation)

    # list[T]
    if origin in (list,):
        (item_type,) = get_args(annotation) or (Any,)
        return [_coerce_value(v, item_type) for v in value]

    # dict[K, V] — left as-is (used for free-form metadata/arguments dicts)
    if origin in (dict,):
        return value

    # Nested BaseModel
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(value, annotation):
            return value
        if isinstance(value, dict):
            return annotation(**value)
        return value

    # Enum coercion (allow passing raw values, e.g. "tool_call")
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if isinstance(value, annotation):
            return value
        return annotation(value)

    # datetime / date coercion from ISO strings
    if annotation is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if annotation is date and isinstance(value, str):
        return date.fromisoformat(value)

    return value


def _check_constraints(name: str, value: Any, info: FieldInfo) -> None:
    if value is None:
        return
    if info.min_length is not None and hasattr(value, "__len__"):
        if len(value) < info.min_length:
            raise ValidationError(f"{name}: length must be >= {info.min_length}")
    if info.max_length is not None and hasattr(value, "__len__"):
        if len(value) > info.max_length:
            raise ValidationError(f"{name}: length must be <= {info.max_length}")
    if info.ge is not None and isinstance(value, (int, float)) and value < info.ge:
        raise ValidationError(f"{name}: must be >= {info.ge}")
    if info.le is not None and isinstance(value, (int, float)) and value > info.le:
        raise ValidationError(f"{name}: must be <= {info.le}")
    if info.gt is not None and isinstance(value, (int, float)) and value <= info.gt:
        raise ValidationError(f"{name}: must be > {info.gt}")
    if info.lt is not None and isinstance(value, (int, float)) and value >= info.lt:
        raise ValidationError(f"{name}: must be < {info.lt}")


class BaseModel:
    """Minimal Pydantic-v2-compatible base class. See module docstring."""

    model_config: ClassVar[ConfigDict] = ConfigDict()

    def __init__(self, **data: Any) -> None:
        cls = type(self)
        hints = get_type_hints(cls)
        field_infos: dict[str, FieldInfo] = getattr(cls, "__field_infos__", {})
        plain_defaults: dict[str, Any] = getattr(cls, "__plain_defaults__", {})
        extra_mode = cls.model_config.get("extra", "ignore")

        if extra_mode == "forbid":
            unknown = set(data) - set(hints)
            if unknown:
                raise ValidationError(f"Unexpected field(s): {sorted(unknown)}")

        resolved: dict[str, Any] = {}
        for name, annotation in hints.items():
            if name.startswith("_") or name == "model_config":
                continue
            info = field_infos.get(name)
            if name in data:
                raw = data[name]
            elif info is not None and info.has_default:
                raw = info.get_default()
            elif name in plain_defaults:
                raw = plain_defaults[name]
            elif _is_optional(annotation)[0]:
                raw = None
            else:
                raise ValidationError(f"Missing required field: {name!r}")

            value = _coerce_value(raw, annotation)
            if info is not None:
                _check_constraints(name, value, info)
            resolved[name] = value

        # Run field_validators ("after"-style: receive the coerced value).
        for attr_name in dir(cls):
            if attr_name.startswith("__"):
                continue
            attr = cls.__dict__.get(attr_name)
            validator_fields = getattr(attr, "__validator_fields__", None)
            if not validator_fields:
                continue
            func = attr.__func__
            for fname in validator_fields:
                if fname not in resolved:
                    continue
                info_obj = _ValidationInfo(data=resolved)
                try:
                    new_value = func(cls, resolved[fname], info_obj)
                except TypeError:
                    # Validator without `info` parameter.
                    new_value = func(cls, resolved[fname])
                resolved[fname] = new_value

        for name, value in resolved.items():
            object.__setattr__(self, name, value)

        self.__dict__["_initialized"] = True

        if cls.model_config.get("frozen", False):
            pass  # enforced in __setattr__ below

    def __setattr__(self, name: str, value: Any) -> None:
        if self.__dict__.get("_initialized") and type(self).model_config.get("frozen", False):
            raise ValidationError(
                f"Cannot assign to field {name!r} on frozen model {type(self).__name__}"
            )
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        cls = type(self)
        hints = get_type_hints(cls)
        fields_repr = ", ".join(
            f"{name}={getattr(self, name)!r}" for name in hints if name != "model_config"
        )
        return f"{cls.__name__}({fields_repr})"

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        hints = get_type_hints(type(self))
        return all(
            getattr(self, n) == getattr(other, n) for n in hints if n != "model_config"
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        """Serialize the model into a plain dict (recursively)."""
        hints = get_type_hints(type(self))
        out: dict[str, Any] = {}
        for name in hints:
            if name == "model_config":
                continue
            out[name] = _dump_value(getattr(self, name), mode=mode)
        return out

    def model_dump_json(self, *, indent: int | None = None) -> str:
        """Serialize the model into a JSON string."""
        return json.dumps(self.model_dump(mode="json"), indent=indent, default=str)

    def model_copy(self, *, update: dict[str, Any] | None = None) -> "BaseModel":
        """Return a shallow copy of the model, optionally overriding some fields."""
        data = self.model_dump(mode="python")
        if update:
            data.update(update)
        return type(self)(**data)

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "BaseModel":
        """Construct and validate a model instance from a dict."""
        return cls(**data)


def _dump_value(value: Any, *, mode: str) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode=mode)
    if isinstance(value, Enum):
        return value.value if mode == "json" else value
    if isinstance(value, (datetime, date)):
        return value.isoformat() if mode == "json" else value
    if isinstance(value, list):
        return [_dump_value(v, mode=mode) for v in value]
    if isinstance(value, dict):
        return {k: _dump_value(v, mode=mode) for k, v in value.items()}
    return value


def _collect_field_infos(namespace: dict[str, Any]) -> dict[str, FieldInfo]:
    return {k: v for k, v in namespace.items() if isinstance(v, FieldInfo)}


def _collect_plain_defaults(namespace: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    """Capture simple `name: Type = value` class-level defaults (non-Field, non-method)."""
    plain: dict[str, Any] = {}
    for key in annotations:
        if key in namespace and not isinstance(namespace[key], FieldInfo):
            value = namespace[key]
            if not isinstance(value, (classmethod, staticmethod)) and not callable(value):
                plain[key] = value
            elif isinstance(value, Enum):
                plain[key] = value
    return plain


class _BaseModelMeta(type):
    """Metaclass that hoists `Field()` defaults off the class so they aren't
    mistaken for real default values by `get_type_hints`/instance access,
    mirroring Pydantic's collection of FieldInfo at class-definition time."""

    def __new__(mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any], **kwargs: Any) -> type:
        field_infos = _collect_field_infos(namespace)
        merged_infos: dict[str, FieldInfo] = {}
        for base in bases:
            merged_infos.update(getattr(base, "__field_infos__", {}))
        merged_infos.update(field_infos)

        annotations = namespace.get("__annotations__", {})
        plain_defaults = _collect_plain_defaults(namespace, annotations)
        merged_plain: dict[str, Any] = {}
        for base in bases:
            merged_plain.update(getattr(base, "__plain_defaults__", {}))
        merged_plain.update(plain_defaults)

        # Replace FieldInfo class attributes with nothing (so plain class
        # attribute access doesn't leak FieldInfo objects); annotations
        # remain, which is all get_type_hints needs.
        for key in field_infos:
            del namespace[key]
        # Plain defaults (e.g. `x: Foo = Foo.BAR`) are intentionally left
        # in place as real class attributes too (harmless / expected),
        # but we also stash them so the constructor can find them even
        # if a subclass annotation shadows the attribute lookup order.

        namespace["__field_infos__"] = merged_infos
        namespace["__plain_defaults__"] = merged_plain
        return super().__new__(mcs, name, bases, namespace, **kwargs)


# Rebuild BaseModel with the metaclass applied (can't change metaclass post-hoc
# cleanly, so we redefine via a thin subclass pattern instead).
class BaseModel(BaseModel, metaclass=_BaseModelMeta):  # type: ignore[misc,no-redef]
    pass
