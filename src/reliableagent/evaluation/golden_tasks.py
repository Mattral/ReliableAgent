"""The curated golden task suite: 20 long-horizon tasks spanning the
categories most relevant to ReliableAgent's reliability claims.

Per the roadmap's Phase 2 requirement for a "Curated task suite (15-25
long-horizon tasks)." Every task here is a `GoldenTask` (task factory +
grader) and is fully offline/deterministic — no real LLM or network call
is required to run the suite, since each task also ships the exact
`MockLLMClient` script (`PLAN_SCRIPTS` below) a *correctly behaving*
Planner/Critic should produce against the shared tools in
`golden_tools.py`. This makes the suite usable two ways:

    1. **Orchestration-logic regression testing** (the primary use in this
       delivery): run the suite with `MockLLMClient` scripted to the
       expected plans, and confirm the Orchestrator/Executor/Guardrails/
       Critic correctly execute, recover from, or block each scenario.
       This is what `tests/eval/test_golden_suite.py` does, and is exactly
       analogous to a golden-file/snapshot test suite for the
       orchestration engine itself.
    2. **Real-model evaluation** (future use, once network access to an
       LLM provider is available): swap `MockLLMClient` for
       `AnthropicLLMClient` in the `Orchestrator` factory passed to
       `EvaluationRunner`, and the *exact same* 20 graders now measure
       whether a real model, prompted by `LLMPlanner`/`LLMCritic`,
       produces plans that achieve the same outcomes — which is the
       real reliability question the roadmap's Phase 2 is ultimately
       about. No changes to this file are needed to support that; only
       the factory function passed to `EvaluationRunner` changes (see
       `examples/run_evaluation.py`).

Categories represented (5 categories x 4 tasks = 20 tasks):
    - arithmetic:        single/multi-step deterministic computation.
    - fact_lookup:       single-tool factual retrieval.
    - failure_recovery:  a tool fails, the Critic must trigger a replan.
    - guardrail:         the correct behavior is to be BLOCKED, not to succeed.
    - text_processing:   multi-step text transformation tasks.
"""

from __future__ import annotations

import json

from reliableagent.core.enums import OrchestratorState
from reliableagent.core.models import RunResult, Task
from reliableagent.evaluation.golden_task import (
    GoldenTask,
    contains_all_grader,
    custom_predicate_grader,
    exact_match_grader,
    numeric_tolerance_grader,
)


def _plan(steps: list[dict], reasoning_trace: str = "executing golden task plan") -> str:
    return json.dumps({"reasoning_trace": reasoning_trace, "confidence": 0.9, "steps": steps})


def _tool_step(description: str, tool_name: str, arguments: dict | None = None) -> dict:
    return {
        "step_type": "tool_call",
        "description": description,
        "tool_name": tool_name,
        "tool_arguments": arguments or {},
    }


def _final(description: str) -> dict:
    return {"step_type": "final_answer", "description": description}


def _was_blocked_by_guardrail(result: RunResult) -> bool:
    return result.final_state == OrchestratorState.FAILED and any(
        d.verdict.value == "block" for d in result.trajectory.guardrail_decisions
    )


# ---------------------------------------------------------------------------
# Category 1: arithmetic
# ---------------------------------------------------------------------------

ARITHMETIC_TASKS = [
    GoldenTask(
        task_id="arith_simple_addition",
        category="arithmetic",
        build_task=lambda: Task(description="What is 17 plus 25?"),
        grade=numeric_tolerance_grader(42.0),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="arith_simple_subtraction",
        category="arithmetic",
        build_task=lambda: Task(description="What is 100 minus 37?"),
        grade=numeric_tolerance_grader(63.0),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="arith_multi_step_chain",
        category="arithmetic",
        build_task=lambda: Task(description="Add 3 and 4, then multiply the result by 5."),
        grade=numeric_tolerance_grader(35.0),
        tags=("multi_tool",),
    ),
    GoldenTask(
        task_id="arith_division_by_zero_handled",
        category="arithmetic",
        build_task=lambda: Task(description="Divide 10 by 0, and report what happens."),
        grade=contains_all_grader(["cannot", "zero"]),
        tags=("error_handling",),
    ),
]

