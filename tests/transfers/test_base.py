"""Tests for the guarded transfer flow, using a fake bank."""

from contextlib import contextmanager
from decimal import Decimal
from typing import List, Optional

import pytest

from fintself.core.exceptions import (
    TransferAborted,
    TransferError,
    TransferStatusUnknown,
    TransferVerificationError,
)
from fintself.core.models import TransferRequest, TransferSummary
from fintself.transfers.base import SecondFactorChallenge, TransferCapable
from fintself.transfers.journal import TransferJournal


class FakeBank(TransferCapable):
    def __init__(self, summary: TransferSummary, fail_receipt: bool = False):
        super().__init__(headless=True, debug_mode=False)
        self.summary = summary
        self.fail_receipt = fail_receipt
        self.calls: List[str] = []

    def _get_bank_id(self) -> str:
        return "fake"

    @contextmanager
    def _browser_session(self):
        yield None

    def _login(self) -> None:
        self.calls.append("login")

    def _scrape_movements(self):
        return []

    def _prepare_transfer(self, request) -> None:
        self.calls.append("prepare")

    def _read_confirmation_summary(self) -> TransferSummary:
        return self.summary

    def _submit_transfer(self, second_factor) -> None:
        self.calls.append("submit")
        second_factor(SecondFactorChallenge(kind="app_approval", message="ok"))

    def _read_receipt(self) -> Optional[str]:
        if self.fail_receipt:
            raise RuntimeError("no receipt")
        return "998877"


@pytest.fixture
def request_() -> TransferRequest:
    return TransferRequest(
        destination_rut="12.345.678-5",
        destination_account="00-123-45678-9",
        amount=Decimal("15000"),
    )


@pytest.fixture
def good_summary() -> TransferSummary:
    return TransferSummary(
        amount=Decimal("15000"),
        destination_rut="12345678-5",
        destination_account="56789",
    )


@pytest.fixture
def journal(tmp_path) -> TransferJournal:
    return TransferJournal(str(tmp_path / "transfers.jsonl"))


def run(bank, request, journal, **kwargs):
    defaults = dict(
        confirm=lambda req, summary: True,
        second_factor=lambda challenge: None,
        dry_run=False,
        max_amount=100000,
        journal=journal,
    )
    defaults.update(kwargs)
    return bank.transfer("user", "pass", request, **defaults)


# ─── Request model ────────────────────────────────────────────────────────────


class TestTransferRequest:
    def test_normalizes_fields(self, request_):
        assert request_.destination_rut == "12345678-5"
        assert request_.destination_account == "00123456789"

    @pytest.mark.parametrize("amount", [0, -1, Decimal("10.5")])
    def test_rejects_bad_amounts(self, amount):
        with pytest.raises(ValueError):
            TransferRequest(
                destination_rut="12.345.678-5",
                destination_account="1234",
                amount=amount,
            )

    def test_rejects_bad_rut(self):
        with pytest.raises(ValueError):
            TransferRequest(
                destination_rut="12.345.678-0", destination_account="1234", amount=1
            )

    def test_masked_hides_account(self, request_):
        assert request_.masked()["destination_account"] == "****6789"


# ─── Summary verification ─────────────────────────────────────────────────────


class TestVerifySummary:
    def test_matching_summary_passes(self, request_, good_summary):
        TransferCapable.verify_summary(request_, good_summary)

    def test_amount_mismatch(self, request_, good_summary):
        good_summary.amount = Decimal("150000")
        with pytest.raises(TransferVerificationError, match="amount"):
            TransferCapable.verify_summary(request_, good_summary)

    def test_missing_amount(self, request_, good_summary):
        good_summary.amount = None
        with pytest.raises(TransferVerificationError, match="no amount"):
            TransferCapable.verify_summary(request_, good_summary)

    def test_rut_mismatch(self, request_, good_summary):
        good_summary.destination_rut = "11.111.111-1"
        with pytest.raises(TransferVerificationError, match="RUT"):
            TransferCapable.verify_summary(request_, good_summary)

    def test_account_mismatch(self, request_, good_summary):
        good_summary.destination_account = "1111"
        with pytest.raises(TransferVerificationError, match="account"):
            TransferCapable.verify_summary(request_, good_summary)

    def test_account_with_too_few_digits(self, request_, good_summary):
        good_summary.destination_account = "89"
        with pytest.raises(TransferVerificationError, match="account"):
            TransferCapable.verify_summary(request_, good_summary)

    def test_needs_rut_or_account(self, request_):
        summary = TransferSummary(amount=Decimal("15000"))
        with pytest.raises(TransferVerificationError, match="neither"):
            TransferCapable.verify_summary(request_, summary)

    def test_account_only_is_enough(self, request_):
        summary = TransferSummary(amount=Decimal("15000"), destination_account="6789")
        TransferCapable.verify_summary(request_, summary)


