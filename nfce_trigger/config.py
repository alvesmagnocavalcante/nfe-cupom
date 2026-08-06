import configparser
from dataclasses import dataclass
from pathlib import Path


# Modelo e campos obrigatórios

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
