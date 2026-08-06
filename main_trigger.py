# Sincronizador de XMLs de NFC-e com validação, logs e alertas.

import argparse
import configparser
import hashlib
import logging
import os
import shutil
import smtplib
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path


# Configuração geral

BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("nfce_trigger")

GOOGLE_DRIVE_START_TIMEOUT_SECONDS = 60
GOOGLE_DRIVE_POLL_SECONDS = 2
FILE_READ_BLOCK_SIZE = 1024 * 1024
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 10

REQUIRED_PATH_SETTINGS = {
    "destination_directory",
    "temporary_directory",
    "destino",
    "ultimaexecucaofile",
}


@dataclass(frozen=True)
class Settings:
    sources: tuple[Path, ...]
    destination: Path
    temporary: Path
    heartbeat_destination: Path
    heartbeat_file: Path
    hotel: str


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


# Leitura da configuração

def clean_config_value(value: str) -> str:
    return value.strip().strip("\"'")


def load_sources(config: configparser.ConfigParser) -> tuple[Path, ...]:
    if not config.has_section("sources"):
        return ()

    sources: list[Path] = []
    for _, raw_value in config.items("sources"):
        value = clean_config_value(raw_value)
        if value:
            sources.append(Path(value))
    return tuple(sources)


def load_paths(config: configparser.ConfigParser) -> dict[str, str]:
    if not config.has_section("paths"):
        return {}

    return {
        key.lower(): clean_config_value(value)
        for key, value in config.items("paths")
    }


def load_config(path: Path) -> Settings:
    if not path.is_file():
        raise FileNotFoundError(f"Configuração não encontrada: {path}")

    config = configparser.ConfigParser()
    config.read(path, encoding="utf-8-sig")

    sources = load_sources(config)
    if not sources:
        raise ValueError("Nenhuma origem configurada em [sources].")

    paths = load_paths(config)
    missing = {key for key in REQUIRED_PATH_SETTINGS if not paths.get(key)}
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"Configurações ausentes em [paths]: {missing_names}")

    return Settings(
        sources=sources,
        destination=Path(paths["destination_directory"]),
        temporary=Path(paths["temporary_directory"]),
        heartbeat_destination=Path(paths["destino"]),
        heartbeat_file=Path(paths["ultimaexecucaofile"]),
        hotel=config.get("app", "hotel", fallback="Não informado"),
    )


# Operações com arquivos

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(FILE_READ_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def files_match(source: Path, target: Path) -> bool:
    try:
        if not target.is_file() or source.stat().st_size != target.stat().st_size:
            return False
        return file_sha256(source) == file_sha256(target)
    except OSError:
        return False


def atomic_copy_verified(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
    expected_size = source.stat().st_size
    expected_digest = file_sha256(source)

    try:
        shutil.copy2(source, temporary)

        if temporary.stat().st_size != expected_size:
            raise OSError(f"Tamanho divergente após copiar {source} para {target}.")
        if file_sha256(temporary) != expected_digest:
            raise OSError(f"Conteúdo divergente após copiar {source} para {target}.")

        os.replace(temporary, target)

        if target.stat().st_size != expected_size:
            raise OSError(f"Falha na validação final de {target}.")
        if file_sha256(target) != expected_digest:
            raise OSError(f"Falha na validação final de {target}.")
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"

    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


# Google Drive e diretório temporário

def running_on_windows() -> bool:
    return os.name == "nt"


def google_drive_version(path: Path) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in path.parent.name.split("."))
    except ValueError:
        return ()


def start_google_drive() -> None:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq GoogleDriveFS.exe"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "GoogleDriveFS.exe" in result.stdout:
        LOGGER.info(
            "Google Drive já está em execução; aguardando a unidade ficar disponível."
        )
        return

    installation_dir = Path(r"C:\Program Files\Google\Drive File Stream")
    candidates = list(installation_dir.glob("*/GoogleDriveFS.exe"))
    if not candidates:
        raise FileNotFoundError("Executável do Google Drive não encontrado.")

    executable = max(candidates, key=google_drive_version)
    subprocess.Popen([str(executable)])
    LOGGER.info("Google Drive iniciado: %s", executable)


def ensure_temporary_directory(
    directory: Path,
    dry_run: bool,
    timeout_seconds: float = GOOGLE_DRIVE_START_TIMEOUT_SECONDS,
    poll_seconds: float = GOOGLE_DRIVE_POLL_SECONDS,
) -> None:
    if directory.is_dir():
        return
    if dry_run:
        raise RuntimeError(f"Pasta temporária indisponível: {directory}")

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        LOGGER.warning(
            "Não foi possível criar a pasta temporária %s: %s", directory, error
        )

    if directory.is_dir():
        return
    if not running_on_windows():
        raise RuntimeError(f"Pasta temporária indisponível: {directory}")

    start_google_drive()
    deadline = time.monotonic() + timeout_seconds

    while True:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        if directory.is_dir():
            LOGGER.info("Pasta temporária disponível: %s", directory)
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))

    raise TimeoutError(
        "Google Drive não disponibilizou a pasta temporária em "
        f"{timeout_seconds:.0f} segundo(s): {directory}"
    )


