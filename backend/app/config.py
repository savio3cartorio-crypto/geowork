from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # Variáveis de ambiente com prefixo GEOWORK_, ex.: GEOWORK_DATABASE_URL
    database_url: str = "postgresql://geowork:geowork@localhost:5432/geowork"
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    # Onde os PDFs enviados ficam (fora do banco; o banco guarda só o caminho relativo)
    storage_dir: Path = BACKEND_DIR / "storage"
    # Sessão de login: validade em horas e cookie só por HTTPS (desligar apenas em testes locais sem HTTPS)
    sessao_horas: int = 8
    cookie_seguro: bool = True

    model_config = SettingsConfigDict(env_prefix="GEOWORK_")


settings = Settings()
