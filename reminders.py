"""
reminders.py — Eslatmalar va deadline boshqaruvi
"""
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Eslatmalar: {user_id: {"deadline": datetime, "stage": "", "notified": False}}
reminders = {}

# Suhbat jadval: {user_id: {"sana": "...", "vaqt": "...", "joy": "..."}}
interviews_scheduled = {}

def set_reminder(user_id: int, stage: str, days: int):
    """Nomzod uchun deadline belgilash"""
    deadline = datetime.now() + timedelta(days=days)
    reminders[user_id] = {
        "deadline": deadline,
        "stage": stage,
        "notified": False,
        "days": days
    }

def get_overdue(candidates: dict) -> list:
    """Muddati o'tgan nomzodlarni qaytaradi"""
    overdue = []
    now = datetime.now()
    for user_id, reminder in reminders.items():
        if not reminder["notified"] and now > reminder["deadline"]:
            candidate = candidates.get(user_id, {})
            overdue.append({
                "user_id": user_id,
                "ism": candidate.get("ism", "Noma'lum"),
                "stage": reminder["stage"],
                "days": reminder["days"]
            })
    return overdue

def mark_notified(user_id: int):
    if user_id in reminders:
        reminders[user_id]["notified"] = True

def clear_reminder(user_id: int):
    if user_id in reminders:
        del reminders[user_id]

def schedule_interview(user_id: int, sana: str, vaqt: str, joy: str):
    interviews_scheduled[user_id] = {
        "sana": sana,
        "vaqt": vaqt,
        "joy": joy,
        "created": datetime.now().strftime("%Y-%m-%d")
    }

def get_interview(user_id: int) -> dict:
    return interviews_scheduled.get(user_id, {})

def get_all_reminders() -> dict:
    return reminders