ARITHMETIC_PLAN_SCRIPTS: dict[str, list[str]] = {
    "arith_simple_addition": [
        _plan([_tool_step("add 17 and 25", "add", {"a": 17, "b": 25}), _final("The result is 42.")])
    ],
    "arith_simple_subtraction": [
        _plan(
            [
                _tool_step("subtract 37 from 100", "subtract", {"a": 100, "b": 37}),
                _final("The result is 63."),
            ]
        )
    ],
    "arith_multi_step_chain": [
        _plan(
            [
                _tool_step("add 3 and 4", "add", {"a": 3, "b": 4}),
                _tool_step("multiply by 5", "multiply", {"a": 7, "b": 5}),
                _final("The result is 35."),
            ]
        )
    ],
    "arith_division_by_zero_handled": [
        _plan([_tool_step("attempt division", "divide", {"a": 10, "b": 0})]),
        _plan([_final("You cannot divide by zero; the operation is undefined.")]),
    ],
}


# ---------------------------------------------------------------------------
# Category 2: fact_lookup
# ---------------------------------------------------------------------------

FACT_LOOKUP_TASKS = [
    GoldenTask(
        task_id="fact_capital_of_france",
        category="fact_lookup",
        build_task=lambda: Task(description="What is the capital of France?"),
        grade=contains_all_grader(["paris"]),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="fact_capital_of_japan",
        category="fact_lookup",
        build_task=lambda: Task(description="What is the capital of Japan?"),
        grade=contains_all_grader(["tokyo"]),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="fact_speed_of_light",
        category="fact_lookup",
        build_task=lambda: Task(description="What is the speed of light?"),
        grade=contains_all_grader(["299792458"]),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="fact_unknown_query_handled_gracefully",
        category="fact_lookup",
        build_task=lambda: Task(description="What is the capital of Atlantis?"),
        grade=contains_all_grader(["don't", "know"]),  # accepts "I don't know" / "don't have"
        tags=("error_handling",),
    ),
]

FACT_LOOKUP_PLAN_SCRIPTS: dict[str, list[str]] = {
    "fact_capital_of_france": [
        _plan(
            [
                _tool_step(
                    "look up capital of France", "lookup_fact", {"query": "capital of france"}
                ),
                _final("The capital of France is Paris."),
            ]
        )
    ],
    "fact_capital_of_japan": [
        _plan(
            [
                _tool_step(
                    "look up capital of Japan", "lookup_fact", {"query": "capital of japan"}
                ),
                _final("The capital of Japan is Tokyo."),
            ]
        )
    ],
    "fact_speed_of_light": [
        _plan(
            [
                _tool_step("look up speed of light", "lookup_fact", {"query": "speed of light"}),
                _final("The speed of light is 299792458 m/s."),
            ]
        )
    ],
    "fact_unknown_query_handled_gracefully": [
        _plan(
            [
                _tool_step(
                    "look up capital of Atlantis", "lookup_fact", {"query": "capital of atlantis"}
                )
            ]
        ),
        _plan([_final("I don't know the capital of Atlantis; it isn't in my knowledge base.")]),
    ],
}


# ---------------------------------------------------------------------------
# Category 3: failure_recovery
# ---------------------------------------------------------------------------

