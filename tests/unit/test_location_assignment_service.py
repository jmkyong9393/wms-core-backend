from app.services.location_assignment_service import (
    _select_first_available_shelf,
)


def test_selects_first_shelf_with_remaining_capacity():
    occupancies = {
        "1": 9999,
        "2": 9998,
        "3": 0,
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=1)
        == "2"
    )


def test_reuses_earlier_shelf_after_stock_leaves():
    occupancies = {
        "1": 9998,
        "2": 10,
        "3": 0,
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=1)
        == "1"
    )


def test_does_not_compact_stock_from_later_shelves():
    occupancies = {
        "1": 9999,
        "2": 1,
        "3": 0,
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=1)
        == "2"
    )


def test_returns_none_when_every_shelf_is_full():
    occupancies = {
        str(shelf): 9999
        for shelf in range(1, 11)
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=1)
        is None
    )


def test_skips_inactive_or_unavailable_shelf_entries():
    occupancies = {
        "2": 0,
        "3": 0,
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=1)
        == "2"
    )


def test_selects_first_shelf_that_fits_entire_inbound_batch():
    occupancies = {
        "1": 9997,
        "2": 9998,
        "3": 0,
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=5)
        == "3"
    )


def test_returns_none_instead_of_splitting_inbound_batch():
    occupancies = {
        str(shelf): 9995
        for shelf in range(1, 11)
    }

    assert (
        _select_first_available_shelf(occupancies, required_capacity=5)
        is None
    )
