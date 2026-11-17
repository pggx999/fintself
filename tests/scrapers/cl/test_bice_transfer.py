"""Tests for the parsing helpers of the Banco Bice transfer flow."""

from decimal import Decimal

import pytest

from fintself.core.models import TransferRequest
from fintself.scrapers.cl.bice_transfer import BiceTransferScraper


@pytest.fixture
def request_() -> TransferRequest:
    return TransferRequest(
        destination_rut="12.345.678-5",
        destination_account="000123456789",
        amount=15000,
    )


class TestRecipientTextMatches:
    def test_full_account(self, request_):
        text = "Juan Pérez\n12.345.678-5\nBanco Estado · Cuenta RUT 000123456789"
        assert BiceTransferScraper.recipient_text_matches(text, request_)

    def test_masked_account(self, request_):
        text = "Juan Pérez 12.345.678-5 Cuenta Corriente ****6789"
        assert BiceTransferScraper.recipient_text_matches(text, request_)

    def test_wrong_account(self, request_):
        text = "Juan Pérez 12.345.678-5 Cuenta Corriente ****1111"
        assert not BiceTransferScraper.recipient_text_matches(text, request_)

    def test_wrong_rut(self, request_):
        text = "Ana Soto 11.111.111-1 Cuenta Corriente ****6789"
        assert not BiceTransferScraper.recipient_text_matches(text, request_)

    def test_rut_digits_are_not_taken_as_account(self):
        # Account ends in 6785, same as the last digits of the RUT body + DV.
        req = TransferRequest(
            destination_rut="12.345.678-5", destination_account="9996785", amount=1
        )
        text = "Juan Pérez 12.345.678-5 Cuenta ****1111"
        assert not BiceTransferScraper.recipient_text_matches(text, req)


class TestSummaryFromPairs:
    def test_maps_labels(self):
        summary = BiceTransferScraper.summary_from_pairs(
            {
                "Monto a transferir": "$ 15.000",
                "Nombre destinatario": "Juan Pérez",
                "RUT": "12.345.678-5",
                "Banco": "Banco Estado",
                "Cuenta destino": "Cuenta RUT ****6789",
                "Cuenta origen": "Cuenta Corriente 01-23456-7",
            }
        )
        assert summary.amount == Decimal("15000")
        assert summary.destination_rut == "12345678-5"
        assert summary.destination_account == "6789"
        assert summary.destination_name == "Juan Pérez"
        assert summary.destination_bank == "Banco Estado"

    def test_missing_amount_is_none(self):
        summary = BiceTransferScraper.summary_from_pairs({"RUT": "12.345.678-5"})
        assert summary.amount is None
