from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from shortener_bot.config.settings import settings
import logging

logger = logging.getLogger(__name__)


class DatabaseManager:
    _instance: "DatabaseManager" = None
    _client: MongoClient = None
    _db: Database = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._client is None:
            self._connect()

    def _connect(self):
        try:
            self._client = MongoClient(settings.database.mongo_url)
            self._db = self._client[settings.database.db_name]
            self._create_indexes()
            logger.info("MongoDB connected successfully")
        except Exception as e:
            logger.error(f"MongoDB connection failed: {e}")
            raise

    def _create_indexes(self):
        users = self._db["users"]
        files = self._db["files"]
        queue = self._db["queue"]
        scheduled = self._db["scheduled_posts"]

        users.create_index("chat_id", unique=True, background=True)
        files.create_index("file_key", background=True)
        files.create_index("batch_id", background=True)
        queue.create_index("delete_at", background=True)
        scheduled.create_index("scheduled_at", background=True)

    @property
    def db(self) -> Database:
        return self._db

    @property
    def users(self) -> Collection:
        return self._db["users"]

    @property
    def files(self) -> Collection:
        return self._db["files"]

    @property
    def queue(self) -> Collection:
        return self._db["queue"]

    @property
    def admins(self) -> Collection:
        return self._db["admins"]

    @property
    def channels(self) -> Collection:
        return self._db["update_channels"]

    @property
    def tutorials(self) -> Collection:
        return self._db["tutorials"]

    @property
    def auto_channels(self) -> Collection:
        return self._db["auto_channels"]

    @property
    def stats(self) -> Collection:
        return self._db["bot_stats"]

    @property
    def banned(self) -> Collection:
        return self._db["banned_users"]

    @property
    def force_sub(self) -> Collection:
        return self._db["force_subscribe"]

    @property
    def bot_settings(self) -> Collection:
        return self._db["bot_settings"]

    @property
    def categories(self) -> Collection:
        return self._db["categories"]

    @property
    def scheduled_posts(self) -> Collection:
        return self._db["scheduled_posts"]


db = DatabaseManager()