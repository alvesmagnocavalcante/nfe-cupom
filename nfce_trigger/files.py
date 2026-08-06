import hashlib
import os
import shutil
import uuid
from pathlib import Path


# Validação de conteúdo

FILE_READ_BLOCK_SIZE = 1024 * 1024


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


# Escrita segura

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
