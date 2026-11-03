import pytest

from yieldsuite.levels import Level, ProductClass, classify_product


def test_level_order_and_parse():
    assert Level.L1A < Level.L1B < Level.L2 < Level.L5
    assert Level.parse("l3") is Level.L3
    assert Level.range(Level.L2, Level.L4) == [Level.L2, Level.L3, Level.L4]
    with pytest.raises(ValueError):
        Level.parse("L9")


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(has_harvester=True, has_scale_weights=True, has_imagery=True), ProductClass.ABSOLUTE),
        (dict(has_harvester=True), ProductClass.ABSOLUTE_UNRECONCILED),
        (dict(has_harvester=False, has_imagery=True), ProductClass.RELATIVE),
    ],
)
def test_classify_product(kwargs, expected):
    assert classify_product(**kwargs) is expected


def test_classify_product_needs_data():
    with pytest.raises(ValueError):
        classify_product(has_harvester=False)