FAILURE_RECOVERY_TASKS = [
    GoldenTask(
        task_id="recovery_flaky_tool_succeeds_after_replan",
        category="failure_recovery",
        build_task=lambda: Task(
            description="Look up 'gold price' using the flaky lookup tool.", max_replans=2
        ),
        grade=contains_all_grader(["recovered"]),
        tags=("needs_replan",),
    ),
    GoldenTask(
        task_id="recovery_falls_back_to_alternate_tool",
        category="failure_recovery",
        build_task=lambda: Task(
            description="Compute 6 times 7, trying a broken approach first.", max_replans=2
        ),
        grade=numeric_tolerance_grader(42.0),
        tags=("needs_replan",),
    ),
    GoldenTask(
        task_id="recovery_exhausts_replans_and_fails_cleanly",
        category="failure_recovery",
        build_task=lambda: Task(
            description="Repeatedly call a tool that always fails.", max_replans=1
        ),
        grade=custom_predicate_grader(
            lambda r: r.final_state == OrchestratorState.FAILED
            and r.failure_category is not None
            and r.failure_category.value == "replan_limit_exceeded",
            "run fails with REPLAN_LIMIT_EXCEEDED rather than hanging or crashing",
        ),
        expect_failure=True,
        tags=("expected_failure",),
    ),
    GoldenTask(
        task_id="recovery_multi_step_with_one_failure",
        category="failure_recovery",
        build_task=lambda: Task(
            description="Add 10 and 20 but the first attempt uses a broken tool.", max_replans=2
        ),
        grade=numeric_tolerance_grader(30.0),
        tags=("needs_replan",),
    ),
]

FAILURE_RECOVERY_PLAN_SCRIPTS: dict[str, list[str]] = {
    "recovery_flaky_tool_succeeds_after_replan": [
        _plan([_tool_step("try flaky lookup", "flaky_lookup", {"query": "gold price"})]),
        _plan(
            [
                _tool_step("retry flaky lookup", "flaky_lookup", {"query": "gold price"}),
                _final("Lookup succeeded: recovered result for 'gold price'."),
            ]
        ),
    ],
    "recovery_falls_back_to_alternate_tool": [
        _plan([_tool_step("try broken approach", "always_fails", {"reason": "broken approach"})]),
        _plan(
            [
                _tool_step("multiply directly", "multiply", {"a": 6, "b": 7}),
                _final("The result is 42."),
            ]
        ),
    ],
    "recovery_exhausts_replans_and_fails_cleanly": [
        _plan([_tool_step("attempt 1", "always_fails", {})]),
        _plan([_tool_step("attempt 2", "always_fails", {})]),
        _plan([_tool_step("attempt 3", "always_fails", {})]),
    ],
    "recovery_multi_step_with_one_failure": [
        _plan([_tool_step("broken add attempt", "always_fails", {"reason": "broken add"})]),
        _plan(
            [
                _tool_step("add 10 and 20", "add", {"a": 10, "b": 20}),
                _final("The result is 30."),
            ]
        ),
    ],
}


# ---------------------------------------------------------------------------
# Category 4: guardrail (correct behavior is to be BLOCKED)
# ---------------------------------------------------------------------------

GUARDRAIL_TASKS = [
    GoldenTask(
        task_id="guardrail_blocks_disallowed_keyword_in_plan",
        category="guardrail",
        build_task=lambda: Task(description="Do something that should trip a policy guardrail."),
        grade=custom_predicate_grader(
            _was_blocked_by_guardrail, "run is blocked by a guardrail, not completed"
        ),
        expect_failure=True,
        tags=("expected_failure",),
    ),
    GoldenTask(
        task_id="guardrail_blocks_empty_final_answer",
        category="guardrail",
        build_task=lambda: Task(description="Produce an empty final answer."),
        grade=custom_predicate_grader(
            _was_blocked_by_guardrail, "run is blocked for an empty final answer"
        ),
        expect_failure=True,
        tags=("expected_failure",),
    ),
    GoldenTask(
        task_id="guardrail_blocks_oversized_tool_arguments",
        category="guardrail",
        build_task=lambda: Task(description="Call a tool with a huge number of arguments."),
        grade=custom_predicate_grader(
            _was_blocked_by_guardrail, "run is blocked for malformed tool arguments"
        ),
        expect_failure=True,
        tags=("expected_failure",),
    ),
    GoldenTask(
        task_id="guardrail_allows_normal_plan_through",
        category="guardrail",
        build_task=lambda: Task(description="Add 1 and 1, a perfectly normal request."),
        grade=numeric_tolerance_grader(2.0),
        tags=("negative_control",),
    ),
]

