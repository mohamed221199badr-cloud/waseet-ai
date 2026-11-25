"""Regression guard on the synthetic Arabic evaluation sets.

The hard rule is safety: zero wrong products, zero wrong quantities, zero false accepts.
The accuracy floors sit a little under the measured numbers so a real regression fails.
Run `python -m tests.eval.evaluate` (and `--holdout`) to see the full table.
"""

from tests.eval.dataset import CASES, HOLDOUT
from tests.eval.evaluate import run


def _check(cases, floor_items, floor_messages):
    c = run(cases)["counts"]
    assert c["wrong_product"] == 0
    assert c["wrong_quantity"] == 0
    assert c["false_accept"] == 0
    assert c["p_correct"] / c["p_total"] >= floor_items
    assert c["messages_ok"] / c["messages"] >= floor_messages


def test_dev_set_safety_and_accuracy():
    assert len(CASES) >= 100
    _check(CASES, floor_items=0.90, floor_messages=0.93)


def test_holdout_set_safety_and_accuracy():
    assert len(HOLDOUT) >= 40
    _check(HOLDOUT, floor_items=0.92, floor_messages=0.95)
