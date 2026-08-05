import argparse
import configparser
import os
import shutil
import smtplib
import subprocess
from dataclasses import dataclass
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


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
        print(f"[WARN] Alerta não enviado; SMTP não configurado: {message}")
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
        print(f"[ERROR] Falha ao enviar alerta: {error}")


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
    sources = [source for source in settings.sources if source.is_dir()]
    unavailable = [str(source) for source in settings.sources if not source.is_dir()]
    for source in unavailable:
        print(f"[WARN] Origem indisponível: {source}")
    if not sources:
        message = "Nenhuma pasta de origem está disponível."
        send_alert(settings.hotel, message)
        raise RuntimeError(message)

    if not settings.temporary.is_dir():
        print(f"[WARN] Pasta temporária indisponível: {settings.temporary}")
        if os.name == "nt" and not dry_run:
            start_google_drive()
        send_alert(settings.hotel, f"Pasta temporária indisponível: {settings.temporary}")
        return 2

    settings.destination.mkdir(parents=True, exist_ok=True)
    source_files: dict[str, Path] = {}
    for source in sources:
        for name, path in current_month_xml(source, now).items():
            if name in source_files and source_files[name] != path:
                print(f"[WARN] Nome duplicado ignorado: {path}")
                continue
            source_files[name] = path

    destination_files = current_month_xml(settings.destination, now)
    temporary_files = current_month_xml(settings.temporary, now)
    missing_destination = sorted(set(source_files) - set(destination_files))
    missing_temporary = sorted(set(source_files) - set(temporary_files))

    for name in missing_destination:
        source = source_files[name]
        print(f"{'[DRY-RUN] Copiaria' if dry_run else 'Copiando'}: {source} -> {settings.destination / name}")
        if not dry_run:
            shutil.copy2(source, settings.destination / name)

    for name in missing_temporary:
        source = source_files[name]
        print(f"{'[DRY-RUN] Copiaria' if dry_run else 'Copiando'}: {source} -> {settings.temporary / name}")
        if not dry_run:
            shutil.copy2(source, settings.temporary / name)

    if not dry_run:
        settings.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        settings.heartbeat_destination.mkdir(parents=True, exist_ok=True)
        settings.heartbeat_file.write_text(now.replace(tzinfo=None).isoformat(), encoding="utf-8")
        shutil.copy2(settings.heartbeat_file, settings.heartbeat_destination / settings.heartbeat_file.name)
    print(
        "Sincronização concluída: "
        f"{len(missing_destination)} novo(s) no destino, "
        f"{len(missing_temporary)} novo(s) no temporário, "
        f"{len(source_files)} no mês."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza XMLs de NFC-e do mês atual.")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "config" / "config.ini")
    parser.add_argument("--dry-run", action="store_true", help="Exibe ações sem alterar arquivos.")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(sync(load_config(arguments.config), arguments.dry_run))