# ─── Orchestration ────────────────────────────────────────────────────────────


class TestTransferFlow:
    def test_dry_run_never_submits(self, request_, good_summary, journal):
        bank = FakeBank(good_summary)
        result = run(bank, request_, journal, dry_run=True)
        assert result.status == "dry_run"
        assert "submit" not in bank.calls
        assert not journal._read()

    def test_completed(self, request_, good_summary, journal):
        bank = FakeBank(good_summary)
        result = run(bank, request_, journal)
        assert result.status == "completed"
        assert result.receipt_id == "998877"
        assert [e["status"] for e in journal._read()] == ["submitting", "completed"]

    def test_over_limit_never_opens_browser(self, request_, good_summary, journal):
        bank = FakeBank(good_summary)
        with pytest.raises(TransferError, match="exceeds the limit"):
            run(bank, request_, journal, max_amount=1000)
        assert bank.calls == []

    def test_mismatch_never_submits(self, request_, good_summary, journal):
        good_summary.amount = Decimal("1")
        bank = FakeBank(good_summary)
        with pytest.raises(TransferVerificationError):
            run(bank, request_, journal)
        assert "submit" not in bank.calls

    def test_user_declines(self, request_, good_summary, journal):
        bank = FakeBank(good_summary)
        with pytest.raises(TransferAborted):
            run(bank, request_, journal, confirm=lambda req, summary: False)
        assert "submit" not in bank.calls
        assert not journal._read()

    def test_truthy_but_not_true_confirmation_aborts(
        self, request_, good_summary, journal
    ):
        bank = FakeBank(good_summary)
        with pytest.raises(TransferAborted):
            run(bank, request_, journal, confirm=lambda req, summary: "yes")

    def test_unknown_status_after_submit(self, request_, good_summary, journal):
        bank = FakeBank(good_summary, fail_receipt=True)
        with pytest.raises(TransferStatusUnknown, match="check your statement"):
            run(bank, request_, journal)
        assert [e["status"] for e in journal._read()] == ["submitting", "unknown"]

    def test_duplicate_is_blocked(self, request_, good_summary, journal):
        run(FakeBank(good_summary), request_, journal)
        bank = FakeBank(good_summary)
        with pytest.raises(TransferError, match="identical transfer"):
            run(bank, request_, journal)
        assert bank.calls == []

    def test_duplicate_after_unknown_is_blocked(self, request_, good_summary, journal):
        with pytest.raises(TransferStatusUnknown):
            run(FakeBank(good_summary, fail_receipt=True), request_, journal)
        with pytest.raises(TransferError, match="status: unknown"):
            run(FakeBank(good_summary), request_, journal)

    def test_allow_duplicate(self, request_, good_summary, journal):
        run(FakeBank(good_summary), request_, journal)
        result = run(FakeBank(good_summary), request_, journal, allow_duplicate=True)
        assert result.status == "completed"

    def test_dry_run_ignores_journal(self, request_, good_summary, journal):
        run(FakeBank(good_summary), request_, journal)
        result = run(FakeBank(good_summary), request_, journal, dry_run=True)
        assert result.status == "dry_run"

    def test_different_amount_is_not_duplicate(self, request_, good_summary, journal):
        run(FakeBank(good_summary), request_, journal)
        other = request_.model_copy(update={"amount": Decimal("20000")})
        good_summary.amount = Decimal("20000")
        assert run(FakeBank(good_summary), other, journal).status == "completed"
