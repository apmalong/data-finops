"""Hypothesis profiles for the property and fuzz tests (#242, #282).

`ci`, the default, is seeded and bounded, so each run checks the same examples. `long`
checks many more random examples. Run it on request:

    HYPOTHESIS_PROFILE=long python -m pytest tests/unit/test_accounting_properties.py
    HYPOTHESIS_PROFILE=long python -m pytest tests/integration/test_reader_fuzz.py
"""

import os

from hypothesis import settings

settings.register_profile("ci", max_examples=200, derandomize=True, deadline=None, database=None)
settings.register_profile("long", max_examples=20_000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
