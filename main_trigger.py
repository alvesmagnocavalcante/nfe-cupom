"""Sincroniza XMLs de NFC-e com cópia validada, logs e alertas.

O programa lê as pastas de um arquivo INI, seleciona os XMLs modificados no
mês atual e mantém duas cópias: uma no destino principal e outra na pasta
temporária/intermediária. Ele não valida regras fiscais internas do XML; a
validação garante que a cópia seja idêntica ao arquivo de origem.
"""

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


BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("nfce_trigger")

# Tempo máximo de espera para o Google Drive disponibilizar a pasta montada.
GOOGLE_DRIVE_START_TIMEOUT_SECONDS = 60
GOOGLE_DRIVE_POLL_SECONDS = 2


def configure_logging(log_file: Path) -> None:
    """Envia os logs para o terminal e para um arquivo com rotação automática."""
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
    """Reúne todas as configurações necessárias para uma sincronização."""

    sources: tuple[Path, ...]
    destination: Path
    temporary: Path
    heartbeat_destination: Path
    heartbeat_file: Path
    hotel: str


def load_config(path: Path) -> Settings:
    """Lê o arquivo INI, valida os campos obrigatórios e devolve as configurações."""
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
    """Lista, pelo nome, somente os XMLs modificados no mês e ano atuais."""
    files: dict[str, Path] = {}
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() != ".xml":
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if (modified.year, modified.month) == (now.year, now.month):
            files.setdefault(path.name, path)
    return files


def file_sha256(path: Path) -> str:
    """Calcula o SHA-256 em blocos para não carregar o arquivo inteiro na memória."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files_match(source: Path, target: Path) -> bool:
    """Confirma que origem e destino têm o mesmo tamanho e o mesmo conteúdo."""
    try:
        # O tamanho é uma verificação rápida antes do cálculo de SHA-256.
        if not target.is_file() or source.stat().st_size != target.stat().st_size:
            return False
        return file_sha256(source) == file_sha256(target)
    except OSError:
        return False


def atomic_copy_verified(source: Path, target: Path) -> None:
    """Copia, valida e publica o arquivo sem expor uma cópia incompleta.

    A gravação ocorre primeiro em um arquivo oculto com extensão ``.part`` no
    mesmo diretório do destino. Somente após validar tamanho e SHA-256 esse
    arquivo substitui o destino definitivo por meio de ``os.replace``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"

    # Estes valores representam o arquivo que esperamos encontrar ao final.
    expected_size = source.stat().st_size
    expected_digest = file_sha256(source)
    try:
        shutil.copy2(source, temporary)

        # Nunca publica a cópia temporária se ela estiver truncada ou alterada.
        if temporary.stat().st_size != expected_size:
            raise OSError(f"Tamanho divergente após copiar {source} para {target}.")
        if file_sha256(temporary) != expected_digest:
            raise OSError(f"Conteúdo divergente após copiar {source} para {target}.")

        # A substituição só acontece depois de a cópia temporária ser aprovada.
        os.replace(temporary, target)

        # Confere novamente o arquivo já publicado no destino definitivo.
        if target.stat().st_size != expected_size or file_sha256(target) != expected_digest:
            raise OSError(f"Falha na validação final de {target}.")
    finally:
        # Remove qualquer sobra temporária em caso de sucesso ou erro.
        temporary.unlink(missing_ok=True)


