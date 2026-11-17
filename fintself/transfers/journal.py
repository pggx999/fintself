import json
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fintself.core.models import TransferRequest
from fintself.utils.logging import logger

# Statuses that mean money may have left the account.
_BLOCKING_STATUSES = {"submitting", "completed", "unknown"}


class TransferJournal:
    """Append-only JSONL log of transfer attempts (account numbers masked).

    Each attempt writes a 'submitting' entry right before the final click and a
    second entry with the outcome. A 'submitting' entry without an outcome means
    the run died mid-transfer and is treated as possibly sent.
    """

    def __init__(self, path: str):
        self.path = path

    def _read(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Skipping corrupt line in {self.path}.")
        return entries

    def _append(self, entry: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _key(bank_id: str, request: TransferRequest) -> str:
        return "|".join(
            [
                bank_id,
                request.destination_rut,
                request.destination_account[-4:],
                str(request.amount),
            ]
        )

    def find_recent_duplicate(
        self, bank_id: str, request: TransferRequest, window: timedelta
    ) -> Optional[dict]:
        """Returns the latest state of a matching attempt inside `window`, if any."""
        key = self._key(bank_id, request)
        cutoff = datetime.now() - window
        latest_by_id: dict = {}
        for entry in self._read():
            if entry.get("key") != key:
                continue
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            latest_by_id[entry.get("attempt_id")] = entry
        for entry in reversed(list(latest_by_id.values())):
            if entry.get("status") in _BLOCKING_STATUSES:
                return entry
        return None

    def record(
        self,
        bank_id: str,
        request: TransferRequest,
        status: str,
        attempt_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> str:
        attempt_id = attempt_id or uuid.uuid4().hex
        self._append(
            {
                "attempt_id": attempt_id,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "bank_id": bank_id,
                "key": self._key(bank_id, request),
                "status": status,
                "receipt_id": receipt_id,
                **request.masked(),
            }
        )
        return attempt_id
