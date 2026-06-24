from datetime import datetime
from typing import Dict, Any, Optional
from shortener_bot.models.database import db


DEFAULTS = {
    "header": "",
    "footer": "",
    "post_header": "",
    "post_footer": "",
    "auto_delete": 0,
    "pending_link": "",
    "pending_short_link": "",
    "step": "none",
    "batch_id": "",
    "btn_download": 1,
    "btn_share": 1,
    "btn_tutorial": 1,
    "btn_link_in_caption": 1,
    "link_repeat_count": 1,
    "custom_buttons": [],
    "temp_media_id": "",
    "temp_media_type": "",
    "joined_at": "",
    "last_active": "",
    "total_downloads": 0,
    "total_uploads": 0,
    "link_filter": 0,
    "text_filter": 0,
    "pending_category": "",
    "pending_schedule": "",
    "temp_caption": "",
}


class UserModel:
    @staticmethod
    def get(chat_id: int) -> Dict[str, Any]:
        chat_id = str(chat_id)
        now = datetime.now().isoformat()
        user = db.users.find_one({"chat_id": chat_id})
        if not user:
            user = {**DEFAULTS, "chat_id": chat_id, "joined_at": now, "last_active": now}
            db.users.insert_one(user)
            StatsModel.increment("new_users")
        else:
            upd = {k: v for k, v in DEFAULTS.items() if k not in user}
            upd["last_active"] = now
            if upd:
                db.users.update_one({"chat_id": chat_id}, {"$set": upd})
            user.update(upd)
        return user

    @staticmethod
    def update(chat_id: int, updates: Dict[str, Any]) -> None:
        db.users.update_one({"chat_id": str(chat_id)}, {"$set": updates})

    @staticmethod
    def set_step(chat_id: int, step: str) -> None:
        db.users.update_one({"chat_id": str(chat_id)}, {"$set": {"step": step}})

    @staticmethod
    def is_admin(chat_id: int) -> bool:
        return bool(db.admins.find_one({"chat_id": str(chat_id)}))

    @staticmethod
    def is_banned(chat_id: int) -> bool:
        return bool(db.banned.find_one({"chat_id": str(chat_id)}))

    @staticmethod
    def add_admin(chat_id: int, role: str = "admin") -> None:
        db.admins.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {"chat_id": str(chat_id), "role": role, "added_at": datetime.now().isoformat()}},
            upsert=True,
        )

    @staticmethod
    def remove_admin(chat_id: int) -> None:
        db.admins.delete_one({"chat_id": str(chat_id)})

    @staticmethod
    def ban_user(chat_id: int) -> None:
        db.banned.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {"chat_id": str(chat_id), "banned_at": datetime.now().isoformat()}},
            upsert=True,
        )

    @staticmethod
    def unban_user(chat_id: int) -> None:
        db.banned.delete_one({"chat_id": str(chat_id)})


class StatsModel:
    @staticmethod
    def increment(field: str, n: int = 1) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        db.stats.update_one({"date": today}, {"$inc": {field: n}}, upsert=True)

    @staticmethod
    def get() -> Dict[str, Any]:
        today = datetime.now().strftime("%Y-%m-%d")
        td = db.stats.find_one({"date": today}) or {}
        active = db.users.count_documents({"last_active": {"$regex": f"^{today}"}})
        return {
            "total_users": db.users.count_documents({}),
            "total_files": db.files.count_documents({}),
            "total_admins": db.admins.count_documents({}),
            "total_banned": db.banned.count_documents({}),
            "active_today": active,
            "dl_today": td.get("downloads", 0),
            "ul_today": td.get("uploads", 0),
        }


class SettingsModel:
    @staticmethod
    def get(key: str, default: Any = 0) -> Any:
        doc = db.bot_settings.find_one({"key": key})
        return doc["value"] if doc else default

    @staticmethod
    def set(key: str, value: Any) -> None:
        db.bot_settings.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

    @staticmethod
    def toggle(key: str) -> int:
        new = 0 if SettingsModel.get(key, 0) else 1
        SettingsModel.set(key, new)
        return new