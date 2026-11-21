"""Hypothesis profiles for the property and fuzz tests (#242, #282), and a local time zone
other than UTC for the tests that must not depend on it (#243).

`ci`, the default, is seeded and bounded, so each run checks the same examples. `long`
checks many more random examples. Run it on request:

    HYPOTHESIS_PROFILE=long python -m pytest tests/unit/test_accounting_properties.py
    HYPOTHESIS_PROFILE=long python -m pytest tests/integration/test_reader_fuzz.py
"""

import os
import time
from collections.abc import Iterator

import pytest
from hypothesis import settings

settings.register_profile("ci", max_examples=200, derandomize=True, deadline=None, database=None)
settings.register_profile("long", max_examples=20_000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


@pytest.fixture
def local_zone_not_utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The process's local time zone is UTC+05:30 for the test. The POSIX TZ string needs no
    time-zone database. Windows cannot change the zone of a running process, so it skips."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is POSIX only")
    monkeypatch.setenv("TZ", "IST-5:30")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
