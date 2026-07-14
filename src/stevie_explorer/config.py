from pydantic_settings import BaseSettings, SettingsConfigDict

from stevie_explorer.identifiers import ServiceName
from stevie_explorer.kernel import BaseComponent

class ExplorerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    explorer_env: str = "development"

    api_host: str = "127.0.0.1"
    api_port: int = 8100

class Configuration(BaseComponent):
    name = ServiceName.CONFIGURATION

    def __init__(self) -> None:
        self.settings = ExplorerSettings()
