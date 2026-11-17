from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal, Optional

from fintself import settings
from fintself.core.exceptions import (
    TransferAborted,
    TransferError,
    TransferStatusUnknown,
    TransferVerificationError,
)
from fintself.core.models import TransferRequest, TransferResult, TransferSummary
from fintself.core.rut import normalize_rut
from fintself.scrapers.base import BaseScraper
from fintself.transfers.journal import TransferJournal
from fintself.utils.logging import logger


@dataclass
class SecondFactorChallenge:
    """What the bank asks for to authorize the transfer.

    kind:
        "app_approval": approve the operation in the bank's mobile app.
        "code": type a code (token, SMS, coordinates) into the website.
    """

    kind: Literal["app_approval", "code"]
    message: str


# Shows the summary to the user; returns True only if they confirm.
ConfirmCallback = Callable[[TransferRequest, TransferSummary], bool]
# Returns the code for "code" challenges; for "app_approval" it returns once the
# user says they approved (the return value is ignored).
SecondFactorCallback = Callable[[SecondFactorChallenge], Optional[str]]


class TransferCapable(BaseScraper):
    """Adds a guarded transfer flow on top of a bank scraper.

    The flow is the same for every bank:

    1. Validate the request against the configured limit and the local journal.
    2. Log in, open the transfer form, pick an already registered recipient and
       fill amount and message (`_prepare_transfer`).
    3. Read the bank's confirmation screen (`_read_confirmation_summary`) and
       check it matches the request. Any mismatch aborts here.
    4. Stop if `dry_run`. Otherwise ask `confirm`; anything but True aborts.
    5. Submit, complete the second factor with the user's help, read the receipt.

    Nothing is sent before step 5. Errors after the final click raise
    TransferStatusUnknown: the caller must check the statement, never retry.
    """

    @abstractmethod
    def _prepare_transfer(self, request: TransferRequest) -> None:
        """Navigates to the transfer form and fills it, stopping on the
        confirmation screen right before the final submit button."""

    @abstractmethod
    def _read_confirmation_summary(self) -> TransferSummary:
        """Reads the data the bank shows on its confirmation screen."""

    @abstractmethod
    def _submit_transfer(self, second_factor: SecondFactorCallback) -> None:
        """Clicks the final submit button and handles the second factor."""

    @abstractmethod
    def _read_receipt(self) -> Optional[str]:
        """Waits for the success screen and returns the receipt/operation id.

        Must raise if the success screen does not appear."""

    # ------------------------------------------------------------------

    @staticmethod
    def verify_summary(request: TransferRequest, summary: TransferSummary) -> None:
        """Raises TransferVerificationError unless the bank's summary matches."""
        problems = []

        if summary.amount is None:
            problems.append("the confirmation screen shows no amount")
        elif summary.amount != request.amount:
            problems.append(
                f"amount on screen is {summary.amount}, expected {request.amount}"
            )

        rut_ok = False
        if summary.destination_rut:
            shown = normalize_rut(summary.destination_rut)
            if shown != request.destination_rut:
                problems.append(
                    f"RUT on screen is {shown}, expected {request.destination_rut}"
                )
            else:
                rut_ok = True

        account_ok = False
        shown_digits = "".join(
            c for c in (summary.destination_account or "") if c.isdigit()
        )
        if shown_digits:
            # Banks often show only the last digits; require at least 4 of them.
            if len(shown_digits) < 4 or not request.destination_account.endswith(
                shown_digits
            ):
                problems.append(
                    f"account on screen ends in {shown_digits[-4:]}, "
                    f"expected ****{request.destination_account[-4:]}"
                )
            else:
                account_ok = True

        if not (rut_ok or account_ok) and not problems:
            problems.append("the confirmation screen shows neither RUT nor account")

        if problems:
            raise TransferVerificationError(
                "Confirmation screen does not match the request: "
                + "; ".join(problems)
                + ". Nothing was sent."
            )

    def check_request(
        self,
        request: TransferRequest,
        *,
        dry_run: bool = True,
        allow_duplicate: bool = False,
        max_amount: Optional[int] = None,
        journal: Optional[TransferJournal] = None,
    ) -> None:
        """Checks the amount limit and the duplicate journal. No browser needed."""
        limit = (
            settings.FINTSELF_TRANSFER_MAX_AMOUNT if max_amount is None else max_amount
        )
        if request.amount > limit:
            raise TransferError(
                f"Amount {request.amount} exceeds the limit of {limit} CLP "
                "(FINTSELF_TRANSFER_MAX_AMOUNT)."
            )

        if dry_run or allow_duplicate:
            return
        journal = journal or TransferJournal(settings.FINTSELF_TRANSFER_JOURNAL)
        window = timedelta(hours=settings.FINTSELF_TRANSFER_DUPLICATE_WINDOW_HOURS)
        duplicate = journal.find_recent_duplicate(self._get_bank_id(), request, window)
        if duplicate:
            raise TransferError(
                "An identical transfer was attempted at "
                f"{duplicate['timestamp']} (status: {duplicate['status']}). "
                "Check your statement; use --allow-duplicate only if you are "
                "sure it was not sent."
            )

    def transfer(
        self,
        user: str,
        password: str,
        request: TransferRequest,
        *,
        confirm: ConfirmCallback,
        second_factor: SecondFactorCallback,
        dry_run: bool = True,
        allow_duplicate: bool = False,
        max_amount: Optional[int] = None,
        journal: Optional[TransferJournal] = None,
    ) -> TransferResult:
        bank_id = self._get_bank_id()
        journal = journal or TransferJournal(settings.FINTSELF_TRANSFER_JOURNAL)
        self.check_request(
            request,
            dry_run=dry_run,
            allow_duplicate=allow_duplicate,
            max_amount=max_amount,
            journal=journal,
        )

        self.user = user
        self.password = password
        logger.info(f"Preparing transfer on {bank_id}: {request.masked()}")

        with self._browser_session():
            self._login()
            self._prepare_transfer(request)
            summary = self._read_confirmation_summary()
            self._save_debug_info("transfer_confirmation_screen")
            self.verify_summary(request, summary)
            logger.info("Confirmation screen matches the request.")

            if dry_run:
                logger.info("Dry run: stopping before submitting. Nothing was sent.")
                return TransferResult(
                    status="dry_run", request=request, summary=summary
                )

            if confirm(request, summary) is not True:
                raise TransferAborted()

            attempt_id = journal.record(bank_id, request, "submitting")
            try:
                self._submit_transfer(second_factor)
                receipt_id = self._read_receipt()
            except Exception as e:
                journal.record(bank_id, request, "unknown", attempt_id=attempt_id)
                self._save_debug_info("transfer_status_unknown")
                raise TransferStatusUnknown(
                    f"The transfer was submitted but its result could not be "
                    f"confirmed ({e}). It may have been sent: check your statement "
                    "before trying again."
                ) from e

            journal.record(
                bank_id,
                request,
                "completed",
                attempt_id=attempt_id,
                receipt_id=receipt_id,
            )
            self._save_debug_info("transfer_receipt")
            logger.info(f"Transfer completed. Receipt: {receipt_id or 'n/a'}")
            return TransferResult(
                status="completed",
                request=request,
                summary=summary,
                receipt_id=receipt_id,
                executed_at=datetime.now(),
            )
