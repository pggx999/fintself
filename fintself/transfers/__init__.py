from typing import Dict, Optional, Type

from fintself.core.exceptions import TransferNotSupported
from fintself.transfers.base import (
    SecondFactorChallenge,
    TransferCapable,
)


def _registry() -> Dict[str, Type[TransferCapable]]:
    # Imported here to avoid a circular import: bank modules import
    # fintself.transfers.base.
    from fintself.scrapers.cl.bice_transfer import BiceTransferScraper

    return {"cl_bice": BiceTransferScraper}


def list_transfer_banks() -> tuple:
    return tuple(_registry())


def get_transferer(
    bank_id: str, headless: Optional[bool] = None, debug_mode: Optional[bool] = None
) -> TransferCapable:
    cls = _registry().get(bank_id)
    if not cls:
        raise TransferNotSupported(bank_id)
    return cls(headless=headless, debug_mode=debug_mode)


__all__ = [
    "SecondFactorChallenge",
    "TransferCapable",
    "get_transferer",
    "list_transfer_banks",
]
