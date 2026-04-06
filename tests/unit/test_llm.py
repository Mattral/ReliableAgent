"""Unit tests for `reliableagent.llm`."""

from __future__ import annotations

from reliableagent.llm.base import LLMMessage
from reliableagent.llm.mock import MockLLMClient


def test_mock_client_returns_scripted_responses_in_order():
    client = MockLLMClient(responses=["first", "second"])
    r1 = client.complete([LLMMessage(role="user", content="hi")])
    r2 = client.complete([LLMMessage(role="user", content="again")])
    assert r1.text == "first"
    assert r2.text == "second"


def test_mock_client_falls_back_to_default_when_queue_exhausted():
    client = MockLLMClient(responses=["only one"], default_response="fallback")
    client.complete([LLMMessage(role="user", content="a")])
    r2 = client.complete([LLMMessage(role="user", content="b")])
    assert r2.text == "fallback"


def test_mock_client_logs_every_call():
    client = MockLLMClient(responses=["x", "y"])
    client.complete([LLMMessage(role="user", content="1")])
    client.complete([LLMMessage(role="user", content="2")])
    assert len(client.call_log) == 2


def test_mock_client_enqueue_adds_to_end_of_queue():
    client = MockLLMClient(responses=["first"])
    client.enqueue("second")
    assert client.remaining == 2
    assert client.complete([LLMMessage(role="user", content="x")]).text == "first"
    assert client.complete([LLMMessage(role="user", content="x")]).text == "second"


def test_mock_client_response_carries_model_name():
    client = MockLLMClient(responses=["x"], model_name="my-test-model")
    response = client.complete([LLMMessage(role="user", content="hi")])
    assert response.model == "my-test-model"


def test_mock_client_can_be_scripted_with_full_llmresponse_objects():
    from reliableagent.llm.base import LLMResponse

    custom = LLMResponse(text="custom text", model="custom-model", input_tokens=5, output_tokens=2)
    client = MockLLMClient(responses=[custom])
    response = client.complete([LLMMessage(role="user", content="hi")])
    assert response.text == "custom text"
    assert response.model == "custom-model"