# Coleta e sincronização

def current_month_xml(directory: Path, now: datetime) -> dict[str, Path]:
    files: dict[str, Path] = {}

    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() != ".xml":
            continue

        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if (modified.year, modified.month) == (now.year, now.month):
            files.setdefault(path.name, path)

    return files


def available_sources(sources: tuple[Path, ...]) -> list[Path]:
    available: list[Path] = []

    for source in sources:
        try:
            if source.is_dir():
                available.append(source)
            else:
                LOGGER.warning("Origem indisponível: %s", source)
        except OSError as error:
            LOGGER.warning("Origem indisponível: %s (%s)", source, error)

    return available


def collect_source_files(sources: list[Path], now: datetime) -> dict[str, Path]:
    source_files: dict[str, Path] = {}
    collisions: dict[str, list[Path]] = {}

    for source in sources:
        for name, path in current_month_xml(source, now).items():
            existing = source_files.get(name)
            if existing is not None and existing != path:
                collisions.setdefault(name, [existing]).append(path)
            else:
                source_files[name] = path

    if collisions:
        details = "; ".join(
            f"{name}: {', '.join(str(path) for path in paths)}"
            for name, paths in sorted(collisions.items())
        )
        raise RuntimeError(f"Colisão de nomes entre origens: {details}")

    return source_files


def copy_changed_files(
    source_files: dict[str, Path],
    destination: Path,
    dry_run: bool,
) -> int:
    changed = 0

    for name, source in sorted(source_files.items()):
        target = destination / name
        if files_match(source, target):
            continue

        changed += 1
        action = "[DRY-RUN] Copiaria" if dry_run else "Copiando"
        LOGGER.info("%s: %s -> %s", action, source, target)
        if not dry_run:
            atomic_copy_verified(source, target)

    return changed


def update_heartbeat(settings: Settings, now: datetime) -> None:
    settings.heartbeat_destination.mkdir(parents=True, exist_ok=True)
    atomic_write_text(settings.heartbeat_file, now.replace(tzinfo=None).isoformat())
    atomic_copy_verified(
        settings.heartbeat_file,
        settings.heartbeat_destination / settings.heartbeat_file.name,
    )
    LOGGER.info("Arquivo de monitoramento atualizado: %s", settings.heartbeat_file)


def sync(settings: Settings, dry_run: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now()
    sources = available_sources(settings.sources)

    if not sources:
        raise RuntimeError("Nenhuma pasta de origem está disponível.")

    ensure_temporary_directory(settings.temporary, dry_run)
    if not dry_run:
        settings.destination.mkdir(parents=True, exist_ok=True)

    source_files = collect_source_files(sources, now)
    destination_changes = copy_changed_files(
        source_files,
        settings.destination,
        dry_run,
    )
    temporary_changes = copy_changed_files(
        source_files,
        settings.temporary,
        dry_run,
    )

    if not dry_run:
        update_heartbeat(settings, now)

    LOGGER.info(
        "Sincronização concluída: %s atualizado(s) no destino, "
        "%s atualizado(s) no temporário, %s no mês.",
        destination_changes,
        temporary_changes,
        len(source_files),
    )
    return 0


# Alertas

def send_alert(hotel: str, message: str) -> None:
    user = os.getenv("NFCE_SMTP_USER")
    password = os.getenv("NFCE_SMTP_PASSWORD")
    recipient = os.getenv("NFCE_ALERT_RECIPIENT")

    if not all((user, password, recipient)):
        LOGGER.warning("Alerta não enviado; SMTP não configurado: %s", message)
        return

    email = MIMEText(
        f"Problema no NFCeTrigger do hotel {hotel}: {message}",
        _charset="utf-8",
    )
    email["From"], email["To"] = user, recipient
    email["Subject"] = f"Problema no NFCeTrigger do Hotel {hotel}"

    try:
        port = int(os.getenv("NFCE_SMTP_PORT", "587"))
        host = os.getenv("NFCE_SMTP_HOST", "smtp.gmail.com")
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(email)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        LOGGER.error("Falha ao enviar alerta: %s", error)


# Linha de comando e execução

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


if __name__ == "__main__":
    raise SystemExit(main())
