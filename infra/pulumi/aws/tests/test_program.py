"""The program's guard on its state backend (spec 08 §Layout; ADR 0001)."""

import pytest
from deployment.program import require_local_state


@pytest.mark.parametrize("url", ["", "https://api.pulumi.com", "s3://secret-state-bucket"])
def test_any_backend_other_than_a_local_file_is_refused(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULUMI_BACKEND_URL", url)
    with pytest.raises(RuntimeError, match="file://") as raised:
        require_local_state()
    assert "secret" not in str(raised.value)


def test_a_local_file_backend_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULUMI_BACKEND_URL", "file://C:/data-finops/.local/pulumi")
    require_local_state()
