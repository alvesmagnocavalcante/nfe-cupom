import logging
import os
import subprocess
import time
from pathlib import Path


# Configuração

LOGGER = logging.getLogger("nfce_trigger")
GOOGLE_DRIVE_START_TIMEOUT_SECONDS = 60
GOOGLE_DRIVE_POLL_SECONDS = 2
GOOGLE_DRIVE_INSTALLATION_DIR = Path(
    r"C:\Program Files\Google\Drive File Stream"
)


# Inicialização do Google Drive

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

    candidates = list(GOOGLE_DRIVE_INSTALLATION_DIR.glob("*/GoogleDriveFS.exe"))
    if not candidates:
        raise FileNotFoundError("Executável do Google Drive não encontrado.")

    executable = max(candidates, key=google_drive_version)
    subprocess.Popen([str(executable)])
    LOGGER.info("Google Drive iniciado: %s", executable)


# Disponibilidade da pasta temporária

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
