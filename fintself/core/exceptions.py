class FintselfException(Exception):
    """Base exception for all Fintself specific errors."""

    pass


class LoginError(FintselfException):
    """Exception raised when a bank login fails."""

    def __init__(
        self,
        message="Login failed. Incorrect credentials or website issue.",
    ):
        self.message = message
        super().__init__(self.message)


class DataExtractionError(FintselfException):
    """Exception raised when data extraction from the bank fails."""

    def __init__(
        self,
        message="Data extraction failed. The website structure may have changed.",
    ):
        self.message = message
        super().__init__(self.message)


class ScraperNotFound(FintselfException):
    """Exception raised when the requested scraper is not found."""

    def __init__(self, bank_id: str):
        self.message = (
            f"Scraper '{bank_id}' not found. Use 'fintself list' to see available ones."
        )
        super().__init__(self.message)


class OutputError(FintselfException):
    """Exception raised when there is a problem generating the output file."""

    def __init__(self, message="Error generating output file."):
        self.message = message
        super().__init__(self.message)


class TransferError(FintselfException):
    """Exception raised when a transfer cannot be prepared or completed."""

    def __init__(self, message="Transfer failed."):
        self.message = message
        super().__init__(self.message)


class TransferNotSupported(TransferError):
    """Exception raised when a bank has no transfer implementation."""

    def __init__(self, bank_id: str):
        super().__init__(f"Transfers are not supported for '{bank_id}'.")


class TransferVerificationError(TransferError):
    """Raised when the bank's confirmation screen does not match the request.

    It is always raised before the transfer is submitted.
    """


class TransferAborted(TransferError):
    """Raised when the user declines the transfer at the confirmation step."""

    def __init__(self, message="Transfer cancelled by the user. Nothing was sent."):
        super().__init__(message)


class TransferStatusUnknown(TransferError):
    """Raised when the transfer was submitted but its outcome could not be read.

    The money may or may not have been sent. Never retry automatically: check
    the account statement first.
    """
