import pytest

from statespace._assignment import Eligibility, GroupConfig, bucket, choose

# Every SDK asserts these values, so a subject lands in the same group in
# Python, TypeScript, and Go.
VECTORS = [
    ("salt", "u_1", 0.18452190011276326),
    ("salt", "u_2", 0.6489778519534802),
    ("3f9a", "user-42", 0.19165740483602034),
]


@pytest.mark.parametrize(("salt", "subject", "expected"), VECTORS)
def test_buckets_match_every_sdk(salt: str, subject: str, expected: float) -> None:
    assert bucket(salt, subject) == expected


def test_chooses_the_group_covering_the_bucket() -> None:
    groups = [
        GroupConfig("control", (), {}),
        GroupConfig("bm25", ((0.0, 0.2),), {}),
        GroupConfig("deep", ((0.2, 0.3), (0.6, 0.7)), {}),
    ]
    assert choose(groups, 0.1).name == "bm25"
    assert choose(groups, 0.65).name == "deep"
    assert choose(groups, 0.5).name == "control"


def test_eligibility() -> None:
    rule = Eligibility('context.country == "US"')
    assert rule.evaluate({"country": "US"})
    assert not rule.evaluate({"country": "CA"})
    assert Eligibility(None).evaluate({})
    with pytest.raises(Exception):  # noqa: B017, PT011 - any evaluation error
        rule.evaluate({})
