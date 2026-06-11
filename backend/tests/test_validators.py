"""HU identifier validators — checksum, format, mutation tests."""

from app.services.wfm.validators import (
    is_valid_bank_account,
    is_valid_tax_id,
    is_valid_taj,
    normalize_identifier,
)
from tests.conftest import gen_bank_account, gen_tax_id, gen_taj


class TestTaxId:
    def test_generated_valid(self):
        for seed in range(20):
            assert is_valid_tax_id(gen_tax_id(seed)), gen_tax_id(seed)

    def test_mutated_digit_fails(self):
        valid = gen_tax_id()
        # bármely számjegy megváltoztatása rontsa el az ellenőrzőösszeget
        flipped = valid[:9] + str((int(valid[9]) + 1) % 10)
        assert not is_valid_tax_id(flipped)

    def test_must_start_with_8(self):
        valid = gen_tax_id()
        assert not is_valid_tax_id("7" + valid[1:])

    def test_length_and_charset(self):
        assert not is_valid_tax_id("12345")
        assert not is_valid_tax_id("abcdefghij")
        assert not is_valid_tax_id("")

    def test_accepts_formatting(self):
        valid = gen_tax_id()
        spaced = f"{valid[:4]} {valid[4:7]} {valid[7:]}"
        assert is_valid_tax_id(spaced)


class TestTaj:
    def test_generated_valid(self):
        for seed in range(20):
            assert is_valid_taj(gen_taj(seed)), gen_taj(seed)

    def test_mutated_check_digit_fails(self):
        valid = gen_taj()
        flipped = valid[:8] + str((int(valid[8]) + 1) % 10)
        assert not is_valid_taj(flipped)

    def test_length_and_charset(self):
        assert not is_valid_taj("1234")
        assert not is_valid_taj("12345678X")

    def test_accepts_hyphens(self):
        valid = gen_taj()
        assert is_valid_taj(f"{valid[:3]}-{valid[3:6]}-{valid[6:]}")


class TestBankAccount:
    def test_generated_valid_16(self):
        for seed in range(10):
            assert is_valid_bank_account(gen_bank_account(seed)), gen_bank_account(seed)

    def test_mutated_fails(self):
        valid = gen_bank_account()
        flipped = valid[:15] + str((int(valid[15]) + 1) % 10)
        assert not is_valid_bank_account(flipped)

    def test_bad_lengths(self):
        assert not is_valid_bank_account("12345678")
        assert not is_valid_bank_account("1" * 20)
        assert not is_valid_bank_account("")

    def test_accepts_hyphenated(self):
        valid = gen_bank_account()
        assert is_valid_bank_account(f"{valid[:8]}-{valid[8:]}")


def test_normalize_identifier():
    assert normalize_identifier("117 7301-6") == "11773016"
    assert normalize_identifier("") == ""
