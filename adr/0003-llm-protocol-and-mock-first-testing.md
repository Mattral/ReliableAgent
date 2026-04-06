# ADR 0003: A structural `Protocol` for LLM backends, with mock-first testing

## Status
Accepted.

## Context
The roadmap explicitly calls for "Multiple Planner strategies" and treats
provider lock-in as an architectural smell to avoid. The Planner and Critic
both need to call an LLM, but the rest of the system (Orchestrator,
Guardrails, Memory, Executor) should never need to know which provider — or
even whether a *real* provider — is behind that call. Separately, the
project needed a fast, free, fully deterministic way to test the entire
Orchestrator control loop without making real network calls to an LLM API
on every test run, and (in this specific delivery) without network access
to call one at all during development.

## Decision
Define `LLMClient` in `llm/base.py` as a `typing.Protocol` with a single
method, `complete(messages, *, system, max_tokens, temperature, seed) ->
LLMResponse`, marked `@runtime_checkable`. Ship two concrete
implementations against this same contract: `MockLLMClient` (deterministic,
offline, scriptable via a response queue) and `AnthropicLLMClient` (a real
adapter over the Anthropic Messages API, with the `anthropic` SDK imported
lazily so it's only required if that adapter is actually instantiated).
`LLMPlanner` and `LLMCritic` depend only on the `LLMClient` protocol type
in their constructors — never on either concrete class.

Build and validate the entire framework — Orchestrator, Executor,
Guardrails, Memory, Planner, Critic — against `MockLLMClient` first, with
all 76+ tests passing against it, before writing `AnthropicLLMClient` at
all.

## Alternatives considered

**A: An abstract base class (ABC) that all LLM clients must inherit
from.** Rejected in favor of a pure `Protocol`. A `Protocol` supports
structural typing: any object exposing a compatible `complete(...)` method
satisfies the contract without needing to import or inherit from anything
in `reliableagent`. This matters concretely for anyone who wants to wrap an
existing internal LLM client (at a company already using one) into
ReliableAgent — they don't need to retrofit an inheritance relationship,
just match the method signature. `BaseLLMClient` still exists as an
*optional* convenience ABC for clients that want free `model_name`
bookkeeping, but it is not the contract itself.

**B: Build and test against the real Anthropic API from the start.**
Rejected for this delivery, for two compounding reasons. First, the actual
development sandbox had no network access at all, so this was not even
possible here. Second — and this would have been the right call regardless
of that constraint — a test suite that makes real network calls to a paid
API on every run is slow, costly, flaky (rate limits, transient network
errors unrelated to the code under test), and non-deterministic in ways
that actively undermine the project's own reliability-first thesis. Mocking
the LLM layer specifically (not mocking the Orchestrator, Executor, or
Guardrails — those are exercised for real in every integration test) keeps
the parts of the system this project is actually about under real,
deterministic test coverage.

**C: A single combined "LLM client + Planner" abstraction**, where
prompting strategy and provider connectivity are the same object.
Rejected: this would mean a new Planner *strategy* (e.g. a future
ReAct-style planner) would also have to reimplement provider connectivity,
and a provider swap would require touching every Planner strategy
implementation. Keeping `LLMClient` and `Planner` as separate, independently
swappable layers (a `Planner` *uses* an `LLMClient`, doesn't extend one) is
what makes "swap `MockLLMClient` for `AnthropicLLMClient`" a one-line change
at the `LLMPlanner(...)` call site, documented and tested directly in
`docs/architecture.md` section 6.

## Consequences

**Positive:**
- The full test suite is fast (a complete run takes well under a second),
  free, deterministic, and offline by construction — not as an
  afterthought, but because the architecture made the mock-first path the
  natural one.
- `tests/integration/test_orchestrator.py::
  test_orchestrator_resume_from_checkpoint_completes_without_new_llm_call`
  is able to assert, concretely, that a resumed run makes *zero* new LLM
  calls — a regression that would otherwise be easy to introduce silently
  (resume quietly turning into "re-plan from scratch") is caught by a
  simple assertion on `MockLLMClient.call_log`, not by inspecting logs by
  hand.
- Swapping providers, or adding a third (e.g. a future OpenAI adapter),
  requires zero changes to `Orchestrator`, `LLMPlanner`, or `LLMCritic`.

**Negative:**
- `MockLLMClient`'s scripted-response model means tests must hand-craft
  exact JSON strings that match the Planner/Critic's expected response
  shape (see `tests/helpers.py`'s `plan_json`/`critic_json` builders). This
  is a deliberate, explicit coupling to the current prompt schema in
  `planner/prompts.py` — if that schema changes, the helper functions (and
  only the helper functions, not every individual test) need to be updated
  in lockstep.
- This ADR's "tested without real LLM calls" claim is real but bounded: it
  proves the *orchestration logic* is correct given a well-formed LLM
  response. It does not prove a real model will *reliably produce*
  well-formed responses to the actual prompts in `planner/prompts.py` —
  that's a question for the Phase 2 Evaluation Harness (golden-task runs
  against a real model), explicitly out of scope for this delivery.
