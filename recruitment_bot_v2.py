"""
recruitment_bot_v2.py — HR 2.0 Recruitment Pipeline Bot
Yangiliklar: Rollar, Eslatmalar, Muloqot, Test banki, Onboarding, Funnel tahlil
"""
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackQueryHandler, ConversationHandler,
    JobQueue
)
import google.generativeai as genai

# Modullar
from roles import (
    get_role, is_super_admin, is_hr_manager, is_observer, is_staff,
    add_user, get_all_staff, remove_user, users_db, ROLES
)
from reminders import (
    set_reminder, get_overdue, mark_notified, clear_reminder,
    schedule_interview, get_interview
)
from onboarding import (
    init_onboarding, get_checklist, complete_task,
    get_progress, format_checklist
)
from analytics import track_stage, get_funnel_report, get_avg_time_per_stage

load_dotenv()

# ===== SOZLAMALAR =====
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SUPER_ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_CANDIDATES_DB = os.environ.get("NOTION_CANDIDATES_DB", "")
NOTION_VACANCIES_DB = os.environ.get("NOTION_VACANCIES_DB", "")
NOTION_TESTS_DB = os.environ.get("NOTION_TESTS_DB", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== BOSQICHLAR =====
STAGES = {
    "anketa": "📝 Anketa",
    "test1": "📊 1-sinov",
    "training": "📚 O'qitish",
    "test2": "📊 2-sinov",
    "interview": "🎤 Suhbat",
    "probation": "⏳ Sinov muddati",
    "hired": "✅ Asosiy ish",
    "rejected": "❌ Rad etildi"
}

# ===== HOLATLAR =====
(MENU, ANKETA_ISM, ANKETA_YOSH, ANKETA_TAJRIBA, ANKETA_SABAB,
 TEST_JAVOB, ADMIN_VACANCY_TITLE, ADMIN_VACANCY_DESC, ADMIN_VACANCY_DEADLINE,
 ADMIN_TEST_QUESTION, ADMIN_TEST_COUNT, TRAINING_ADD_TITLE,
 TRAINING_ADD_TYPE, TRAINING_ADD_CONTENT, MSG_TO_CANDIDATE,
 ADD_HR_ID, ADD_HR_NAME, SET_REMINDER_DAYS, INTERVIEW_SANA,
 INTERVIEW_VAQT, INTERVIEW_JOY, MINI_TEST_Q, MINI_TEST_COUNT) = range(23)

# ===== XOTIRA =====
active_tests = {}
candidates = {}
training_materials = {}
test_banks = {}         # {vacancy_name: [{"savol": "", "javob": ""}]}
messaging_target = {}   # {hr_id: candidate_id} — kim kimga yozmoqda


# ===== NOTION FUNKSIYALARI =====

def notion_post(endpoint, data):
    try:
        r = requests.post(f"https://api.notion.com/v1/{endpoint}", headers=NOTION_HEADERS, json=data)
        if r.status_code in [200, 201]:
            return r.json()
        return None
    except Exception as e:
        logger.error(f"Notion xato: {e}")
        return None

def notion_patch(page_id, data):
    try:
        r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=NOTION_HEADERS, json=data)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Notion patch xato: {e}")
        return False

def notion_query(db_id, filter_data=None):
    try:
        r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                         headers=NOTION_HEADERS, json=filter_data or {})
        return r.json().get("results", []) if r.status_code == 200 else []
    except Exception as e:
        logger.error(f"Notion query xato: {e}")
        return []

def notion_add_candidate(user_id, ism, yosh, tajriba, sabab, vacancy_name, ai_tahlil):
    data = {
        "parent": {"database_id": NOTION_CANDIDATES_DB},
        "properties": {
            "Ism": {"title": [{"text": {"content": ism}}]},
            "Telegram ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "Yosh": {"rich_text": [{"text": {"content": str(yosh)}}]},
            "Tajriba": {"rich_text": [{"text": {"content": tajriba}}]},
            "Ariza sababi": {"rich_text": [{"text": {"content": sabab[:1900]}}]},
            "Vakansiya": {"rich_text": [{"text": {"content": vacancy_name}}]},
            "Bosqich": {"select": {"name": "📝 Anketa"}},
            "AI Tahlil": {"rich_text": [{"text": {"content": ai_tahlil[:1900]}}]},
            "Sana": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
        }
    }
    result = notion_post("pages", data)
    return result.get("id") if result else None

def notion_update_stage(page_id, bosqich):
    return notion_patch(page_id, {"properties": {"Bosqich": {"select": {"name": bosqich}}}})

def notion_add_vacancy(title, desc, deadline, admin_id):
    data = {
        "parent": {"database_id": NOTION_VACANCIES_DB},
        "properties": {
            "Nomi": {"title": [{"text": {"content": title}}]},
            "Tavsif": {"rich_text": [{"text": {"content": desc[:1900]}}]},
            "Muddat": {"rich_text": [{"text": {"content": deadline}}]},
            "Holat": {"select": {"name": "Faol"}},
            "HR ID": {"rich_text": [{"text": {"content": str(admin_id)}}]},
            "Sana": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
        }
    }
    result = notion_post("pages", data)
    return result.get("id") if result else None

def notion_get_vacancies():
    results = notion_query(NOTION_VACANCIES_DB, {
        "filter": {"property": "Holat", "select": {"equals": "Faol"}}
    })
    vacancies = []
    for r in results:
        props = r.get("properties", {})
        title = props.get("Nomi", {}).get("title", [{}])
        name = title[0].get("text", {}).get("content", "Noma'lum") if title else "Noma'lum"
        vacancies.append({"id": r["id"], "name": name})
    return vacancies

def notion_add_test_result(user_id, ism, vacancy, ball, tahlil, bosqich_nomi):
    data = {
        "parent": {"database_id": NOTION_TESTS_DB},
        "properties": {
            "Ism": {"title": [{"text": {"content": ism}}]},
            "Telegram ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "Vakansiya": {"rich_text": [{"text": {"content": vacancy}}]},
            "Ball": {"rich_text": [{"text": {"content": str(ball)}}]},
            "Tahlil": {"rich_text": [{"text": {"content": tahlil[:1900]}}]},
            "Test turi": {"rich_text": [{"text": {"content": bosqich_nomi}}]},
            "Sana": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
        }
    }
    return notion_post("pages", data)


