"""Tests for keyboard-shortcut loading in lue.input_handler."""

import io
import json
from contextlib import redirect_stderr

import pytest

from lue import input_handler


@pytest.fixture(autouse=True)
def restore_shortcuts():
    yield
    input_handler.KEYBOARD_SHORTCUTS = input_handler.DEFAULT_KEYBOARD_SHORTCUTS


def test_corrupt_keys_file_warns_and_uses_defaults(tmp_path):
    bad = tmp_path / "keys.json"
    bad.write_text("{ not valid json", encoding="utf-8")

    err = io.StringIO()
    with redirect_stderr(err):
        input_handler.load_keyboard_shortcuts(str(bad))

    assert input_handler.KEYBOARD_SHORTCUTS == input_handler.DEFAULT_KEYBOARD_SHORTCUTS
    assert "keys.json" in err.getvalue()


def test_missing_keys_file_warns_and_uses_defaults(tmp_path):
    err = io.StringIO()
    with redirect_stderr(err):
        input_handler.load_keyboard_shortcuts(str(tmp_path / "nope.json"))

    assert input_handler.KEYBOARD_SHORTCUTS == input_handler.DEFAULT_KEYBOARD_SHORTCUTS
    assert "using defaults" in err.getvalue()


def test_valid_keys_file_loads_silently(tmp_path):
    good = tmp_path / "keys.json"
    custom = {"navigation": {"next_paragraph": "x"}}
    good.write_text(json.dumps(custom), encoding="utf-8")

    err = io.StringIO()
    with redirect_stderr(err):
        input_handler.load_keyboard_shortcuts(str(good))

    assert input_handler.KEYBOARD_SHORTCUTS == custom
    assert err.getvalue() == ""
