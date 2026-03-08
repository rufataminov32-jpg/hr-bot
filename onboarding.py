"""
onboarding.py — Onboarding checklist boshqaruvi
"""

# Default checklist (HR o'zgartirishi mumkin)
DEFAULT_CHECKLIST = [
    {"id": 1, "task": "Shartnoma imzolash", "done": False},
    {"id": 2, "task": "Hujjatlarni topshirish (pasport, diplom)", "done": False},
    {"id": 3, "task": "Ish joyi bilan tanishish", "done": False},
    {"id": 4, "task": "Kompaniya qoidalarini o'qish", "done": False},
    {"id": 5, "task": "IT tizimlariga kirish olish", "done": False},
    {"id": 6, "task": "Jamoa bilan tanishish", "done": False},
    {"id": 7, "task": "Birinchi vazifani olish", "done": False},
    {"id": 8, "task": "Mentor bilan uchrashish", "done": False},
]

# Har xodim uchun checklist: {user_id: [{"id": ..., "task": ..., "done": ...}]}
onboarding_data = {}

def init_onboarding(user_id: int, custom_checklist: list = None):
    """Yangi xodim uchun checklist yaratish"""
    import copy
    checklist = copy.deepcopy(custom_checklist or DEFAULT_CHECKLIST)
    onboarding_data[user_id] = checklist

def get_checklist(user_id: int) -> list:
    return onboarding_data.get(user_id, [])

def complete_task(user_id: int, task_id: int) -> bool:
    checklist = onboarding_data.get(user_id, [])
    for task in checklist:
        if task["id"] == task_id:
            task["done"] = True
            return True
    return False

def get_progress(user_id: int) -> dict:
    checklist = onboarding_data.get(user_id, [])
    if not checklist:
        return {"total": 0, "done": 0, "percent": 0}
    total = len(checklist)
    done = sum(1 for t in checklist if t["done"])
    percent = int((done / total) * 100)
    return {"total": total, "done": done, "percent": percent}

def format_checklist(user_id: int) -> str:
    checklist = onboarding_data.get(user_id, [])
    if not checklist:
        return "📭 Checklist topilmadi."
    progress = get_progress(user_id)
    text = f"📋 *Onboarding Checklist*\n"
    text += f"✅ {progress['done']}/{progress['total']} ({progress['percent']}%)\n\n"
    for task in checklist:
        icon = "✅" if task["done"] else "⬜"
        text += f"{icon} {task['id']}. {task['task']}\n"
    return text