# ===== MENYULAR =====

def candidate_menu():
    keyboard = [
        [KeyboardButton("📋 Vakansiyalarni ko'rish")],
        [KeyboardButton("📊 Mening holatim"), KeyboardButton("📋 Onboarding")],
        [KeyboardButton("💬 HR ga xabar"), KeyboardButton("🤖 Savol berish")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def hr_menu():
    keyboard = [
        [KeyboardButton("➕ Vakansiya"), KeyboardButton("👥 Nomzodlar")],
        [KeyboardButton("📊 Funnel hisobot"), KeyboardButton("📋 Faol vakansiyalar")],
        [KeyboardButton("📝 Test banki"), KeyboardButton("📚 O'quv material")],
        [KeyboardButton("💬 Nomzodga xabar"), KeyboardButton("⏰ Eslatma belgilash")],
        [KeyboardButton("🎤 Suhbat rejalashtirish")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def super_admin_menu():
    keyboard = [
        [KeyboardButton("➕ Vakansiya"), KeyboardButton("👥 Nomzodlar")],
        [KeyboardButton("📊 Funnel hisobot"), KeyboardButton("📋 Faol vakansiyalar")],
        [KeyboardButton("📝 Test banki"), KeyboardButton("📚 O'quv material")],
        [KeyboardButton("💬 Nomzodga xabar"), KeyboardButton("⏰ Eslatma belgilash")],
        [KeyboardButton("🎤 Suhbat rejalashtirish"), KeyboardButton("👑 Xodimlar boshqaruvi")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_menu(user_id):
    if is_super_admin(user_id):
        return super_admin_menu()
    elif is_hr_manager(user_id):
        return hr_menu()
    else:
        return candidate_menu()


# ===== START =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Super adminlarni avtomatik ro'yxatdan o'tkazish
    if user_id in SUPER_ADMIN_IDS and not is_staff(user_id):
        add_user(user_id, user.first_name, "super_admin")

    role = get_role(user_id)

    if role == "super_admin":
        await update.message.reply_text(
            f"👑 Xush kelibsiz, {user.first_name}!\n\n"
            "*Super Admin paneli*\n"
            "Barcha funksiyalar sizga ochiq.",
            parse_mode="Markdown",
            reply_markup=super_admin_menu()
        )
    elif role == "hr_manager":
        await update.message.reply_text(
            f"🛠 Xush kelibsiz, {user.first_name}!\n\n"
            "*HR Menejer paneli*",
            parse_mode="Markdown",
            reply_markup=hr_menu()
        )
    elif role == "observer":
        await update.message.reply_text(
            f"👁 Xush kelibsiz, {user.first_name}!\n\n"
            "Siz kuzatuvchi sifatida kirgansiz.\n"
            "Hisobotlarni ko'rishingiz mumkin.",
            parse_mode="Markdown",
            reply_markup=hr_menu()
        )
    else:
        await update.message.reply_text(
            f"👋 Salom, {user.first_name}!\n\n"
            "🏢 *Recruitment botiga xush kelibsiz!*\n\n"
            "📋 Vakansiyalarni ko'ring\n"
            "📝 Anketa to'ldiring\n"
            "📊 Testlarni topshiring\n"
            "🎤 Suhbatga taklif oling\n\n"
            "Boshlash uchun *Vakansiyalarni ko'rish* tugmasini bosing!",
            parse_mode="Markdown",
            reply_markup=candidate_menu()
        )
    return MENU


# ===== VAKANSIYALAR =====

async def show_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vacancies = notion_get_vacancies()
    if not vacancies:
        await update.message.reply_text("😔 Hozircha faol vakansiyalar yo'q.")
        return MENU

    keyboard = [[InlineKeyboardButton(v["name"], callback_data=f"apply_{v['id']}_{v['name']}")] for v in vacancies]
    await update.message.reply_text(
        "📋 *Faol vakansiyalar:*\nBirini tanlang:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MENU


# ===== ANKETA =====

async def vacancy_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    context.user_data["vacancy_id"] = parts[1]
    context.user_data["vacancy_name"] = parts[2] if len(parts) > 2 else "Noma'lum"

    await query.message.reply_text(
        f"✅ *{context.user_data['vacancy_name']}* tanlandi!\n\n"
        "1️⃣ Ismingiz va familiyangizni kiriting:",
        parse_mode="Markdown"
    )
    return ANKETA_ISM

async def anketa_ism(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ism"] = update.message.text
    await update.message.reply_text("2️⃣ Yoshingizni kiriting:")
    return ANKETA_YOSH

async def anketa_yosh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["yosh"] = update.message.text
    await update.message.reply_text("3️⃣ Ish tajribangiz haqida yozing:")
    return ANKETA_TAJRIBA

async def anketa_tajriba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tajriba"] = update.message.text
    await update.message.reply_text("4️⃣ Nima uchun bu vakansiyaga ariza berdingiz?")
    return ANKETA_SABAB

async def anketa_sabab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["sabab"] = update.message.text
    await update.message.reply_text("⏳ AI tahlil qilinmoqda...")
    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    ism = context.user_data.get("ism", "")
    yosh = context.user_data.get("yosh", "")
    tajriba = context.user_data.get("tajriba", "")
    sabab = context.user_data.get("sabab", "")
    vacancy_name = context.user_data.get("vacancy_name", "")

    prompt = f"""Nomzod anketasini o'zbek tilida tahlil qil:
Ism: {ism}, Yosh: {yosh}, Tajriba: {tajriba}, Sabab: {sabab}, Vakansiya: {vacancy_name}

1. Umumiy baho (1-10)
2. Tajriba mosligі
3. Motivatsiya darajasi
4. Kuchli tomonlari
5. Xavf omillari
6. Tavsiya: Davom ettirish / Rad etish"""

    try:
        response = model.generate_content(prompt)
        ai_tahlil = response.text
    except Exception:
        ai_tahlil = "AI tahlil qila olmadi."

    page_id = notion_add_candidate(user.id, ism, yosh, tajriba, sabab, vacancy_name, ai_tahlil)
    candidates[user.id] = {"ism": ism, "bosqich": "anketa", "vacancy": vacancy_name, "page_id": page_id}
    track_stage(user.id, vacancy_name, "anketa")

    # HR larga xabar
    all_staff = get_all_staff()
    for admin_id, staff_data in all_staff.items():
        if staff_data["role"] in ["super_admin", "hr_manager"]:
            try:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Test yuborish", callback_data=f"send_test_{user.id}"),
                    InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_{user.id}_{page_id}")
                ]])
                await context.bot.send_message(
                    admin_id,
                    f"📝 *Yangi anketa!*\n\n👤 {ism} | 🎂 {yosh}\n💼 {vacancy_name}\n\n🤖 AI:\n{ai_tahlil[:500]}",
                    parse_mode="Markdown", reply_markup=keyboard
                )
            except Exception:
                pass

    await update.message.reply_text(
        f"✅ *Anketa qabul qilindi!*\n\n"
        f"👤 {ism}\n💼 {vacancy_name}\n\n"
        f"🤖 *AI Tahlil:*\n{ai_tahlil}\n\n"
        "HR menejer ko'rib chiqadi.",
        parse_mode="Markdown", reply_markup=candidate_menu()
    )
    return MENU


# ===== TEST BANKI =====

async def test_bank_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hr_manager(update.effective_user.id):
        return MENU

    vacancies = notion_get_vacancies()
    if not vacancies:
        await update.message.reply_text("❌ Avval vakansiya qo'shing.")
        return MENU

    keyboard = [[InlineKeyboardButton(v["name"], callback_data=f"testbank_{v['name']}")] for v in vacancies]
    await update.message.reply_text(
        "📝 *Test banki*\nQaysi vakansiya uchun?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MENU

async def test_bank_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vacancy = query.data.replace("testbank_", "")
    context.user_data["testbank_vacancy"] = vacancy

    existing = test_banks.get(vacancy, [])
    await query.message.reply_text(
        f"📝 *{vacancy}* uchun test banki\n"
        f"Hozir: {len(existing)} ta savol\n\n"
        "Nechta savol qo'shmoqchisiz?",
        parse_mode="Markdown"
    )
    return ADMIN_TEST_COUNT

async def admin_test_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        context.user_data["test_total"] = count
        context.user_data["test_questions"] = []
        await update.message.reply_text(
            f"✅ {count} ta savol qo'shiladi.\n\n"
            "1-savolni kiriting:\n_(Savol + variantlar + to'g'ri javob)_",
            parse_mode="Markdown"
        )
        return ADMIN_TEST_QUESTION
    except ValueError:
        await update.message.reply_text("⚠️ Faqat raqam kiriting.")
        return ADMIN_TEST_COUNT

async def admin_test_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    questions = context.user_data.get("test_questions", [])
    questions.append(update.message.text)
    context.user_data["test_questions"] = questions
    total = context.user_data.get("test_total", 5)
    current = len(questions)

    if current < total:
        await update.message.reply_text(f"✅ {current}-savol saqlandi.\n\n{current+1}-savolni kiriting:")
        return ADMIN_TEST_QUESTION
    else:
        vacancy = context.user_data.get("testbank_vacancy") or context.user_data.get("test_vacancy", "")
        candidate_id = context.user_data.get("test_for")

        if vacancy:
            if vacancy not in test_banks:
                test_banks[vacancy] = []
            for q in questions:
                test_banks[vacancy].append({"savol": q})

        if candidate_id:
            active_tests[candidate_id] = {
                "questions": questions, "answers": [],
                "current": 0,
                "vacancy": candidates.get(candidate_id, {}).get("vacancy", ""),
                "stage": "test1"
            }
            try:
                await context.bot.send_message(
                    candidate_id,
                    f"📊 *Test yuborildi!*\n\nJami {total} ta savol.\n/test_boshlash yozing!",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        await update.message.reply_text(
            f"✅ {total} ta savol saqlandi!" + (f" Nomzodga yuborildi!" if candidate_id else " Test bankiga qo'shildi!"),
            reply_markup=get_menu(update.effective_user.id)
        )
        return MENU


# ===== TEST TOPSHIRISH =====

async def send_test_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    candidate_id = int(query.data.split("_")[2])
    context.user_data["test_for"] = candidate_id

    vacancy = candidates.get(candidate_id, {}).get("vacancy", "")
    bank = test_banks.get(vacancy, [])

    if bank:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📚 Bank savollaridan", callback_data=f"test_from_bank_{candidate_id}"),
            InlineKeyboardButton("✍️ Yangi savollar", callback_data=f"test_new_{candidate_id}"),
        ]])
        await query.message.reply_text(
            f"*{vacancy}* uchun {len(bank)} ta savol mavjud.\nQaysi usulni tanlaysiz?",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        context.user_data["test_questions"] = []
        context.user_data["test_vacancy"] = vacancy
        await query.message.reply_text("Nechta savol bo'ladi?")
        return ADMIN_TEST_COUNT
    return MENU

async def test_from_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    candidate_id = int(query.data.split("_")[3])
    vacancy = candidates.get(candidate_id, {}).get("vacancy", "")
    bank = test_banks.get(vacancy, [])
    questions = [q["savol"] for q in bank]

    active_tests[candidate_id] = {
        "questions": questions, "answers": [],
        "current": 0, "vacancy": vacancy, "stage": "test1"
    }

    try:
        await context.bot.send_message(
            candidate_id,
            f"📊 *Test yuborildi!*\n\nJami {len(questions)} ta savol.\n/test_boshlash yozing!",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await query.edit_message_text(f"✅ {len(questions)} ta savol nomzodga yuborildi!")
    return MENU

async def test_boshlash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in active_tests:
        await update.message.reply_text("❌ Sizga hozircha test yuborilmagan.")
        return MENU

    test = active_tests[user_id]
    test["current"] = 0
    test["answers"] = []
    await update.message.reply_text(
        f"📊 *Test boshlanmoqda! {len(test['questions'])} ta savol.*\n\n1-savol:\n\n{test['questions'][0]}",
        parse_mode="Markdown"
    )
    return TEST_JAVOB

async def test_javob(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in active_tests:
        return MENU

    test = active_tests[user_id]
    test["answers"].append(update.message.text)
    test["current"] += 1

    if test["current"] < len(test["questions"]):
        await update.message.reply_text(
            f"{test['current']+1}-savol:\n\n{test['questions'][test['current']]}"
        )
        return TEST_JAVOB
    else:
        await update.message.reply_text("⏳ Javoblar tahlil qilinmoqda...")
        qa = "\n".join([f"{i+1}. {q}\n→ {a}" for i, (q, a) in enumerate(zip(test["questions"], test["answers"]))])

        try:
            response = model.generate_content(
                f"Test javoblarini o'zbek tilida baholang:\n{qa}\n\n"
                "1. Har javob baholash\n2. Umumiy ball (100dan)\n3. Tavsiya"
            )
            ai_tahlil = response.text
        except Exception:
            ai_tahlil = "AI baholay olmadi."

        candidate_info = candidates.get(user_id, {})
        notion_add_test_result(user_id, candidate_info.get("ism", ""), candidate_info.get("vacancy", ""), "AI", ai_tahlil, "Test")
        track_stage(user_id, candidate_info.get("vacancy", ""), test.get("stage", "test1"))

        for admin_id, staff_data in get_all_staff().items():
            if staff_data["role"] in ["super_admin", "hr_manager"]:
                try:
                    page_id = candidate_info.get("page_id")
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("📚 O'qitish", callback_data=f"stage_training_{user_id}_{page_id}"),
                        InlineKeyboardButton("🎤 Suhbat", callback_data=f"stage_interview_{user_id}_{page_id}"),
                        InlineKeyboardButton("❌ Rad", callback_data=f"reject_{user_id}_{page_id}")
                    ]])
                    await context.bot.send_message(
                        admin_id,
                        f"📊 *Test natijasi*\n\n👤 {candidate_info.get('ism','')}\n\n🤖 {ai_tahlil[:500]}",
                        parse_mode="Markdown", reply_markup=keyboard
                    )
                except Exception:
                    pass

        del active_tests[user_id]
        await update.message.reply_text(
            "✅ *Test yakunlandi!*\n\nJavoblaringiz yuborildi. Natija haqida xabar olasiz.",
            parse_mode="Markdown", reply_markup=candidate_menu()
        )
        return MENU


# ===== O'QITISH =====

async def training_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hr_manager(update.effective_user.id):
        return MENU
    vacancies = notion_get_vacancies()
    if not vacancies:
        await update.message.reply_text("❌ Avval vakansiya qo'shing.")
        return MENU
    keyboard = [[InlineKeyboardButton(v["name"], callback_data=f"train_vac_{v['name']}")] for v in vacancies]
    await update.message.reply_text("📚 Qaysi vakansiya uchun?", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def training_vac_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["train_vacancy"] = query.data.replace("train_vac_", "")
    await query.message.reply_text("📌 Material nomini kiriting:")
    return TRAINING_ADD_TITLE

async def training_add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["train_title"] = update.message.text
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video", callback_data="train_type_video")],
        [InlineKeyboardButton("📄 PDF", callback_data="train_type_pdf")],
        [InlineKeyboardButton("📝 Matn", callback_data="train_type_text")],
        [InlineKeyboardButton("🔗 Havola", callback_data="train_type_link")],
    ])
    await update.message.reply_text("📂 Material turini tanlang:", reply_markup=keyboard)
    return TRAINING_ADD_TYPE

async def training_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["train_type"] = query.data.replace("train_type_", "")
    msgs = {"video": "🎬 Videoni yuboring:", "pdf": "📄 PDFni yuboring:", "text": "📝 Matn kiriting:", "link": "🔗 Havolani kiriting:"}
    await query.message.reply_text(msgs.get(context.user_data["train_type"], "Kontent yuboring:"))
    return TRAINING_ADD_CONTENT

async def training_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mat_type = context.user_data.get("train_type", "text")
    vacancy = context.user_data.get("train_vacancy", "")
    title = context.user_data.get("train_title", "")
    material = {"title": title, "type": mat_type, "file_id": None, "content": ""}

    if mat_type == "video" and update.message.video:
        material["file_id"] = update.message.video.file_id
    elif mat_type == "pdf" and update.message.document:
        material["file_id"] = update.message.document.file_id
    elif mat_type in ["text", "link"]:
        material["content"] = update.message.text
    else:
        await update.message.reply_text("⚠️ To'g'ri formatda yuboring.")
        return TRAINING_ADD_CONTENT

    if vacancy not in training_materials:
        training_materials[vacancy] = []
    training_materials[vacancy].append(material)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Yana", callback_data=f"train_vac_{vacancy}"),
        InlineKeyboardButton("✅ Tayyor", callback_data=f"train_done_{vacancy}"),
    ]])
    await update.message.reply_text(
        f"✅ Material saqlandi! Jami: {len(training_materials[vacancy])} ta",
        reply_markup=keyboard
    )
    return MENU

