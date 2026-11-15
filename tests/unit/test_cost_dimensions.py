"""The Cost Dimensions configuration (spec 09 §Cost Dimensions, ADR 0031; #221): one TOML file,
validated as the `collect` settings are, that names each dimension's sources in order."""

from pathlib import Path

import pytest

from data_finops.contracts.dimensions import (
    CostDimensions,
    Source,
    SourceKind,
)

TEAM_AND_TENANT = """
[dimensions.team]
sources = ["query_comment.team", "openlineage_tag.team", "workgroup_map"]
workgroup_map = { "etl-finance" = "finance" }

[dimensions.tenant]
sources = ["query_comment.tenant_id"]
sensitive = true
"""


def _config(tmp_path: Path, text: str) -> CostDimensions:
    path = tmp_path / "cost-dimensions.toml"
    path.write_text(text, encoding="utf-8")
    return CostDimensions.from_toml(path)


def test_a_configuration_names_each_dimensions_sources_in_order(tmp_path: Path) -> None:
    config = _config(tmp_path, TEAM_AND_TENANT)

    team, tenant = config.dimensions
    assert team.name == "team"
    assert team.sources == (
        Source(SourceKind.QUERY_COMMENT, "team"),
        Source(SourceKind.OPENLINEAGE_TAG, "team"),
        Source(SourceKind.WORKGROUP_MAP),
    )
    assert dict(team.workgroup_map) == {"etl-finance": "finance"}
    assert (team.sensitive, tenant.sensitive) == (False, True)
    assert config.comment_keys == frozenset({"team", "tenant_id"})


def test_eight_dimensions_are_accepted_and_nine_are_refused(tmp_path: Path) -> None:
    def dimensions(count: int) -> str:
        return "".join(f'[dimensions.d{i}]\nsources = ["workgroup_map"]\n' for i in range(count))

    maps = "workgroup_map = {}\n"
    eight = dimensions(8).replace('["workgroup_map"]\n', '["workgroup_map"]\n' + maps)
    nine = dimensions(9).replace('["workgroup_map"]\n', '["workgroup_map"]\n' + maps)

    assert len(_config(tmp_path, eight).dimensions) == 8
    with pytest.raises(ValueError, match="at most 8 dimensions"):
        _config(tmp_path, nine)


@pytest.mark.parametrize(
    ("text", "fault"),
    [
        ('[dimensions.team]\nsources = ["query_tag.team"]\n', "unknown source query_tag.team"),
        ('[dimensions.team]\nsources = ["query_comment"]\n', "unknown source query_comment"),
        ('[dimensions.team]\nsources = ["query_comment.team"]\ncolour = 1\n', "unknown key colour"),
        ('[teams]\nsources = ["query_comment.team"]\n', "unknown key teams"),
        ('[dimensions.team]\nsources = ["workgroup_map"]\n', "workgroup_map is not given"),
        ("[dimensions.team]\nsources = []\n", "sources must be a non-empty list"),
        ('[dimensions.team]\nsources = ["query_comment.team"]\nsensitive = "yes"\n', "sensitive"),
        ("not = valid = toml\n", "not valid TOML"),
    ],
)
def test_an_unknown_source_or_key_is_refused(tmp_path: Path, text: str, fault: str) -> None:
    with pytest.raises(ValueError, match=fault):
        _config(tmp_path, text)


def test_at_most_16_comment_keys_are_configured(tmp_path: Path) -> None:
    def keys(count: int) -> str:
        names = ", ".join(f'"query_comment.k{i}"' for i in range(count))
        return f"[dimensions.team]\nsources = [{names}]\n"

    assert len(_config(tmp_path, keys(16)).comment_keys) == 16
    with pytest.raises(ValueError, match="at most 16 comment keys"):
        _config(tmp_path, keys(17))


def test_no_file_means_no_dimensions() -> None:
    assert CostDimensions.none().dimensions == ()
    assert CostDimensions.none().comment_keys == frozenset()
