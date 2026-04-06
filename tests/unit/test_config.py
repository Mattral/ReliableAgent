"""Unit tests for `reliableagent.config`."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from reliableagent.config import ExecutorConfig, ReliableAgentConfig
from reliableagent.exceptions import ConfigurationError


def test_default_config_is_valid():
    config = ReliableAgentConfig()
    assert config.task_max_steps == 20
    assert config.executor.max_retries == 1


def test_code_based_construction_with_nested_overrides():
    config = ReliableAgentConfig(task_max_steps=50, executor=ExecutorConfig(max_retries=3))
    assert config.task_max_steps == 50
    assert config.executor.max_retries == 3


def test_yaml_roundtrip_preserves_all_fields():
    config = ReliableAgentConfig(task_max_steps=42, llm_model="custom-model")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "config.yaml"
        config.to_yaml(path)
        loaded = ReliableAgentConfig.from_yaml(path)
        assert loaded == config


def test_from_yaml_missing_file_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        ReliableAgentConfig.from_yaml("/path/that/does/not/exist.yaml")


def test_from_yaml_invalid_yaml_syntax_raises_configuration_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "bad.yaml"
        path.write_text("task_max_steps: [unterminated list\n")
        with pytest.raises(ConfigurationError):
            ReliableAgentConfig.from_yaml(path)


def test_from_yaml_schema_violation_raises_configuration_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "bad_schema.yaml"
        path.write_text("task_max_steps: -5\n")  # violates gt=0 constraint
        with pytest.raises(ConfigurationError):
            ReliableAgentConfig.from_yaml(path)


def test_from_dict_rejects_unknown_fields():
    with pytest.raises(ConfigurationError):
        ReliableAgentConfig.from_dict({"this_field_does_not_exist": 123})


def test_nested_executor_config_defaults():
    config = ExecutorConfig()
    assert config.max_retries == 1
    assert config.default_timeout_seconds == 30.0
