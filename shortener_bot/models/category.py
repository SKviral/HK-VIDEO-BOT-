from typing import List, Dict, Any, Optional
from shortener_bot.models.database import db


class CategoryModel:
    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        return list(db.categories.find())

    @staticmethod
    def get(cat_id: str) -> Optional[Dict[str, Any]]:
        return db.categories.find_one({"cat_id": cat_id})

    @staticmethod
    def get_by_name(name: str) -> Optional[Dict[str, Any]]:
        return db.categories.find_one({"name": name})

    @staticmethod
    def create(name: str) -> str:
        import uuid
        cat_id = uuid.uuid4().hex[:10]
        db.categories.insert_one({
            "cat_id": cat_id,
            "name": name,
            "channels": [],
            "created_at": __import__("datetime").datetime.now().isoformat(),
        })
        return cat_id

    @staticmethod
    def delete(cat_id: str) -> bool:
        result = db.categories.delete_one({"cat_id": cat_id})
        return result.deleted_count > 0

    @staticmethod
    def add_channel(cat_id: str, channel_data: Dict[str, Any]) -> bool:
        result = db.categories.update_one(
            {"cat_id": cat_id},
            {"$push": {"channels": channel_data}}
        )
        return result.modified_count > 0

    @staticmethod
    def remove_channel(cat_id: str, channel_id: str) -> bool:
        result = db.categories.update_one(
            {"cat_id": cat_id},
            {"$pull": {"channels": {"channel_id": channel_id}}}
        )
        return result.modified_count > 0

    @staticmethod
    def get_channels(cat_id: str, ch_type: str = None, status: str = "on") -> List[Dict[str, Any]]:
        cat = CategoryModel.get(cat_id)
        if not cat:
            return []
        channels = cat.get("channels", [])
        if ch_type:
            channels = [c for c in channels if c.get("type") == ch_type]
        if status:
            channels = [c for c in channels if c.get("status") == status]
        return channels


class ChannelModel:
    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        return list(db.auto_channels.find())

    @staticmethod
    def get_by_type(ch_type: str, status: str = "on") -> List[Dict[str, Any]]:
        return list(db.auto_channels.find({"type": ch_type, "status": status}))

    @staticmethod
    def add(channel_id: str, name: str, ch_type: str, url: str) -> str:
        import uuid
        ch_id = uuid.uuid4().hex[:10]
        db.auto_channels.insert_one({
            "ch_id": ch_id,
            "channel_id": channel_id,
            "name": name,
            "type": ch_type,
            "url": url,
            "status": "on",
            "added_at": __import__("datetime").datetime.now().isoformat(),
        })
        return ch_id

    @staticmethod
    def update_status(ch_id: str, status: str) -> bool:
        result = db.auto_channels.update_one(
            {"ch_id": ch_id},
            {"$set": {"status": status}}
        )
        return result.modified_count > 0

    @staticmethod
    def delete(ch_id: str) -> bool:
        result = db.auto_channels.delete_one({"ch_id": ch_id})
        return result.deleted_count > 0


class ForceSubscribeModel:
    @staticmethod
    def get_all_active() -> List[Dict[str, Any]]:
        return list(db.force_sub.find({"status": "on"}))

    @staticmethod
    def add(channel_id: str, name: str, url: str) -> str:
        import uuid
        fs_id = uuid.uuid4().hex[:10]
        db.force_sub.insert_one({
            "fs_id": fs_id,
            "channel_id": channel_id,
            "name": name,
            "url": url,
            "status": "on",
            "added_at": __import__("datetime").datetime.now().isoformat(),
        })
        return fs_id

    @staticmethod
    def delete(fs_id: str) -> bool:
        result = db.force_sub.delete_one({"fs_id": fs_id})
        return result.deleted_count > 0