"""
analytics.py — Funnel tahlil va hisobot
"""
from datetime import datetime

# Statistika: {vakansiya: {bosqich: count}}
funnel_data = {}
# Vaqt ma'lumotlari: {user_id: {bosqich: datetime}}
stage_times = {}

STAGE_ORDER = ["anketa", "test1", "training", "test2", "interview", "probation", "hired", "rejected"]

def track_stage(user_id: int, vacancy: str, stage: str):
    """Nomzod bosqichga o'tganda qayd etish"""
    # Funnel
    if vacancy not in funnel_data:
        funnel_data[vacancy] = {}
    funnel_data[vacancy][stage] = funnel_data[vacancy].get(stage, 0) + 1

    # Vaqt
    if user_id not in stage_times:
        stage_times[user_id] = {}
    stage_times[user_id][stage] = datetime.now()

def get_funnel_report(candidates: dict) -> str:
    """Umumiy funnel hisoboti"""
    if not candidates:
        return "📭 Hozircha ma'lumot yo'q."

    stage_counts = {}
    vacancy_counts = {}

    for uid, c in candidates.items():
        stage = c.get("bosqich", "anketa")
        vacancy = c.get("vacancy", "Noma'lum")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        vacancy_counts[vacancy] = vacancy_counts.get(vacancy, 0) + 1

    total = len(candidates)
    hired = stage_counts.get("hired", 0)
    rejected = stage_counts.get("rejected", 0)
    active = total - hired - rejected

    report = f"📊 *FUNNEL HISOBOTI*\n"
    report += f"{'─' * 25}\n"
    report += f"👥 Jami nomzodlar: {total}\n"
    report += f"✅ Qabul qilingan: {hired}\n"
    report += f"❌ Rad etilgan: {rejected}\n"
    report += f"⏳ Faol jarayonda: {active}\n\n"

    if total > 0 and hired > 0:
        conversion = round((hired / total) * 100, 1)
        report += f"📈 Konversiya: {conversion}%\n\n"

    report += f"*Bosqichlar bo'yicha:*\n"
    stage_names = {
        "anketa": "📝 Anketa",
        "test1": "📊 1-sinov",
        "training": "📚 O'qitish",
        "test2": "📊 2-sinov",
        "interview": "🎤 Suhbat",
        "probation": "⏳ Sinov muddati",
        "hired": "✅ Asosiy ish",
        "rejected": "❌ Rad etildi",
    }
    for key in STAGE_ORDER:
        count = stage_counts.get(key, 0)
        if count > 0:
            bar = "█" * min(count, 10)
            report += f"{stage_names.get(key, key)}: {count} {bar}\n"

    if vacancy_counts:
        report += f"\n*Vakansiyalar bo'yicha:*\n"
        for vac, count in sorted(vacancy_counts.items(), key=lambda x: -x[1]):
            report += f"• {vac}: {count} nomzod\n"

    return report

def get_avg_time_per_stage(user_id: int) -> str:
    """Nomzod har bosqichda qancha vaqt o'tkazgan"""
    times = stage_times.get(user_id, {})
    if len(times) < 2:
        return "Yetarli ma'lumot yo'q."

    text = "⏱ *Bosqichlardagi vaqt:*\n"
    stages = sorted(times.items(), key=lambda x: x[1])
    for i in range(len(stages) - 1):
        stage, start = stages[i]
        _, end = stages[i + 1]
        delta = end - start
        days = delta.days
        text += f"• {stage}: {days} kun\n"
    return text
