import random

from datagen import taxids as gen
from invoice_pipeline import taxid


def test_published_examples():
    assert taxid.check("DE136695976") == (True, "checksum")
    assert taxid.check("GB 980 7806 84") == (True, "checksum")
    assert taxid.check("DE136695977")[0] is False


def test_generator_and_validator_agree():
    rng = random.Random(3)
    for country in ("DE", "FR", "GB", "US"):
        for _ in range(500):
            good = gen.MAKERS[country](rng)
            assert taxid.check(good)[0], good
            assert not taxid.check(gen.corrupt(good, rng))[0]
