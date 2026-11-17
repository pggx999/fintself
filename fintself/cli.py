import os
from getpass import getpass
from typing import Optional

import typer
from dotenv import load_dotenv
from pydantic import ValidationError

from fintself.core.exceptions import FintselfException, TransferStatusUnknown
from fintself.core.models import TransferRequest, TransferSummary
from fintself.scrapers import get_scraper, list_available_scrapers
from fintself.transfers import SecondFactorChallenge, get_transferer
from fintself.utils.logging import logger
from fintself.utils.output import (
    get_output_data,
    save_to_csv,
    save_to_json,
    save_to_xlsx,
)

load_dotenv()

app = typer.Typer(
    name="fintself",
    help="Fintself: Open source collaborative bank transaction scraper.",
    add_completion=False,
    rich_markup_mode="markdown",
)


@app.command(name="list")
def list_scrapers_command():
    """
    Lists all available bank scrapers.
    """
    logger.info("Available bank scrapers:")
    scrapers = list_available_scrapers()
    if not scrapers:
        logger.warning("No available scrapers found.")
        return

    for bank_id, description in scrapers.items():
        typer.echo(f"- {typer.style(bank_id, fg=typer.colors.GREEN)}: {description}")


@app.command(name="scrape")
def scrape_bank_command(
    bank_id: str = typer.Argument(
        ..., help="The bank identifier to scrape (e.g.: cl_santander)."
    ),
    output_file: Optional[str] = typer.Option(
        None,
        "--output-file",
        "-o",
        help="Output file path (e.g.: my_data.xlsx). Format is inferred from extension.",
    ),
    output_format: Optional[str] = typer.Option(
        None,
        "--output-format",
        "-f",
        help="Output format for console if no file is used. Options: json, csv.",
        case_sensitive=False,
    ),
    debug_mode: Optional[bool] = typer.Option(
        None,
        "--debug/--no-debug",
        help="Enable or disable debug mode, overriding .env configuration.",
        show_default=False,
    ),
    headless: Optional[bool] = typer.Option(
        None,
        "--headless/--no-headless",
        help="Run browser in headless mode (may not work properly with some banks) or visible mode, overriding .env configuration.",
        show_default=False,
    ),
):
    """
    Executes a scraper to extract bank movements.
    """
    if not output_file and not output_format:
        logger.error(
            "You must specify --output-file to save output or --output-format to print to console."
        )
        raise typer.Exit(code=1)

    if output_file and output_format:
        logger.warning(
            "Both --output-file and --output-format specified. --output-file will be prioritized."
        )
        output_format = None

    file_format = None
    if output_file:
        _, ext = os.path.splitext(output_file)
        ext = ext.lower()
        if ext == ".xlsx":
            file_format = "xlsx"
        elif ext == ".csv":
            file_format = "csv"
        elif ext == ".json":
            file_format = "json"
        else:
            logger.error(
                f"File extension '{ext}' not supported. Use .xlsx, .csv, or .json."
            )
            raise typer.Exit(code=1)

    user_env_var = f"{bank_id.upper().replace('-', '_')}_USER"
    password_env_var = f"{bank_id.upper().replace('-', '_')}_PASSWORD"

    user = os.getenv(user_env_var)
    password = os.getenv(password_env_var)

    if not user:
        user = typer.prompt(f"Usuario para {bank_id}")
    if not password:
        password = getpass(f"Password for {bank_id}: ")

    try:
        # Warn if headless mode is explicitly enabled
        if headless is True:
            logger.warning(
                "⚠️  Headless mode enabled. Some banks may not work properly in headless mode. "
                "If you encounter issues, try running without --headless (visible mode)."
            )

        # Pass overrides to scraper factory. If None, settings from .env will be used.
        scraper = get_scraper(bank_id, headless=headless, debug_mode=debug_mode)
        movements = scraper.scrape(user=user, password=password)

        if not movements:
            logger.info("No movements found.")
            raise typer.Exit(code=0)

        if output_file:
            if file_format == "xlsx":
                save_to_xlsx(movements, output_file)
            elif file_format == "csv":
                save_to_csv(movements, output_file)
            elif file_format == "json":
                save_to_json(movements, output_file)
            logger.info(f"Scraping completed. Data saved to {output_file}")
        elif output_format in ["json", "csv"]:
            output_data = get_output_data(movements, output_format)
            typer.echo(output_data)
            logger.info("Scraping completed. Data printed to console.")
        else:
            logger.error(
                f"Output format '{output_format}' not valid. Use 'json' or 'csv'."
            )
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except FintselfException as e:
        logger.error(f"Error en el scraping: {str(e)}")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=debug_mode)
        raise typer.Exit(code=1)


