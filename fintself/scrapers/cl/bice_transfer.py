import re
from decimal import Decimal
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from fintself.core.exceptions import DataExtractionError, TransferError
from fintself.core.models import TransferRequest, TransferSummary
from fintself.core.rut import normalize_rut
from fintself.scrapers.cl.bice import BiceScraper
from fintself.transfers.base import (
    SecondFactorCallback,
    SecondFactorChallenge,
    TransferCapable,
)
from fintself.utils.logging import logger


class BiceTransferScraper(TransferCapable, BiceScraper):
    """Transfers from a Banco Bice checking account to a registered recipient.

    Only recipients already registered in Bice's website are supported:
    registering a new one needs its own second factor and is left to the user.

    The selectors in SELECTORS were NOT verified against the live site yet.
    Run `tutorials/capture_transfer_flow.py` once, then adjust them from the
    saved HTML. While they are wrong the flow fails before the final click,
    because the confirmation screen cannot be read and verified.
    """

    # TODO(verificar): every selector below needs checking against a capture.
    # Buttons use :visible because multi-step forms keep earlier steps hidden
    # in the DOM, and _click picks the first match.
    SELECTORS = {
        # Dashboard → transfer form.
        "transfer_menu": "a:has-text('Transferir'), button:has-text('Transferir')",
        "transfer_form_ready": "text=Destinatario",
        # Recipient picker: list of registered recipients.
        "recipient_search": "input[placeholder*='Buscar']",
        "recipient_rows": "[data-testid='recipient-item'], .destinatario, li:has-text('-')",
        "recipient_continue": "button:has-text('Continuar'):visible",
        # Amount step.
        "amount_input": "input[name='monto'], input[formcontrolname='amount']",
        "message_input": "input[name='mensaje'], textarea[name='comentario']",
        "amount_continue": "button:has-text('Continuar'):visible",
        # Confirmation screen. Each row is a label + value pair.
        "confirmation_ready": "text=Confirma",
        "confirmation_rows": ".resumen li, .summary-row, dl > div",
        # Searched only inside the confirmation block (see _confirmation_block):
        # the site menu also has a visible "Transferir" button.
        "submit": "button:has-text('Transferir'):visible, button:has-text('Confirmar'):visible",
        # Second factor.
        "second_factor_code_input": "input[name='otp'], input[autocomplete='one-time-code']",
        "second_factor_code_submit": "button:has-text('Validar'):visible, button:has-text('Confirmar'):visible",
        "second_factor_app_prompt": "text=BICE Pass",
        # Receipt.
        "receipt_ready": ":text('Transferencia exitosa'), :text('comprobante')",
        "receipt_id": "text=/N[°º] de operaci[oó]n/",
    }

    # Labels (lowercase, partial) used to map confirmation rows to fields.
    SUMMARY_LABELS = {
        "amount": ("monto",),
        "destination_rut": ("rut",),
        "destination_account": ("cuenta destino", "n° de cuenta", "número de cuenta"),
        "destination_name": ("nombre", "destinatario"),
        "destination_bank": ("banco",),
        "origin_account": ("cuenta origen", "desde"),
    }

    SECOND_FACTOR_TIMEOUT_MS = 180_000

    # ------------------------------------------------------------------

    def _prepare_transfer(self, request: TransferRequest) -> None:
        page = self._ensure_page()
        sel = self.SELECTORS
        page.wait_for_timeout(3000)
        self._close_popups()

        logger.info("Opening transfer form.")
        self._click(sel["transfer_menu"])
        self._wait_for_selector(sel["transfer_form_ready"], timeout_override=30000)
        self._save_debug_info("transfer_01_form")

        logger.info("Selecting registered recipient.")
        search = page.locator(sel["recipient_search"])
        if search.count():
            self._fill(search, request.destination_rut.split("-")[0])
            page.wait_for_timeout(1000)
        self._click(self._find_recipient(request))
        self._save_debug_info("transfer_02_recipient_selected")
        self._click(sel["recipient_continue"])

        logger.info("Filling amount.")
        self._fill(sel["amount_input"], str(int(request.amount)))
        if request.message:
            message = page.locator(sel["message_input"])
            if message.count():
                self._fill(message, request.message)
        self._save_debug_info("transfer_03_amount_filled")
        self._click(sel["amount_continue"])

        self._wait_for_selector(sel["confirmation_ready"], timeout_override=30000)

    def _find_recipient(self, request: TransferRequest):
        """Returns the row of the recipient matching both RUT and account."""
        page = self._ensure_page()
        rows = page.locator(self.SELECTORS["recipient_rows"])
        matches = []
        for i in range(rows.count()):
            row = rows.nth(i)
            text = row.inner_text()
            if self.recipient_text_matches(text, request):
                matches.append(row)
        if not matches:
            raise TransferError(
                f"Recipient {request.destination_rut} / "
                f"****{request.destination_account[-4:]} is not registered. "
                "Register it in Bice's website first."
            )
        if len(matches) > 1:
            raise TransferError(
                "More than one registered recipient matches; refusing to guess."
            )
        return matches[0]

    _RUT_PATTERN = r"\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]"

    @classmethod
    def recipient_text_matches(cls, text: str, request: TransferRequest) -> bool:
        """True if a recipient row shows the requested RUT and account (or its ending)."""
        ruts = {normalize_rut(m) for m in re.findall(cls._RUT_PATTERN, text)}
        if request.destination_rut not in ruts:
            return False
        # Drop RUTs so their digits are not mistaken for an account number.
        rest = re.sub(cls._RUT_PATTERN, " ", text)
        for group in re.findall(r"\d[\d\-\s]*\d", rest):
            digits = re.sub(r"\D", "", group)
            if len(digits) >= 4 and request.destination_account.endswith(digits):
                return True
        return False

    # ------------------------------------------------------------------

    def _confirmation_block(self):
        """Closest ancestor of the confirmation heading that contains a button."""
        page = self._ensure_page()
        return (
            page.locator(self.SELECTORS["confirmation_ready"])
            .first.locator("xpath=ancestor::*[.//button][1]")
            .first
        )

    def _read_confirmation_summary(self) -> TransferSummary:
        rows = self._confirmation_block().locator(self.SELECTORS["confirmation_rows"])
        pairs = {}
        for i in range(rows.count()):
            text = rows.nth(i).inner_text().strip()
            if "\n" in text:
                label, value = text.split("\n", 1)
            elif ":" in text:
                label, value = text.split(":", 1)
            else:
                continue
            pairs[label.strip()] = value.strip()
        if not pairs:
            raise DataExtractionError("Could not read the confirmation screen.")
        return self.summary_from_pairs(pairs)

    @classmethod
    def summary_from_pairs(cls, pairs: dict) -> TransferSummary:
        """Maps label/value pairs from the confirmation screen to a summary."""
        fields: dict = {}
        for label, value in pairs.items():
            low = label.lower()
            for field, keys in cls.SUMMARY_LABELS.items():
                if field not in fields and any(k in low for k in keys):
                    fields[field] = value
                    break

        amount = None
        if "amount" in fields:
            digits = re.sub(r"\D", "", fields["amount"])
            amount = Decimal(digits) if digits else None

        account = fields.get("destination_account")
        return TransferSummary(
            amount=amount,
            destination_rut=normalize_rut(fields.get("destination_rut")),
            destination_account=re.sub(r"\D", "", account) if account else None,
            destination_name=fields.get("destination_name"),
            destination_bank=fields.get("destination_bank"),
            origin_account=fields.get("origin_account"),
            raw_data=pairs,
        )

    # ------------------------------------------------------------------

    def _submit_transfer(self, second_factor: SecondFactorCallback) -> None:
        page = self._ensure_page()
        sel = self.SELECTORS
        logger.info("Submitting transfer.")
        self._click(self._confirmation_block().locator(sel["submit"]))
        page.wait_for_timeout(2000)
        self._save_debug_info("transfer_04_second_factor")

        code_input = page.locator(sel["second_factor_code_input"])
        if code_input.count() and code_input.first.is_visible():
            code = second_factor(
                SecondFactorChallenge(
                    kind="code", message="Ingresa la clave de tu segundo factor."
                )
            )
            if not code:
                raise TransferError("No second-factor code provided.")
            self._fill(code_input, code, delay=80)
            self._click(sel["second_factor_code_submit"])
        else:
            second_factor(
                SecondFactorChallenge(
                    kind="app_approval",
                    message="Aprueba la transferencia en tu app BICE Pass.",
                )
            )

    def _read_receipt(self) -> Optional[str]:
        page = self._ensure_page()
        try:
            page.locator(self.SELECTORS["receipt_ready"]).first.wait_for(
                state="visible", timeout=self.SECOND_FACTOR_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            raise DataExtractionError("Success screen did not appear.")

        receipt = page.locator(self.SELECTORS["receipt_id"])
        if receipt.count():
            match = re.search(r"(\d{4,})", receipt.first.inner_text())
            if match:
                return match.group(1)
        return None
