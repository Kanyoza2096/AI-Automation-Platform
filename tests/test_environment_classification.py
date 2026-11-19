import pytest

from config.environments import Environment


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("development", Environment.DEVELOPMENT),
        ("dev", Environment.DEVELOPMENT),
        ("local", Environment.DEVELOPMENT),
        ("testing", Environment.TESTING),
        ("test", Environment.TESTING),
        ("staging", Environment.STAGING),
        ("stage", Environment.STAGING),
        ("production", Environment.PRODUCTION),
        ("prod", Environment.PRODUCTION),
    ],
)
def test_environment_aliases_are_explicit(value, expected):
    assert Environment.from_string(value) is expected


def test_staging_is_not_production():
    environment = Environment.from_string("staging")

    assert environment.is_staging
    assert not environment.is_production
    assert not environment.is_testing


@pytest.mark.parametrize("value", ["", "stagng", "preview", "unknown"])
def test_unknown_environment_fails_closed(value):
    with pytest.raises(ValueError, match="Unsupported environment"):
        Environment.from_string(value)
