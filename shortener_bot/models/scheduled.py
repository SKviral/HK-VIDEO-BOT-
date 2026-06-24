from datetime import datetime
from typing import List, Dict, Any, Optional
import uuid
from shortener_bot.models.database import db


class ScheduledPostModel:
    @staticmethod
    def create(
        admin_id: int,
        media_type: str,
        media_id: str,
        d_link: str,
        s_link: str,
        category_id: str = "",
        scheduled_at: str = None,
    ) -> str:
        sched_id = uuid.uuid4().hex[:10]
        doc = {
            "sched_id": sched_id,
            "admin_id": str(admin_id),
            "media_type": media_type,
            "media_id": media_id,
            "d_link": d_link,
            "s_link": s_link,
            "category_id": category_id,
            "scheduled_at": scheduled_at or datetime.now().isoformat(),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        db.scheduled_posts.insert_one(doc)
        return sched_id

    @staticmethod
    def get_pending() -> List[Dict[str, Any]]:
        now = datetime.now().isoformat()
        return list(db.scheduled_posts.find({
            "status": "pending",
            "scheduled_at": {"$lte": now}
        }).sort("scheduled_at", 1))

    @staticmethod
    def get_all(limit: int = 50) -> List[Dict[str, Any]]:
        return list(db.scheduled_posts.find().sort("scheduled_at", -1).limit(limit))

    @staticmethod
    def get_by_id(sched_id: str) -> Optional[Dict[str, Any]]:
        return db.scheduled_posts.find_one({"sched_id": sched_id})

    @staticmethod
    def mark_done(sched_id: str) -> bool:
        result = db.scheduled_posts.update_one(
            {"sched_id": sched_id},
            {"$set": {"status": "done", "posted_at": datetime.now().isoformat()}}
        )
        return result.modified_count > 0

    @staticmethod
    def mark_error(sched_id: str, error: str) -> bool:
        result = db.scheduled_posts.update_one(
            {"sched_id": sched_id},
            {"$set": {"status": "error", "error": error}}
        )
        return result.modified_count > 0

    @staticmethod
    def delete(sched_id: str) -> bool:
        result = db.scheduled_posts.delete_one({"sched_id": sched_id})
        return result.deleted_count > 0

    @staticmethod
    def count_pending() -> int:
        now = datetime.now().isoformat()
        return db.scheduled_posts.count_documents({
            "status": "pending",
            "scheduled_at": {"$lte": now}
        })