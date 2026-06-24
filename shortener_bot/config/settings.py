import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class BotConfig:
    token: str
    username: str
    main_admin_id: int
    version: str = "6.0.0"


@dataclass
class DatabaseConfig:
    mongo_url: str
    db_name: str = "shortener_bot_db"


@dataclass
class ExternalAPIConfig:
    terabox_token: str
    shortener_api_url: str = "https://teraboxlinks.com/api"


@dataclass
class RenderConfig:
    webhook_url: Optional[str] = None
    port: int = 10000
    panel_secret: Optional[str] = None


@dataclass
class Settings:
    bot: BotConfig
    database: DatabaseConfig
    external_api: ExternalAPIConfig
    render: RenderConfig

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot=BotConfig(
                token=os.environ.get("BOT_TOKEN", ""),
                username=os.environ.get("BOT_USERNAME", ""),
                main_admin_id=int(os.environ.get("MAIN_ADMIN_ID", "0")),
            ),
            database=DatabaseConfig(
                mongo_url=os.environ.get("MONGO_URL", ""),
            ),
            external_api=ExternalAPIConfig(
                terabox_token=os.environ.get("TERABOX_TOKEN", ""),
            ),
            render=RenderConfig(
                webhook_url=os.environ.get("WEBHOOK_URL"),
                port=int(os.environ.get("PORT", "10000")),
                panel_secret=os.environ.get("PANEL_SECRET"),
            ),
        )


settings = Settings.from_env()