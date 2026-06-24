from typing import List, Dict, Any, Optional
from shortener_bot.models.database import db


class FileModel:
    @staticmethod
    def save(
        file_key: str,
        batch_id: str,
        uploader: int,
        file_id: str,
        file_type: str,
        log_chat_id: int = None,
        log_msg_id: int = None,
    ) -> None:
        doc = {
            "file_key": file_key,
            "batch_id": batch_id,
            "uploader": str(uploader),
            "file_id": file_id,
            "type": file_type,
            "uploaded_at": __import__("datetime").datetime.now().isoformat(),
        }
        if log_chat_id and log_msg_id:
            doc["log_chat_id"] = log_chat_id
            doc["log_msg_id"] = log_msg_id
        db.files.insert_one(doc)

    @staticmethod
    def get_by_key(file_key: str) -> List[Dict[str, Any]]:
        return list(db.files.find({"$or": [{"file_key": file_key}, {"batch_id": file_key}]}))

    @staticmethod
    def get_by_batch(batch_id: str) -> List[Dict[str, Any]]:
        return list(db.files.find({"batch_id": batch_id}))

    @staticmethod
    def count_by_batch(batch_id: str) -> int:
        return db.files.count_documents({"batch_id": batch_id})

    @staticmethod
    def count_by_key(file_key: str) -> int:
        return db.files.count_documents({"$or": [{"file_key": file_key}, {"batch_id": file_key}]})

    @staticmethod
    def search(query: str, limit: int = 50) -> List[Dict[str, Any]]:
        return list(db.files.find({
            "$or": [
                {"file_key": {"$regex": query, "$options": "i"}},
                {"batch_id": {"$regex": query, "$options": "i"}},
            ]
        }).limit(limit))

    @staticmethod
    def get_recent(limit: int = 50) -> List[Dict[str, Any]]:
        return list(db.files.find().sort("uploaded_at", -1).limit(limit))