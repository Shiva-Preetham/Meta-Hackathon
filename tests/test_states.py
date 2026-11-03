import pytest

from signalhq.domain.states import ALLOWED_TRANSITIONS, InvalidTransition, ItemState, can_transition, transition


def test_happy_path_moves_forward_one_stage_at_a_time():
    state = ItemState.RECEIVED
    for target in (ItemState.ENRICHED, ItemState.EMBEDDED, ItemState.CLUSTERED):
        state = transition(state, target)
    assert state is ItemState.CLUSTERED


@pytest.mark.parametrize(
    "src,dst",
    [
        ("received", "embedded"),  # skips enrichment
        ("received", "clustered"),
        ("enriched", "clustered"),
        ("clustered", "received"),
        ("dead", "enriched"),  # dead items must be requeued explicitly
    ],
)
def test_invalid_transitions_raise(src, dst):
    with pytest.raises(InvalidTransition):
        transition(src, dst)


def test_failed_is_reachable_from_every_non_terminal_state():
    for src in (ItemState.RECEIVED, ItemState.ENRICHED, ItemState.EMBEDDED):
        assert can_transition(src, ItemState.FAILED)


def test_retry_from_failed_resumes_forward():
    for dst in (ItemState.ENRICHED, ItemState.EMBEDDED, ItemState.CLUSTERED):
        assert can_transition(ItemState.FAILED, dst)


def test_dead_after_max_attempts_and_requeue():
    assert can_transition(ItemState.FAILED, ItemState.DEAD)
    assert transition(ItemState.DEAD, ItemState.RECEIVED) is ItemState.RECEIVED


def test_same_state_is_an_idempotent_noop():
    for state in ItemState:
        assert transition(state, state) is state


def test_every_state_has_an_entry():
    assert set(ALLOWED_TRANSITIONS) == set(ItemState)
