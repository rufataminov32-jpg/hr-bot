"""
roles.py — Foydalanuvchi rollari va huquqlari
Rollar: super_admin, hr_manager, observer
"""

# Rollar
ROLES = {
    "super_admin": "👑 Super Admin",
    "hr_manager": "🛠 HR Menejer",
    "observer": "👁 Kuzatuvchi",
}

# Foydalanuvchilar: {user_id: {"role": "...", "ism": "..."}}
users_db = {}

def get_role(user_id: int) -> str:
    return users_db.get(user_id, {}).get("role", "candidate")

def is_super_admin(user_id: int) -> bool:
    return get_role(user_id) == "super_admin"

def is_hr_manager(user_id: int) -> bool:
    return get_role(user_id) in ["super_admin", "hr_manager"]

def is_observer(user_id: int) -> bool:
    return get_role(user_id) in ["super_admin", "hr_manager", "observer"]

def is_staff(user_id: int) -> bool:
    return get_role(user_id) in ["super_admin", "hr_manager", "observer"]

def add_user(user_id: int, ism: str, role: str):
    users_db[user_id] = {"ism": ism, "role": role}

def get_all_staff():
    return {uid: data for uid, data in users_db.items() if data["role"] != "candidate"}

def remove_user(user_id: int):
    if user_id in users_db:
        del users_db[user_id]
