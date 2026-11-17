"""Captures the HTML of a bank's transfer screens so selectors can be written.

It logs in with the bank's scraper and hands the browser to you. Navigate the
transfer flow by hand; each time you press Enter in the terminal it saves a
screenshot and the HTML to debug_output/<bank_id>/.

STOP at the confirmation screen: do not press the final "Transferir" button.
The files contain personal data (names, RUTs, balances). Review and redact
them before sharing.

Usage:
    uv run python tutorials/capture_transfer_flow.py cl_bice
"""

import os
import sys

from dotenv import load_dotenv

from fintself import get_scraper
from fintself.utils.logging import logger

load_dotenv()


def main() -> None:
    bank_id = sys.argv[1] if len(sys.argv) > 1 else "cl_bice"
    prefix = bank_id.upper()
    user = os.getenv(f"{prefix}_USER")
    password = os.getenv(f"{prefix}_PASSWORD")
    if not user or not password:
        logger.error(f"Define {prefix}_USER and {prefix}_PASSWORD in your .env file.")
        sys.exit(1)

    scraper = get_scraper(bank_id, debug_mode=True)
    scraper.user = user
    scraper.password = password

    with scraper._browser_session():
        scraper._login()
        logger.info("Logged in. Use the browser window to walk the transfer flow.")
        step = 1
        while True:
            name = input(
                "\nEnter = capture, or type a name for this step (q to quit): "
            ).strip()
            if name.lower() == "q":
                break
            label = name.replace(" ", "_") or "step"
            scraper._save_debug_info(f"capture_{step:02d}_{label}")
            logger.info(f"Saved capture {step:02d}.")
            step += 1


if __name__ == "__main__":
    main()
