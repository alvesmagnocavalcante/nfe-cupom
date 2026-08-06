import logging
from datetime import datetime
from pathlib import Path

from .config import Settings
from .files import atomic_copy_verified, atomic_write_text, files_match
from .google_drive import ensure_temporary_directory


# Configuração

LOGGER = logging.getLogger("nfce_trigger")


# Coleta dos XMLs

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


# Cópia e monitoramento

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


# Fluxo da sincronização

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
