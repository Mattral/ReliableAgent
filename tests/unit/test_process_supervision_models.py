"""Unit tests for `reliableagent.core.models`'s Phase 3 additions:
`CriterionScores` and `StepCritique`."""

from __future__ import annotations

import pytest

from reliableagent.core.models import CriterionScores, Feedback, StepCritique


def test_criterion_scores_weighted_overall_default_weights():
    scores = CriterionScores(correctness=1.0, efficiency=0.0, safety=0.0)
    assert scores.weighted_overall() == pytest.approx(0.6)


def test_criterion_scores_weighted_overall_all_equal():
    scores = CriterionScores(correctness=0.8, efficiency=0.8, safety=0.8)
    assert scores.weighted_overall() == pytest.approx(0.8)


def test_criterion_scores_weighted_overall_custom_weights():
    scores = CriterionScores(correctness=1.0, efficiency=0.0, safety=0.0)
    overall = scores.weighted_overall(
        correctness_weight=0.0, efficiency_weight=0.0, safety_weight=1.0
    )
    assert overall == pytest.approx(0.0)


def test_criterion_scores_weighted_overall_normalizes_weights():
    scores = CriterionScores(correctness=1.0, efficiency=1.0, safety=1.0)
    overall = scores.weighted_overall(
        correctness_weight=3.0, efficiency_weight=3.0, safety_weight=3.0
    )
    assert overall == pytest.approx(1.0)


def test_criterion_scores_rejects_non_positive_weight_sum():
    scores = CriterionScores(correctness=0.5, efficiency=0.5, safety=0.5)
    with pytest.raises(ValueError):
        scores.weighted_overall(correctness_weight=0.0, efficiency_weight=0.0, safety_weight=0.0)


def test_criterion_scores_enforces_0_to_1_bounds():
    with pytest.raises(Exception):
        CriterionScores(correctness=1.5, efficiency=0.5, safety=0.5)


def test_step_critique_defaults_concern_to_empty_string():
    critique = StepCritique(step_id="s1", verdict=True)
    assert critique.concern == ""


def test_feedback_criterion_scores_defaults_to_none():
    fb = Feedback(plan_id="p1", quality_score=0.5, should_replan=False)
    assert fb.criterion_scores is None
    assert fb.step_critiques == []


def test_feedback_accepts_criterion_scores_and_step_critiques():
    scores = CriterionScores(correctness=0.9, efficiency=0.8, safety=1.0)
    critique = StepCritique(step_id="s1", verdict=False, concern="too slow")
    fb = Feedback(
        plan_id="p1",
        quality_score=0.85,
        should_replan=False,
        criterion_scores=scores,
        step_critiques=[critique],
    )
    assert fb.criterion_scores.correctness == 0.9
    assert len(fb.step_critiques) == 1
    assert fb.step_critiques[0].concern == "too slow"
