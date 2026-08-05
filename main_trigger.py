import argparse
import configparser
import logging
import os
import shutil
import smtplib
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("nfce_trigger")


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
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    LOGGER.addHandler(console_handler)
    LOGGER.addHandler(file_handler)


@dataclass(frozen=True)
class Settings:
    sources: tuple[Path, ...]
    destination: Path
    temporary: Path
    heartbeat_destination: Path
    heartbeat_file: Path
    hotel: str


def load_config(path: Path) -> Settings:
    if not path.is_file():
        raise FileNotFoundError(f"Configuração não encontrada: {path}")

    config = configparser.ConfigParser()
    config.read(path, encoding="utf-8-sig")
    if not config.has_section("sources") or not config.items("sources"):
        raise ValueError("Nenhuma origem configurada em [sources].")

    required = {
        "destination_directory",
        "temporary_directory",
        "destino",
        "ultimaexecucaofile",
    }
    paths = {key.lower(): value.strip().strip("\"'") for key, value in config.items("paths")}
    missing = required - paths.keys()
    if missing:
        raise ValueError(f"Configurações ausentes em [paths]: {', '.join(sorted(missing))}")

    return Settings(
        sources=tuple(Path(value.strip().strip("\"'")) for _, value in config.items("sources") if value.strip()),
        destination=Path(paths["destination_directory"]),
        temporary=Path(paths["temporary_directory"]),
        heartbeat_destination=Path(paths["destino"]),
        heartbeat_file=Path(paths["ultimaexecucaofile"]),
        hotel=config.get("app", "hotel", fallback="Não informado"),
    )


def current_month_xml(directory: Path, now: datetime) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() != ".xml":
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if (modified.year, modified.month) == (now.year, now.month):
            files.setdefault(path.name, path)
    return files


def send_alert(hotel: str, message: str) -> None:
    user = os.getenv("NFCE_SMTP_USER")
    password = os.getenv("NFCE_SMTP_PASSWORD")
    recipient = os.getenv("NFCE_ALERT_RECIPIENT")
    if not all((user, password, recipient)):
        LOGGER.warning("Alerta não enviado; SMTP não configurado: %s", message)
        return

    email = MIMEText(f"Problema no NFCeTrigger do hotel {hotel}: {message}", _charset="utf-8")
    email["From"], email["To"] = user, recipient
    email["Subject"] = f"Problema no NFCeTrigger do Hotel {hotel}"
    try:
        with smtplib.SMTP(os.getenv("NFCE_SMTP_HOST", "smtp.gmail.com"), int(os.getenv("NFCE_SMTP_PORT", "587"))) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(email)
    except (OSError, smtplib.SMTPException) as error:
        LOGGER.error("Falha ao enviar alerta: %s", error)


def start_google_drive() -> None:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq GoogleDriveFS.exe"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "GoogleDriveFS.exe" in result.stdout:
        return
    candidates = list(Path(r"C:\Program Files\Google\Drive File Stream").glob("*/GoogleDriveFS.exe"))
    if candidates:
        subprocess.Popen([str(max(candidates, key=lambda path: path.parent.name))])


def sync(settings: Settings, dry_run: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now()
    sources: list[Path] = []
    for source in settings.sources:
        try:
            available = source.is_dir()
        except OSError as error:
            LOGGER.warning("Origem indisponível: %s (%s)", source, error)
            continue
        if available:
            sources.append(source)
        else:
            LOGGER.warning("Origem indisponível: %s", source)

    if not sources:
        message = "Nenhuma pasta de origem está disponível."
        send_alert(settings.hotel, message)
        raise RuntimeError(message)

    if not settings.temporary.is_dir():
        LOGGER.warning("Pasta temporária indisponível: %s", settings.temporary)
        if os.name == "nt" and not dry_run:
            start_google_drive()
        send_alert(settings.hotel, f"Pasta temporária indisponível: {settings.temporary}")
        return 2

    settings.destination.mkdir(parents=True, exist_ok=True)
    source_files: dict[str, Path] = {}
    for source in sources:
        for name, path in current_month_xml(source, now).items():
            if name in source_files and source_files[name] != path:
                LOGGER.warning("Nome duplicado ignorado: %s", path)
                continue
            source_files[name] = path

    destination_files = current_month_xml(settings.destination, now)
    temporary_files = current_month_xml(settings.temporary, now)
    missing_destination = sorted(set(source_files) - set(destination_files))
    missing_temporary = sorted(set(source_files) - set(temporary_files))

    for name in missing_destination:
        source = source_files[name]
        LOGGER.info("%s: %s -> %s", "[DRY-RUN] Copiaria" if dry_run else "Copiando", source, settings.destination / name)
        if not dry_run:
            shutil.copy2(source, settings.destination / name)

    for name in missing_temporary:
        source = source_files[name]
        LOGGER.info("%s: %s -> %s", "[DRY-RUN] Copiaria" if dry_run else "Copiando", source, settings.temporary / name)
        if not dry_run:
            shutil.copy2(source, settings.temporary / name)

    if not dry_run:
        settings.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        settings.heartbeat_destination.mkdir(parents=True, exist_ok=True)
        settings.heartbeat_file.write_text(now.replace(tzinfo=None).isoformat(), encoding="utf-8")
        shutil.copy2(settings.heartbeat_file, settings.heartbeat_destination / settings.heartbeat_file.name)
        LOGGER.info("Arquivo de monitoramento atualizado: %s", settings.heartbeat_file)
    LOGGER.info(
        "Sincronização concluída: "
        f"{len(missing_destination)} novo(s) no destino, "
        f"{len(missing_temporary)} novo(s) no temporário, "
        f"{len(source_files)} no mês."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza XMLs de NFC-e do mês atual.")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "config" / "config.ini")
    parser.add_argument("--log-file", type=Path, default=BASE_DIR / "log" / "nfce_trigger.log")
    parser.add_argument("--dry-run", action="store_true", help="Exibe ações sem alterar arquivos.")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    configure_logging(arguments.log_file)
    started_at = time.monotonic()
    LOGGER.info("Execução iniciada%s.", " em modo de simulação" if arguments.dry_run else "")
    try:
        settings = load_config(arguments.config)
        LOGGER.info("Hotel: %s | Origens configuradas: %s", settings.hotel, len(settings.sources))
        exit_code = sync(settings, arguments.dry_run)
    except Exception as error:
        LOGGER.exception("Execução encerrada com erro: %s", error)
        exit_code = 1
    LOGGER.info("Execução finalizada com código %s em %.2f segundo(s).", exit_code, time.monotonic() - started_at)
    raise SystemExit(exit_code)
