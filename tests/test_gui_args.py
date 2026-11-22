"""The GUI's command line (no window opens)."""

from moonlight.gui.app import parse_args


def test_route_names_the_route_to_open_on_the_map() -> None:
    name = "Venore Amazons 20-40"
    assert parse_args(["--route", name]).open_route_on_map == name


def test_no_route_opens_the_first_tab() -> None:
    assert parse_args([]).open_route_on_map is None
