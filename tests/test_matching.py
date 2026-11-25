"""Matching engine unit tests (pure Python, no database)."""

import uuid

import pytest

from app.matching import CatalogEntry, Matcher
from app.matching.parser import parse_line, split_message
from tests.eval.evaluate import build


@pytest.fixture(scope="module")
def m():
    matcher, sku_of = build()
    matcher.sku_of = sku_of
    return matcher


def one(m, text):
    results = m.match_text(text)
    assert len(results) == 1, results
    return results[0]


def sku(m, r):
    return m.sku_of[r.product_id]


def test_normalization_arabic_digits_and_letters(m):
    r = one(m, "٣ أرز مصري ٥ كيلو")
    assert (sku(m, r), r.quantity, r.match_type) == ("R5", 3, "exact_name")


def test_alias_is_exact_match_with_full_confidence(m):
    r = one(m, "2 زيت عباد")
    assert (sku(m, r), r.match_type, r.confidence) == ("OS", "exact_alias", 1.0)


def test_size_is_not_quantity():
    p = parse_line("رز 5 كيلو")
    assert p.size is not None and p.quantity is None and p.quantity_problem == "missing"
    p = parse_line("٣ رز 5 كيلو")
    assert p.quantity == 3 and p.size.amount == 5000


def test_units_are_converted_not_text_matched(m):
    assert sku(m, one(m, "3 سكر 1000 جرام")) == "SU"      # 1000 g == 1 kg
    assert one(m, "3 سكر 2 كيلو").reason == "size_mismatch"
    assert one(m, "3 سكر نص كيلو").reason == "size_mismatch"
    assert sku(m, one(m, "2 زيت زيتون 1000 مل")) == "OO"


def test_two_sizes_of_same_product_family_are_not_confused(m):
    assert sku(m, one(m, "3 رز 1 كيلو")) == "R1"
    assert sku(m, one(m, "3 رز 5 كيلو")) == "R5"
    assert sku(m, one(m, "3 كوب بلاستيك 250 مل")) == "C25"
    assert one(m, "3 كوب بلاستيك 300 مل").reason == "size_mismatch"


@pytest.mark.parametrize("text", ["3 رز", "2 زيت", "4 كوب بلاستيك", "٣ كوبايات", "2 عصير"])
def test_ambiguity_is_never_resolved_arbitrarily(m, text):
    r = one(m, text)
    assert r.status == "exception" and r.product_id is None
    assert r.reason in ("ambiguous", "low_confidence")
    if r.reason == "ambiguous":
        assert len(r.candidates) >= 2


def test_missing_quantity_is_not_guessed(m):
    r = one(m, "سكر")
    assert r.status == "exception" and r.reason == "missing_quantity"
    assert r.product_id is not None  # product known, quantity is the open question


def test_unclear_quantity_two_numbers_without_catalog_help(m):
    assert one(m, "سكر 3 4").reason == "unclear_quantity"


def test_two_numbers_resolved_by_catalog_alias(m):
    r = one(m, "٣ كوبايات 200")
    assert (sku(m, r), r.quantity) == ("C2", 3)


def test_number_in_alias_is_not_fuzzy_matched(m):
    # 22 vs 2: numbers are compared as numbers
    r = one(m, "٢ طبق بلاستيك 22 سم")
    assert sku(m, r) == "PL" and r.quantity == 2


def test_unknown_product_and_lookalikes_become_exceptions(m):
    for text in ("3 بسكويت", "3 سكر ناعم", "2 لبن خالي الدسم", "3 ملح خشن"):
        r = one(m, text)
        assert r.status == "exception" and r.product_id is None, text


def test_number_words_and_fillers(m):
    r = one(m, "لو سمحت تلاتة علب شاي")
    assert (sku(m, r), r.quantity) == ("TE", 3)


def test_multi_line_splitting_and_glued_conjunction(m):
    assert split_message("٣ سكر\n٢ ملح، 5 شاي + 1 لبن") == ["3 سكر", "2 ملح", "5 شاي", "1 لبن"]
    rs = m.match_text("3 سكر وتلاتة شاي")
    assert [(m.sku_of[r.product_id], r.quantity) for r in rs] == [("SU", 3), ("TE", 3)]


def test_same_product_twice_is_flagged_not_merged(m):
    rs = m.match_text("٣ سكر\n٣ سكر")
    assert [r.status for r in rs] == ["matched", "exception"]
    assert rs[1].reason == "duplicate_product"


def test_fuzzy_needs_clear_margin():
    a, b = uuid.uuid4(), uuid.uuid4()
    matcher = Matcher([
        CatalogEntry(a, "A", "شوكولاتة دارك كبيرة", []),
        CatalogEntry(b, "B", "شوكولاتة دارك كبيرتين", []),
    ])
    r = matcher.match_text("2 شوكولاتة دارك كبيرت")[0]
    # both products score ~95: close scores must never be broken arbitrarily
    assert r.status == "exception" and r.reason == "ambiguous" and r.product_id is None


def test_decimal_and_zero_quantity_rejected(m):
    assert one(m, "2.5 سكر").reason == "unclear_quantity"
    assert one(m, "0 سكر").reason == "unclear_quantity"


def test_empty_and_garbage_text(m):
    assert m.match_text("   \n ,, ") == []
    assert one(m, "؟؟؟ 3").status == "exception"
