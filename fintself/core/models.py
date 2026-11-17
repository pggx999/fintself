import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from fintself.core.rut import is_valid_rut, normalize_rut

AccountType = Literal["corriente", "credito", "debito", "prepago"]


class MovementModel(BaseModel):
    """
    Pydantic model to represent a bank movement.
    """

    date: datetime = Field(..., description="Date of the movement.")
    description: str = Field(..., description="Description of the movement.")
    amount: Decimal = Field(
        ...,
        description="Amount of the movement (positive for income, negative for expenses).",
    )
    currency: str = Field(..., description="Currency of the movement (e.g., CLP, USD).")
    transaction_type: Optional[str] = Field(
        None, description="Type of transaction (e.g., 'Debit', 'Credit', 'Transfer')."
    )
    account_id: Optional[str] = Field(
        None,
        description="Identifier of the source/destination account (last 4 digits).",
    )
    account_type: Optional[AccountType] = Field(
        None,
        description="Type of account. Must be one of: corriente, credito, debito, prepago.",
    )
    raw_data: Optional[dict] = Field(
        {}, description="Additional raw data from the scraper."
    )

    @field_validator("account_id", mode="before")
    @classmethod
    def _format_account_id(cls, v: Optional[str]) -> Optional[str]:
        """Ensures the account_id is only the last 4 digits."""
        if v is None:
            return None
        # Remove all non-digit characters
        digits = re.sub(r"\D", "", str(v))
        if len(digits) >= 4:
            return digits[-4:]
        # Return whatever is left if less than 4 digits, or the original if no digits found
        return digits if digits else v

    class Config:
        json_schema_extra = {
            "example": {
                "date": "2023-10-26T10:00:00",
                "description": "Compra en Supermercado",
                "amount": "-15000.00",
                "currency": "CLP",
                "transaction_type": "Cargo",
                "account_id": "5678",
                "account_type": "credito",
                "raw_data": {
                    "original_desc": "COMPRA SUPERMERCADO LIDER",
                    "full_account_id": "1234-5678",
                },
            }
        }


def _mask_account(account: Optional[str]) -> Optional[str]:
    if not account:
        return account
    return f"****{account[-4:]}"


class TransferRequest(BaseModel):
    """A transfer in CLP to a recipient already registered in the bank's website."""

    destination_rut: str = Field(..., description="Recipient RUT, e.g. 12.345.678-5.")
    destination_account: str = Field(
        ..., description="Recipient account number (digits only after validation)."
    )
    amount: Decimal = Field(..., description="Amount in CLP. Whole pesos, > 0.")
    message: Optional[str] = Field(
        None, max_length=100, description="Optional comment sent with the transfer."
    )

    @field_validator("destination_rut")
    @classmethod
    def _validate_rut(cls, v: str) -> str:
        if not is_valid_rut(v):
            raise ValueError(f"Invalid RUT: '{v}'")
        return normalize_rut(v)  # type: ignore[return-value]

    @field_validator("destination_account")
    @classmethod
    def _validate_account(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) < 4:
            raise ValueError("Account number must have at least 4 digits.")
        return digits

    @field_validator("amount")
    @classmethod
    def _validate_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than zero.")
        if v != v.to_integral_value():
            raise ValueError("CLP amounts must be whole pesos.")
        return v

    def masked(self) -> dict:
        """Representation safe to log."""
        return {
            "destination_rut": self.destination_rut,
            "destination_account": _mask_account(self.destination_account),
            "amount": str(self.amount),
        }


class TransferSummary(BaseModel):
    """What the bank shows on its confirmation screen, read before submitting."""

    amount: Optional[Decimal] = None
    destination_rut: Optional[str] = None
    destination_account: Optional[str] = Field(
        None, description="Digits as shown by the bank; may be only the last ones."
    )
    destination_name: Optional[str] = None
    destination_bank: Optional[str] = None
    origin_account: Optional[str] = None
    raw_data: dict = Field(default_factory=dict)


TransferStatus = Literal["dry_run", "completed"]


class TransferResult(BaseModel):
    """Outcome of a transfer run."""

    status: TransferStatus
    request: TransferRequest
    summary: TransferSummary
    receipt_id: Optional[str] = Field(
        None, description="Bank's operation or receipt number, when shown."
    )
    executed_at: Optional[datetime] = None
    raw_data: dict = Field(default_factory=dict)