def atomic_write_text(target: Path, content: str) -> None:
    """Grava um texto em arquivo temporário e o publica de forma atômica."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def send_alert(hotel: str, message: str) -> None:
    """Tenta enviar um alerta SMTP; ausência de credenciais não interrompe o processo."""
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
        port = int(os.getenv("NFCE_SMTP_PORT", "587"))
        with smtplib.SMTP(os.getenv("NFCE_SMTP_HOST", "smtp.gmail.com"), port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(email)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        LOGGER.error("Falha ao enviar alerta: %s", error)


def google_drive_version(path: Path) -> tuple[int, ...]:
    """Converte o nome da pasta de versão do Google Drive em números comparáveis."""
    try:
        return tuple(int(part) for part in path.parent.name.split("."))
    except ValueError:
        return ()


def running_on_windows() -> bool:
    """Informa em tempo de execução se o sincronizador está rodando no Windows."""
    return os.name == "nt"


def start_google_drive() -> None:
    """Inicia a versão mais recente instalada do Google Drive, se necessário."""
    # Evita abrir uma segunda instância quando o processo já está em execução.
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq GoogleDriveFS.exe"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "GoogleDriveFS.exe" in result.stdout:
        LOGGER.info("Google Drive já está em execução; aguardando a unidade ficar disponível.")
        return
    candidates = list(Path(r"C:\Program Files\Google\Drive File Stream").glob("*/GoogleDriveFS.exe"))
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
    """Garante a pasta temporária, iniciando o Google Drive no Windows se preciso."""
    if directory.is_dir():
        return
    if dry_run:
        raise RuntimeError(f"Pasta temporária indisponível: {directory}")

    # Pastas locais ou subpastas de uma unidade já montada podem ser criadas aqui.
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        LOGGER.warning("Não foi possível criar a pasta temporária %s: %s", directory, error)
    if directory.is_dir():
        return
    if not running_on_windows():
        raise RuntimeError(f"Pasta temporária indisponível: {directory}")

    # Se a unidade não existe no Windows, inicia o Drive e aguarda sua montagem.
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
        f"Google Drive não disponibilizou a pasta temporária em {timeout_seconds:.0f} segundo(s): {directory}"
    )


def sync(settings: Settings, dry_run: bool = False, now: datetime | None = None) -> int:
    """Executa uma sincronização completa e devolve zero quando ela termina bem."""
    now = now or datetime.now()

    # Uma origem indisponível gera aviso; o processo continua com as demais.
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
        raise RuntimeError("Nenhuma pasta de origem está disponível.")

    ensure_temporary_directory(settings.temporary, dry_run)

    # Consolida os arquivos de todas as origens usando o nome como identificador.
    settings.destination.mkdir(parents=True, exist_ok=True)
    source_files: dict[str, Path] = {}
    collisions: dict[str, list[Path]] = {}
    for source in sources:
        for name, path in current_month_xml(source, now).items():
            if name in source_files and source_files[name] != path:
                collisions.setdefault(name, [source_files[name]]).append(path)
                continue
            source_files[name] = path

    if collisions:
        # Interromper é mais seguro do que escolher silenciosamente uma das origens.
        details = "; ".join(
            f"{name}: {', '.join(str(path) for path in paths)}"
            for name, paths in sorted(collisions.items())
        )
        raise RuntimeError(f"Colisão de nomes entre origens: {details}")

    # Arquivos ausentes ou com conteúdo diferente precisam ser copiados novamente.
    pending_destination = sorted(
        name for name, source in source_files.items() if not files_match(source, settings.destination / name)
    )
    pending_temporary = sorted(
        name for name, source in source_files.items() if not files_match(source, settings.temporary / name)
    )

    # Atualiza primeiro o destino principal.
    for name in pending_destination:
        source = source_files[name]
        LOGGER.info("%s: %s -> %s", "[DRY-RUN] Copiaria" if dry_run else "Copiando", source, settings.destination / name)
        if not dry_run:
            atomic_copy_verified(source, settings.destination / name)

    # Depois atualiza a pasta intermediária, como uma pasta do Google Drive.
    for name in pending_temporary:
        source = source_files[name]
        LOGGER.info("%s: %s -> %s", "[DRY-RUN] Copiaria" if dry_run else "Copiando", source, settings.temporary / name)
        if not dry_run:
            atomic_copy_verified(source, settings.temporary / name)

    if not dry_run:
        # O heartbeat indica que todas as cópias anteriores terminaram com sucesso.
        settings.heartbeat_destination.mkdir(parents=True, exist_ok=True)
        atomic_write_text(settings.heartbeat_file, now.replace(tzinfo=None).isoformat())
        atomic_copy_verified(
            settings.heartbeat_file,
            settings.heartbeat_destination / settings.heartbeat_file.name,
        )
        LOGGER.info("Arquivo de monitoramento atualizado: %s", settings.heartbeat_file)
    LOGGER.info(
        "Sincronização concluída: "
        f"{len(pending_destination)} atualizado(s) no destino, "
        f"{len(pending_temporary)} atualizado(s) no temporário, "
        f"{len(source_files)} no mês."
    )
    return 0


def parse_args() -> argparse.Namespace:
    """Define os argumentos aceitos na linha de comando."""
    parser = argparse.ArgumentParser(description="Sincroniza XMLs de NFC-e do mês atual.")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "config" / "config.ini")
    parser.add_argument("--log-file", type=Path, default=BASE_DIR / "log" / "nfce_trigger.log")
    parser.add_argument("--dry-run", action="store_true", help="Exibe ações sem alterar arquivos.")
    return parser.parse_args()


def run(arguments: argparse.Namespace) -> int:
    """Coordena configuração, sincronização, tratamento de erro e alerta."""
    started_at = time.monotonic()
    LOGGER.info("Execução iniciada%s.", " em modo de simulação" if arguments.dry_run else "")
    settings = None
    try:
        settings = load_config(arguments.config)
        LOGGER.info("Hotel: %s | Origens configuradas: %s", settings.hotel, len(settings.sources))
        exit_code = sync(settings, arguments.dry_run)
    except Exception as error:
        # Qualquer falha fica no log e gera uma tentativa de alerta por e-mail.
        LOGGER.exception("Execução encerrada com erro: %s", error)
        send_alert(settings.hotel if settings else "Não informado", str(error))
        exit_code = 1
    LOGGER.info("Execução finalizada com código %s em %.2f segundo(s).", exit_code, time.monotonic() - started_at)
    return exit_code


if __name__ == "__main__":
    # Este bloco só é executado quando o arquivo é chamado diretamente.
    arguments = parse_args()
    configure_logging(arguments.log_file)
    raise SystemExit(run(arguments))