async def training_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vacancy = query.data.replace("train_done_", "")
    count = len(training_materials.get(vacancy, []))
    await query.message.reply_text(
        f"✅ *{vacancy}* uchun {count} ta material tayyor!",
        parse_mode="Markdown", reply_markup=get_menu(update.effective_user.id)
    )
    return MENU

async def send_training_to_candidate(context, candidate_id: int):
    candidate = candidates.get(candidate_id, {})
    vacancy = candidate.get("vacancy", "")
    materials = training_materials.get(vacancy, [])

    if not materials:
        await context.bot.send_message(candidate_id, "📚 O'quv materiallar tez orada yuboriladi.")
        return

    await context.bot.send_message(
        candidate_id,
        f"📚 *O'qitish boshlandi!*\n\n{len(materials)} ta material tayyorlangan. 2-sinov shu asosida!",
        parse_mode="Markdown"
    )
    icons = {"video": "🎬", "pdf": "📄", "text": "📝", "link": "🔗"}
    for i, mat in enumerate(materials, 1):
        icon = icons.get(mat["type"], "📎")
        caption = f"{icon} *{i}-dars: {mat['title']}*"
        try:
            if mat["type"] == "video" and mat["file_id"]:
                await context.bot.send_video(candidate_id, video=mat["file_id"], caption=caption, parse_mode="Markdown")
            elif mat["type"] == "pdf" and mat["file_id"]:
                await context.bot.send_document(candidate_id, document=mat["file_id"], caption=caption, parse_mode="Markdown")
            else:
                await context.bot.send_message(candidate_id, f"{caption}\n\n{mat['content']}", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Material yuborish xato: {e}")

    await context.bot.send_message(candidate_id, "✅ Barcha materiallar yuborildi! O'rganing va 2-sinov uchun tayyor bo'ling. 💪", parse_mode="Markdown")


# ===== MULOQOT (HR ↔ NOMZOD) =====

async def msg_to_candidate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hr_manager(update.effective_user.id):
        return MENU
    if not candidates:
        await update.message.reply_text("📭 Hozircha nomzod yo'q.")
        return MENU
    keyboard = []
    for uid, c in candidates.items():
        stage = STAGES.get(c.get("bosqich", ""), "")
        keyboard.append([InlineKeyboardButton(f"{c['ism']} — {stage}", callback_data=f"msg_to_{uid}")])
    await update.message.reply_text("💬 Kimga xabar yozasiz?", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def msg_candidate_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    candidate_id = int(query.data.replace("msg_to_", ""))
    messaging_target[update.effective_user.id] = candidate_id
    candidate = candidates.get(candidate_id, {})
    await query.message.reply_text(
        f"💬 *{candidate.get('ism', '')}* ga xabar yozing:\n_(Matn, rasm yoki fayl yuborishingiz mumkin)_",
        parse_mode="Markdown"
    )
    return MSG_TO_CANDIDATE

async def msg_send_to_candidate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hr_id = update.effective_user.id
    candidate_id = messaging_target.get(hr_id)
    if not candidate_id:
        return MENU

    candidate = candidates.get(candidate_id, {})
    hr_name = users_db.get(hr_id, {}).get("ism", "HR Menejer")

    try:
        # Matn
        if update.message.text:
            await context.bot.send_message(
                candidate_id,
                f"💬 *{hr_name} (HR):*\n\n{update.message.text}",
                parse_mode="Markdown"
            )
        # Rasm
        elif update.message.photo:
            await context.bot.send_photo(
                candidate_id,
                photo=update.message.photo[-1].file_id,
                caption=f"📸 *{hr_name} (HR):* {update.message.caption or ''}",
                parse_mode="Markdown"
            )
        # Fayl
        elif update.message.document:
            await context.bot.send_document(
                candidate_id,
                document=update.message.document.file_id,
                caption=f"📎 *{hr_name} (HR):* {update.message.caption or ''}",
                parse_mode="Markdown"
            )

        await update.message.reply_text(
            f"✅ *{candidate.get('ism', '')}* ga xabar yuborildi!",
            parse_mode="Markdown",
            reply_markup=get_menu(hr_id)
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Xabar yuborilmadi: {e}")

    del messaging_target[hr_id]
    return MENU

async def candidate_reply_to_hr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nomzoddan HR ga javob"""
    user = update.effective_user
    candidate = candidates.get(user.id, {})
    if not candidate:
        return

    # Barcha HR larga yuborish
    for admin_id, staff_data in get_all_staff().items():
        if staff_data["role"] in ["super_admin", "hr_manager"]:
            try:
                if update.message.text:
                    await context.bot.send_message(
                        admin_id,
                        f"💬 *{candidate.get('ism', 'Nomzod')}* javob yozdi:\n\n{update.message.text}",
                        parse_mode="Markdown"
                    )
                elif update.message.photo:
                    await context.bot.send_photo(
                        admin_id,
                        photo=update.message.photo[-1].file_id,
                        caption=f"📸 *{candidate.get('ism', 'Nomzod')}:* {update.message.caption or ''}",
                        parse_mode="Markdown"
                    )
                elif update.message.document:
                    await context.bot.send_document(
                        admin_id,
                        document=update.message.document.file_id,
                        caption=f"📎 *{candidate.get('ism', 'Nomzod')}:* {update.message.caption or ''}",
                        parse_mode="Markdown"
                    )
            except Exception:
                pass


# ===== ESLATMA =====

async def set_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hr_manager(update.effective_user.id):
        return MENU
    if not candidates:
        await update.message.reply_text("📭 Hozircha nomzod yo'q.")
        return MENU
    keyboard = [[InlineKeyboardButton(f"{c['ism']}", callback_data=f"remind_{uid}")] for uid, c in candidates.items()]
    await update.message.reply_text("⏰ Kim uchun eslatma?", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def reminder_candidate_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["remind_candidate"] = int(query.data.replace("remind_", ""))
    await query.message.reply_text("Necha kundan keyin eslatma yuborilsin? (raqam kiriting):")
    return SET_REMINDER_DAYS

async def set_reminder_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())
        candidate_id = context.user_data.get("remind_candidate")
        candidate = candidates.get(candidate_id, {})
        set_reminder(candidate_id, candidate.get("bosqich", ""), days)
        await update.message.reply_text(
            f"✅ *{candidate.get('ism', '')}* uchun {days} kundan keyin eslatma o'rnatildi!",
            parse_mode="Markdown",
            reply_markup=get_menu(update.effective_user.id)
        )
    except ValueError:
        await update.message.reply_text("⚠️ Faqat raqam kiriting.")
        return SET_REMINDER_DAYS
    return MENU

async def check_reminders(context):
    """Har soatda eslatmalarni tekshirish"""
    overdue = get_overdue(candidates)
    for item in overdue:
        for admin_id, staff_data in get_all_staff().items():
            if staff_data["role"] in ["super_admin", "hr_manager"]:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"⏰ *Eslatma!*\n\n"
                        f"👤 {item['ism']} — *{item['days']} kun* javob bermadi!\n"
                        f"📍 Bosqich: {STAGES.get(item['stage'], item['stage'])}",
                        parse_mode="Markdown"
                    )
                    mark_notified(item["user_id"])
                except Exception:
                    pass


# ===== SUHBAT REJALASHTIRISH =====

async def interview_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hr_manager(update.effective_user.id):
        return MENU
    if not candidates:
        await update.message.reply_text("📭 Nomzod yo'q.")
        return MENU
    keyboard = [[InlineKeyboardButton(c["ism"], callback_data=f"interview_{uid}")] for uid, c in candidates.items()]
    await update.message.reply_text("🎤 Kim uchun suhbat rejalashtirasiz?", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def interview_candidate_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["interview_candidate"] = int(query.data.replace("interview_", ""))
    await query.message.reply_text("📅 Suhbat sanasini kiriting (masalan: 15.03.2025):")
    return INTERVIEW_SANA

async def interview_sana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["interview_sana"] = update.message.text
    await update.message.reply_text("🕐 Vaqtini kiriting (masalan: 14:00):")
    return INTERVIEW_VAQT

async def interview_vaqt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["interview_vaqt"] = update.message.text
    await update.message.reply_text("📍 Joyi/format kiriting (masalan: Ofis / Zoom):")
    return INTERVIEW_JOY

async def interview_joy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    candidate_id = context.user_data.get("interview_candidate")
    sana = context.user_data.get("interview_sana", "")
    vaqt = context.user_data.get("interview_vaqt", "")
    joy = update.message.text
    candidate = candidates.get(candidate_id, {})

    schedule_interview(candidate_id, sana, vaqt, joy)

    try:
        await context.bot.send_message(
            candidate_id,
            f"🎤 *Suhbatga taklif!*\n\n"
            f"📅 Sana: {sana}\n"
            f"🕐 Vaqt: {vaqt}\n"
            f"📍 Joy: {joy}\n\n"
            "Iltimos, tasdiqlab boring! HR bilan bog'laning.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ *{candidate.get('ism', '')}* ga suhbat xabari yuborildi!\n{sana} | {vaqt} | {joy}",
        parse_mode="Markdown",
        reply_markup=get_menu(update.effective_user.id)
    )
    return MENU


# ===== ONBOARDING =====

async def onboarding_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    candidate = candidates.get(user_id, {})

    if candidate.get("bosqich") not in ["probation", "hired"]:
        await update.message.reply_text("ℹ️ Onboarding faqat qabul qilingan xodimlar uchun.")
        return MENU

    if not get_checklist(user_id):
        init_onboarding(user_id)

    text = format_checklist(user_id)
    progress = get_progress(user_id)

    keyboard = []
    for task in get_checklist(user_id):
        if not task["done"]:
            keyboard.append([InlineKeyboardButton(f"✅ {task['task']}", callback_data=f"onboard_{user_id}_{task['id']}")])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )
    return MENU

async def onboarding_complete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    user_id = int(parts[1])
    task_id = int(parts[2])

    complete_task(user_id, task_id)
    progress = get_progress(user_id)

    await query.edit_message_text(
        f"✅ Vazifa bajarildi!\n\n"
        f"{format_checklist(user_id)}\n\n"
        f"📊 Progress: {progress['percent']}%",
        parse_mode="Markdown"
    )

    if progress["percent"] == 100:
        await context.bot.send_message(user_id, "🎉 *Onboarding yakunlandi! Tabriklaymiz!*", parse_mode="Markdown")


# ===== HR XODIMLAR BOSHQARUVI (Super Admin) =====

async def staff_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return MENU

    staff = get_all_staff()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ HR Menejer qo'shish", callback_data="add_hr_manager")],
        [InlineKeyboardButton("➕ Kuzatuvchi qo'shish", callback_data="add_observer")],
        [InlineKeyboardButton("👥 Xodimlar ro'yxati", callback_data="list_staff")],
    ])

    await update.message.reply_text(
        f"👑 *Xodimlar boshqaruvi*\n\nJami: {len(staff)} ta xodim",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return MENU

async def add_hr_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = "hr_manager" if "manager" in query.data else "observer"
    context.user_data["new_staff_role"] = role
    await query.message.reply_text(
        f"{'🛠 HR Menejer' if role == 'hr_manager' else '👁 Kuzatuvchi'} uchun:\n\nTelegram ID kiriting:"
    )
    return ADD_HR_ID

async def add_hr_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_id = int(update.message.text.strip())
        context.user_data["new_staff_id"] = new_id
        await update.message.reply_text("Ismini kiriting:")
        return ADD_HR_NAME
    except ValueError:
        await update.message.reply_text("⚠️ Faqat raqam kiriting.")
        return ADD_HR_ID

async def add_hr_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_id = context.user_data.get("new_staff_id")
    role = context.user_data.get("new_staff_role", "hr_manager")
    ism = update.message.text

    add_user(new_id, ism, role)

    try:
        await context.bot.send_message(
            new_id,
            f"✅ Siz *{ROLES.get(role, role)}* sifatida qo'shildingiz!\n/start yozing.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ *{ism}* — *{ROLES.get(role, role)}* qo'shildi!",
        parse_mode="Markdown",
        reply_markup=get_menu(update.effective_user.id)
    )
    return MENU


# ===== BOSQICH O'ZGARTIRISH =====

async def stage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    action = parts[1]
    candidate_id = int(parts[2])
    page_id = "_".join(parts[3:]) if len(parts) > 3 else None

    stage_messages = {
        "training": ("📚 O'qitish", "📚 *O'qitishga yo'naltirildinggiz!*"),
        "test2": ("📊 2-sinov", "📊 *2-sinov testi yuborildi!* /test_boshlash yozing."),
        "interview": ("🎤 Suhbat", "🎤 *Suhbatga taklif etildinggiz!* HR bog'lanadi."),
        "probation": ("⏳ Sinov muddati", "⏳ *Sinov muddatiga qabul qilindingiz!* 🎉"),
        "hired": ("✅ Asosiy ish", "✅ *Asosiy ishga qabul qilindingiz!* 🎉"),
        "rejected": ("❌ Rad etildi", "❌ *Ariza rad etildi.* Keyingi imkoniyatlarda omad!"),
    }

    if action in stage_messages:
        stage_name, msg = stage_messages[action]
        if page_id:
            notion_update_stage(page_id, stage_name)
        if candidate_id in candidates:
            candidates[candidate_id]["bosqich"] = action
            track_stage(candidate_id, candidates[candidate_id].get("vacancy", ""), action)

        try:
            await context.bot.send_message(candidate_id, msg, parse_mode="Markdown")
        except Exception:
            pass

        if action == "training":
            await send_training_to_candidate(context, candidate_id)

        if action in ["probation", "hired"]:
            init_onboarding(candidate_id)
            try:
                await context.bot.send_message(
                    candidate_id,
                    "📋 *Onboarding checklist tayyor!*\nBotda *'📋 Onboarding'* tugmasini bosing.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        clear_reminder(candidate_id)
        await query.edit_message_text(f"✅ Bosqich *{stage_name}* ga o'zgartirildi.", parse_mode="Markdown")


# ===== MENING HOLATIM =====

async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    candidate = candidates.get(user_id)

    if not candidate:
        await update.message.reply_text("ℹ️ Siz hali ariza topshirmagansiz.", reply_markup=candidate_menu())
        return MENU

    stage_key = candidate.get("bosqich", "anketa")
    pipeline = [("anketa","📝 Anketa"),("test1","📊 1-sinov"),("training","📚 O'qitish"),
                ("test2","📊 2-sinov"),("interview","🎤 Suhbat"),("probation","⏳ Sinov"),("hired","✅ Asosiy ish")]

    progress = ""
    reached = False
    for key, name in pipeline:
        if key == stage_key:
            progress += f"👉 {name} ← Siz bu yerdасиз\n"
            reached = True
        elif not reached:
            progress += f"✅ {name}\n"
        else:
            progress += f"⬜ {name}\n"

    interview = get_interview(user_id)
    interview_text = ""
    if interview:
        interview_text = f"\n🎤 *Suhbat:* {interview['sana']} | {interview['vaqt']} | {interview['joy']}"

    await update.message.reply_text(
        f"📊 *Holatingiz:*\n\n👤 {candidate.get('ism','')}\n💼 {candidate.get('vacancy','')}\n\n{progress}{interview_text}",
        parse_mode="Markdown", reply_markup=candidate_menu()
    )
    return MENU


# ===== FUNNEL HISOBOT =====

async def funnel_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_observer(update.effective_user.id):
        return MENU
    report = get_funnel_report(candidates)
    await update.message.reply_text(report, parse_mode="Markdown")
    return MENU


# ===== VAKANSIYA QO'SHISH =====

async def add_vacancy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hr_manager(update.effective_user.id):
        return MENU
    await update.message.reply_text("📌 Vakansiya nomini kiriting:")
    return ADMIN_VACANCY_TITLE

async def vacancy_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["vac_title"] = update.message.text
    await update.message.reply_text("📝 Tavsif va talablarni kiriting:")
    return ADMIN_VACANCY_DESC

async def vacancy_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["vac_desc"] = update.message.text
    await update.message.reply_text("📅 Ariza topshirish muddati (masalan: 01.03.2025):")
    return ADMIN_VACANCY_DEADLINE

async def vacancy_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    title = context.user_data.get("vac_title", "")
    desc = context.user_data.get("vac_desc", "")
    deadline = update.message.text
    page_id = notion_add_vacancy(title, desc, deadline, user.id)
    await update.message.reply_text(
        f"✅ *{title}* vakansiyasi qo'shildi!\n{'📋 Notion\'ga saqlandi!' if page_id else ''}",
        parse_mode="Markdown", reply_markup=get_menu(user.id)
    )
    return MENU


# ===== AI SUHBAT =====

async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        response = model.generate_content(
            f"Siz HR yordamchisisiz. O'zbek tilida javob bering.\nSavol: {update.message.text}"
        )
        await update.message.reply_text(response.text)
    except Exception:
        await update.message.reply_text("⚠️ Xato yuz berdi.")


# ===== XABAR ROUTERI =====

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message.text else ""
    user_id = update.effective_user.id

    # Nomzod tugmalari
    if text == "📋 Vakansiyalarni ko'rish":
        return await show_vacancies(update, context)
    elif text == "📊 Mening holatim":
        return await my_status(update, context)
    elif text == "📋 Onboarding":
        return await onboarding_menu(update, context)
    elif text == "💬 HR ga xabar":
        await update.message.reply_text("💬 Xabaringizni yozing yoki fayl yuboring:")
        context.user_data["candidate_replying"] = True
        return MENU
    elif text == "🤖 Savol berish":
        await update.message.reply_text("🤖 Savolingizni yozing:")
        return MENU

    # HR tugmalari
    elif text == "➕ Vakansiya" and is_hr_manager(user_id):
        return await add_vacancy_start(update, context)
    elif text == "📊 Funnel hisobot" and is_observer(user_id):
        return await funnel_report(update, context)
    elif text == "📋 Faol vakansiyalar" and is_observer(user_id):
        return await show_vacancies(update, context)
    elif text == "👥 Nomzodlar" and is_observer(user_id):
        if not candidates:
            await update.message.reply_text("📭 Hozircha nomzod yo'q.")
        else:
            msg = "👥 *Nomzodlar:*\n\n"
            for uid, c in candidates.items():
                stage = STAGES.get(c.get("bosqich", ""), "")
                msg += f"• {c['ism']} — {c['vacancy']} — {stage}\n"
            await update.message.reply_text(msg, parse_mode="Markdown")
        return MENU
    elif text == "📝 Test banki" and is_hr_manager(user_id):
        return await test_bank_start(update, context)
    elif text == "📚 O'quv material" and is_hr_manager(user_id):
        return await training_add_start(update, context)
    elif text == "💬 Nomzodga xabar" and is_hr_manager(user_id):
        return await msg_to_candidate_start(update, context)
    elif text == "⏰ Eslatma belgilash" and is_hr_manager(user_id):
        return await set_reminder_start(update, context)
    elif text == "🎤 Suhbat rejalashtirish" and is_hr_manager(user_id):
        return await interview_start(update, context)
    elif text == "👑 Xodimlar boshqaruvi" and is_super_admin(user_id):
        return await staff_management(update, context)

    # Nomzod HR ga javob yozayotgan bo'lsa
    elif context.user_data.get("candidate_replying") and not is_staff(user_id):
        await candidate_reply_to_hr(update, context)
        context.user_data["candidate_replying"] = False
        await update.message.reply_text("✅ Xabaringiz HR ga yuborildi!", reply_markup=candidate_menu())
        return MENU
    else:
        await ai_chat(update, context)
        return MENU

async def media_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rasm va fayl yuborishlari uchun"""
    user_id = update.effective_user.id
    if is_hr_manager(user_id) and user_id in messaging_target:
        return await msg_send_to_candidate(update, context)
    elif not is_staff(user_id) and context.user_data.get("candidate_replying"):
        await candidate_reply_to_hr(update, context)
        context.user_data["candidate_replying"] = False
        await update.message.reply_text("✅ Yuborildi!", reply_markup=candidate_menu())
    return MENU

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 Menyu:", reply_markup=get_menu(update.effective_user.id))
    return MENU


# ===== CALLBACK ROUTER =====

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("apply_"):
        return await vacancy_selected(update, context)
    elif data.startswith("train_vac_"):
        return await training_vac_selected(update, context)
    elif data.startswith("train_type_"):
        return await training_type_selected(update, context)
    elif data.startswith("train_done_"):
        return await training_done(update, context)
    elif data.startswith("send_test_"):
        return await send_test_callback(update, context)
    elif data.startswith("test_from_bank_"):
        return await test_from_bank(update, context)
    elif data.startswith("test_new_"):
        candidate_id = int(data.split("_")[2])
        context.user_data["test_for"] = candidate_id
        context.user_data["test_questions"] = []
        await query.message.reply_text("Nechta savol?")
        return ADMIN_TEST_COUNT
    elif data.startswith("testbank_"):
        return await test_bank_selected(update, context)
    elif data.startswith("stage_"):
        return await stage_callback(update, context)
    elif data.startswith("reject_"):
        parts = data.split("_")
        candidate_id = int(parts[1])
        page_id = "_".join(parts[2:]) if len(parts) > 2 else None
        query.data = f"stage_rejected_{candidate_id}_{page_id}"
        return await stage_callback(update, context)
    elif data.startswith("msg_to_"):
        return await msg_candidate_selected(update, context)
    elif data.startswith("remind_"):
        return await reminder_candidate_selected(update, context)
    elif data.startswith("interview_"):
        return await interview_candidate_selected(update, context)
    elif data.startswith("onboard_"):
        return await onboarding_complete_task(update, context)
    elif data in ["add_hr_manager", "add_observer"]:
        return await add_hr_start(update, context)
    elif data == "list_staff":
        staff = get_all_staff()
        if not staff:
            await query.answer("Xodim yo'q")
            return MENU
        msg = "👥 *Xodimlar:*\n\n"
        for uid, data_s in staff.items():
            msg += f"• {data_s['ism']} — {ROLES.get(data_s['role'], data_s['role'])}\n"
        await query.message.reply_text(msg, parse_mode="Markdown")
        return MENU


# ===== MAIN =====

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Har soatda eslatmalarni tekshirish
    job_queue = app.job_queue
    job_queue.run_repeating(check_reminders, interval=3600, first=60)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, message_router),
                MessageHandler(filters.PHOTO | filters.Document.ALL, media_router),
                CallbackQueryHandler(callback_router),
            ],
            ANKETA_ISM: [MessageHandler(filters.TEXT & ~filters.COMMAND, anketa_ism)],
            ANKETA_YOSH: [MessageHandler(filters.TEXT & ~filters.COMMAND, anketa_yosh)],
            ANKETA_TAJRIBA: [MessageHandler(filters.TEXT & ~filters.COMMAND, anketa_tajriba)],
            ANKETA_SABAB: [MessageHandler(filters.TEXT & ~filters.COMMAND, anketa_sabab)],
            TEST_JAVOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_javob)],
            ADMIN_VACANCY_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vacancy_title)],
            ADMIN_VACANCY_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, vacancy_desc)],
            ADMIN_VACANCY_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vacancy_deadline)],
            ADMIN_TEST_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_test_count)],
            ADMIN_TEST_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_test_question)],
            TRAINING_ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, training_add_title)],
            TRAINING_ADD_TYPE: [CallbackQueryHandler(callback_router)],
            TRAINING_ADD_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, training_add_content),
                MessageHandler(filters.VIDEO, training_add_content),
                MessageHandler(filters.Document.PDF, training_add_content),
            ],
            MSG_TO_CANDIDATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_send_to_candidate),
                MessageHandler(filters.PHOTO | filters.Document.ALL, msg_send_to_candidate),
            ],
            ADD_HR_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_hr_id)],
            ADD_HR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_hr_name)],
            SET_REMINDER_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_reminder_days)],
            INTERVIEW_SANA: [MessageHandler(filters.TEXT & ~filters.COMMAND, interview_sana)],
            INTERVIEW_VAQT: [MessageHandler(filters.TEXT & ~filters.COMMAND, interview_vaqt)],
            INTERVIEW_JOY: [MessageHandler(filters.TEXT & ~filters.COMMAND, interview_joy)],
        },
        fallbacks=[CommandHandler("menu", menu_command)],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("test_boshlash", test_boshlash))
    app.add_handler(CallbackQueryHandler(callback_router))

    logger.info("HR 2.0 Recruitment Bot v2 ishga tushdi! 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