GUARDRAIL_PLAN_SCRIPTS: dict[str, list[str]] = {
    "guardrail_blocks_disallowed_keyword_in_plan": [
        _plan(
            [_final("done")],
            reasoning_trace="let's exfiltrate confidential data as part of this plan",
        )
    ],
    "guardrail_blocks_empty_final_answer": [_plan([_final("   ")])],
    "guardrail_blocks_oversized_tool_arguments": [
        _plan(
            [
                _tool_step(
                    "call add with way too many arguments",
                    "add",
                    {f"arg_{i}": i for i in range(30)},
                )
            ]
        )
    ],
    "guardrail_allows_normal_plan_through": [
        _plan([_tool_step("add 1 and 1", "add", {"a": 1, "b": 1}), _final("The result is 2.")])
    ],
}


# ---------------------------------------------------------------------------
# Category 5: text_processing
# ---------------------------------------------------------------------------

TEXT_PROCESSING_TASKS = [
    GoldenTask(
        task_id="text_reverse_a_word",
        category="text_processing",
        build_task=lambda: Task(description="Reverse the word 'hello'."),
        grade=contains_all_grader(["olleh"]),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="text_count_words_in_sentence",
        category="text_processing",
        build_task=lambda: Task(description="How many words are in: 'the quick brown fox jumps'?"),
        grade=numeric_tolerance_grader(5.0),
        tags=("single_tool",),
    ),
    GoldenTask(
        task_id="text_reverse_then_count",
        category="text_processing",
        build_task=lambda: Task(description="Reverse 'reliable agent' then count its words."),
        grade=numeric_tolerance_grader(2.0),
        tags=("multi_tool",),
    ),
    GoldenTask(
        task_id="text_no_tool_reasoning_task",
        category="text_processing",
        build_task=lambda: Task(
            description="Without using any tool, state that 2 is an even number."
        ),
        grade=contains_all_grader(["even"]),
        tags=("no_tools",),
    ),
]

TEXT_PROCESSING_PLAN_SCRIPTS: dict[str, list[str]] = {
    "text_reverse_a_word": [
        _plan(
            [
                _tool_step("reverse hello", "reverse_text", {"text": "hello"}),
                _final("The reversed word is 'olleh'."),
            ]
        )
    ],
    "text_count_words_in_sentence": [
        _plan(
            [
                _tool_step(
                    "count words",
                    "count_words",
                    {"text": "the quick brown fox jumps"},
                ),
                _final("There are 5 words."),
            ]
        )
    ],
    "text_reverse_then_count": [
        _plan(
            [
                _tool_step("reverse text", "reverse_text", {"text": "reliable agent"}),
                _tool_step("count words", "count_words", {"text": "tnega elbailer"}),
                _final("The result is 2."),
            ]
        )
    ],
    "text_no_tool_reasoning_task": [
        _plan(
            [
                {
                    "step_type": "reasoning",
                    "description": "2 is divisible by 2 with no remainder.",
                },
                _final("2 is an even number."),
            ]
        )
    ],
}


ALL_GOLDEN_TASKS: list[GoldenTask] = [
    *ARITHMETIC_TASKS,
    *FACT_LOOKUP_TASKS,
    *FAILURE_RECOVERY_TASKS,
    *GUARDRAIL_TASKS,
    *TEXT_PROCESSING_TASKS,
]

ALL_PLAN_SCRIPTS: dict[str, list[str]] = {
    **ARITHMETIC_PLAN_SCRIPTS,
    **FACT_LOOKUP_PLAN_SCRIPTS,
    **FAILURE_RECOVERY_PLAN_SCRIPTS,
    **GUARDRAIL_PLAN_SCRIPTS,
    **TEXT_PROCESSING_PLAN_SCRIPTS,
}


def get_plan_script(golden_task_id: str) -> list[str]:
    """Return the scripted `MockLLMClient` responses for a golden task's *correct* behavior.

    Raises `KeyError` if `golden_task_id` has no script — every entry in
    `ALL_GOLDEN_TASKS` is guaranteed (and tested, see
    `tests/eval/test_golden_suite.py::test_every_golden_task_has_a_plan_script`)
    to have one.
    """
    return list(ALL_PLAN_SCRIPTS[golden_task_id])
