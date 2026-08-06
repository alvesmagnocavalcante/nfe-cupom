import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .alerts import send_alert
from .config import load_config
from .sync import sync


# Configuração

BASE_DIR = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger("nfce_trigger")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 10


# Logs

def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    LOGGER.addHandler(console_handler)
    LOGGER.addHandler(file_handler)


# Linha de comando

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza XMLs de NFC-e do mês atual."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=BASE_DIR / "config" / "config.ini",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=BASE_DIR / "log" / "nfce_trigger.log",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Exibe ações sem alterar arquivos.",
    )
    return parser.parse_args(argv)


# Execução

def run(arguments: argparse.Namespace) -> int:
    started_at = time.monotonic()
    mode = " em modo de simulação" if arguments.dry_run else ""
    LOGGER.info("Execução iniciada%s.", mode)

    settings = None
    try:
        settings = load_config(arguments.config)
        LOGGER.info(
            "Hotel: %s | Origens configuradas: %s",
            settings.hotel,
            len(settings.sources),
        )
        exit_code = sync(settings, arguments.dry_run)
    except Exception as error:
        LOGGER.exception("Execução encerrada com erro: %s", error)
        hotel = settings.hotel if settings else "Não informado"
        send_alert(hotel, str(error))
        exit_code = 1

    duration = time.monotonic() - started_at
    LOGGER.info(
        "Execução finalizada com código %s em %.2f segundo(s).",
        exit_code,
        duration,
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    configure_logging(arguments.log_file)
    return run(arguments)