def _get_credentials(bank_id: str):
    prefix = bank_id.upper().replace("-", "_")
    user = os.getenv(f"{prefix}_USER") or typer.prompt(f"Usuario para {bank_id}")
    password = os.getenv(f"{prefix}_PASSWORD") or getpass(f"Password for {bank_id}: ")
    return user, password


def _print_summary(request: TransferRequest, summary: TransferSummary) -> None:
    typer.echo("")
    typer.secho("Resumen mostrado por el banco:", bold=True)
    rows = [
        (
            "Monto",
            f"${int(summary.amount):,}".replace(",", ".") if summary.amount else "-",
        ),
        ("Destinatario", summary.destination_name or "-"),
        ("RUT", summary.destination_rut or "-"),
        ("Banco", summary.destination_bank or "-"),
        ("Cuenta destino", summary.destination_account or "-"),
        ("Cuenta origen", summary.origin_account or "-"),
    ]
    for label, value in rows:
        typer.echo(f"  {label:<15} {value}")
    if request.message:
        typer.echo(f"  {'Mensaje':<15} {request.message}")
    typer.echo("")


def _confirm_transfer(request: TransferRequest, summary: TransferSummary) -> bool:
    _print_summary(request, summary)
    typed = typer.prompt(
        f"Para enviar, escribe el monto exacto ({int(request.amount)}). "
        "Cualquier otra cosa cancela",
        default="",
        show_default=False,
    )
    return typed.strip() == str(int(request.amount))


def _second_factor(challenge: SecondFactorChallenge):
    if challenge.kind == "code":
        return getpass(f"{challenge.message} ")
    typer.prompt(
        f"{challenge.message} Presiona Enter cuando la hayas aprobado",
        default="",
        show_default=False,
    )
    return None


@app.command(name="transfer")
def transfer_command(
    bank_id: str = typer.Argument(..., help="Bank identifier (e.g.: cl_bice)."),
    rut: str = typer.Option(..., "--rut", help="Recipient RUT (e.g.: 12.345.678-5)."),
    account: str = typer.Option(..., "--account", help="Recipient account number."),
    amount: int = typer.Option(..., "--amount", help="Amount in CLP, whole pesos."),
    message: Optional[str] = typer.Option(
        None, "--message", help="Optional comment for the recipient."
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Actually send the transfer. Without it, runs a dry run that stops "
        "on the bank's confirmation screen.",
    ),
    allow_duplicate: bool = typer.Option(
        False,
        "--allow-duplicate",
        help="Allow a transfer identical to one attempted recently.",
    ),
    debug_mode: Optional[bool] = typer.Option(
        None, "--debug/--no-debug", show_default=False
    ),
    headless: Optional[bool] = typer.Option(
        None, "--headless/--no-headless", show_default=False
    ),
):
    """
    Transfers CLP to a recipient already registered in the bank's website.

    Dry run by default. With --execute it asks you to type the amount after
    showing the bank's confirmation screen, then waits for your second factor.
    """
    try:
        request = TransferRequest(
            destination_rut=rut,
            destination_account=account,
            amount=amount,
            message=message,
        )
    except ValidationError as e:
        for err in e.errors():
            logger.error(f"{err['loc'][0]}: {err['msg']}")
        raise typer.Exit(code=1)

    try:
        transferer = get_transferer(bank_id, headless=headless, debug_mode=debug_mode)
        transferer.check_request(
            request, dry_run=not execute, allow_duplicate=allow_duplicate
        )
        user, password = _get_credentials(bank_id)
        result = transferer.transfer(
            user,
            password,
            request,
            confirm=_confirm_transfer,
            second_factor=_second_factor,
            dry_run=not execute,
            allow_duplicate=allow_duplicate,
        )
    except TransferStatusUnknown as e:
        logger.critical(str(e))
        raise typer.Exit(code=3)
    except FintselfException as e:
        logger.error(f"Transfer not sent: {e}")
        raise typer.Exit(code=1)

    if result.status == "dry_run":
        _print_summary(request, result.summary)
        typer.secho(
            "Dry run OK: el resumen coincide. No se envió nada. "
            "Repite con --execute para transferir.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            f"Transferencia enviada. Comprobante: {result.receipt_id or 'no disponible'}",
            fg=typer.colors.GREEN,
        )


if __name__ == "__main__":
    app()
