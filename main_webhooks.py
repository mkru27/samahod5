import os
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, List, Tuple
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Update

# ===================== CONFIG =====================
load_dotenv()

CUSTOMER_BOT_TOKEN    = os.getenv("CUSTOMER_BOT_TOKEN")
PRO_BOT_TOKEN         = os.getenv("PRO_BOT_TOKEN")
DISPATCHER_BOT_TOKEN  = os.getenv("DISPATCHER_BOT_TOKEN")
if not (CUSTOMER_BOT_TOKEN and PRO_BOT_TOKEN and DISPATCHER_BOT_TOKEN):
    raise RuntimeError("Set CUSTOMER_BOT_TOKEN, PRO_BOT_TOKEN, DISPATCHER_BOT_TOKEN")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+37529XXXXXXX")

BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # e.g., https://your-service.onrender.com
if not BASE_WEBHOOK_URL:
    raise RuntimeError("Set BASE_WEBHOOK_URL to your public Render URL (e.g. https://appname.onrender.com)")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # optional but recommended

# Bots
customer_bot   = Bot(CUSTOMER_BOT_TOKEN,   default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
pro_bot        = Bot(PRO_BOT_TOKEN,        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dispatcher_bot = Bot(DISPATCHER_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

# Dispatchers (раздельные FSM/хэндлеры)
dp_customer   = Dispatcher(storage=MemoryStorage())
dp_pro        = Dispatcher(storage=MemoryStorage())
dp_dispatcher = Dispatcher(storage=MemoryStorage())

r_customer   = Router()
r_pro        = Router()
r_dispatcher = Router()

dp_customer.include_router(r_customer)
dp_pro.include_router(r_pro)
dp_dispatcher.include_router(r_dispatcher)

# ===================== DATA MODELS =====================
CATEGORIES = [
    "Экскаватор", "Мини-экскаватор", "Погрузчик", "Мини-погрузчик",
    "Самосвал", "Манипулятор", "Автовышка", "Кран",
    "Бетонный насос",
    "Демонтажная бригада", "Кладочные работы", "Отделочные работы",
    "Сантехника", "Электрика", "Кровля", "Сварочные работы",
]

@dataclass
class Executor:
    user_id: int
    name: str = ""
    phone: str = ""
    categories: Set[str] = field(default_factory=set)
    status: str = "pending"  # pending|approved|blocked
    registered_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class Order:
    id: int
    customer_id: int
    customer_phone: str
    category: str
    description: str
    address: str
    date_str: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    likes: Set[int] = field(default_factory=set)  # executor user_ids

EXECUTORS: Dict[int, Executor] = {}  # key = executor user_id (ProBot user)
ORDERS: Dict[int, Order] = {}
_order_seq = 1

def next_order_id() -> int:
    global _order_seq
    i = _order_seq
    _order_seq += 1
    return i

# ===================== HELPERS =====================
def mention(uid: int, username: Optional[str], full_name: str) -> str:
    return f"@{username}" if username else f"[{full_name}](tg://user?id={uid})"

def valid_by_fmt375(phone: str) -> bool:
    p = (phone or "").replace(" ", "").replace("-", "")
    return p.startswith("+375") and len(p) == 13 and p[1:].isdigit()

async def notify_admins(text: str):
    for aid in ADMIN_IDS:
        try:
            await dispatcher_bot.send_message(aid, text)
        except Exception:
            pass

def chunk_buttons(items: List[str], prefix: str, per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    rows, row = [], []
    for title in items:
        row.append(InlineKeyboardButton(text=title, callback_data=f"{prefix}:{title}"))
        if len(row) == per_row:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows

# ===================== CUSTOMER BOT =====================
class CBNewOrder(StatesGroup):
    waiting_phone = State()
    waiting_category = State()
    waiting_description = State()
    waiting_address = State()
    waiting_date = State()

class CBCallback(StatesGroup):
    waiting_phone = State()

def cb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать заявку", callback_data="cb:new")],
        [InlineKeyboardButton(text="📞 Обратный звонок", callback_data="cb:call")],
        [InlineKeyboardButton(text="ℹ️ О нас", callback_data="cb:about")],
    ])

ABOUT_TEXT = (
    "Мы — диспетчерский центр строительных работ в Могилёве.\n\n"
    "• Помогаем быстро найти технику и бригады.\n"
    "• Подбираем исполнителей под вашу задачу.\n"
    "• Диспетчер связывается и сопровождает до старта работ.\n\n"
    f"Телефон диспетчера: *{SUPPORT_PHONE}*"
)

@r_customer.message(CommandStart())
async def cb_start(m: Message, state: FSMContext):
    await m.answer("Здравствуйте! Чем можем помочь?", reply_markup=cb_main_menu())

@r_customer.callback_query(F.data == "cb:about")
async def cb_about(c: CallbackQuery):
    await c.message.answer(ABOUT_TEXT)
    await c.answer()

@r_customer.callback_query(F.data == "cb:call")
async def cb_call(c: CallbackQuery, state: FSMContext):
    await state.set_state(CBCallback.waiting_phone)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cb:home")]])
    await c.message.answer("Оставьте номер *в формате* `+375XXXXXXXXX` — мы перезвоним.", reply_markup=kb)
    await c.answer()

@r_customer.message(CBCallback.waiting_phone)
async def cb_call_phone(m: Message, state: FSMContext):
    phone = (m.text or "").strip().replace(" ", "").replace("-", "")
    if not valid_by_fmt375(phone):
        await m.answer("Пожалуйста, укажите номер в формате `+375XXXXXXXXX` (ровно 9 цифр после +375).")
        return
    who = mention(m.from_user.id, m.from_user.username, m.from_user.full_name or "Пользователь")
    await notify_admins(f"📞 *Обратный звонок*\nОт: {who}\nТелефон: *{phone}*")
    await state.clear()
    await m.answer("Спасибо! Передали диспетчеру — скоро свяжемся.", reply_markup=cb_main_menu())

@r_customer.callback_query(F.data == "cb:home")
async def cb_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("Главное меню:", reply_markup=cb_main_menu())
    await c.answer()

# ---- Создать заявку ----
def cb_dates_menu() -> InlineKeyboardMarkup:
    today = datetime.now()
    d0, d1, d2 = today, today + timedelta(days=1), today + timedelta(days=2)
    rows = [
        [InlineKeyboardButton(text=f"Сегодня ({d0.strftime('%d.%m')})", callback_data=f"cbdate:{d0.strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(text=f"Завтра ({d1.strftime('%d.%m')})", callback_data=f"cbdate:{d1.strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(text=f"Послезавтра ({d2.strftime('%d.%m')})", callback_data=f"cbdate:{d2.strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(text="📅 В течение недели", callback_data="cbdate:week")],
        [InlineKeyboardButton(text="Отмена", callback_data="cb:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cb_dates_week() -> InlineKeyboardMarkup:
    base = datetime.now()
    rows = []
    for i in range(0, 7):
        d = base + timedelta(days=i)
        rows.append([InlineKeyboardButton(text=d.strftime("%a %d.%m"), callback_data=f"cbdate:{d.strftime('%Y-%m-%d')}")])
    rows.append([InlineKeyboardButton(text="◀︎ Назад", callback_data="cbdate:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@r_customer.callback_query(F.data == "cb:new")
async def cb_new(c: CallbackQuery, state: FSMContext):
    await state.set_state(CBNewOrder.waiting_phone)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cb:home")]])
    await c.message.answer("Для связи укажите ваш номер `+375XXXXXXXXX`:", reply_markup=kb)
    await c.answer()

@r_customer.message(CBNewOrder.waiting_phone)
async def cb_new_phone(m: Message, state: FSMContext):
    phone = (m.text or "").strip().replace(" ", "").replace("-", "")
    if not valid_by_fmt375(phone):
        await m.answer("Формат: `+375XXXXXXXXX` (ровно 9 цифр после +375).")
        return
    await state.update_data(customer_phone=phone)
    await state.set_state(CBNewOrder.waiting_category)
    rows = chunk_buttons(CATEGORIES, "cbcat", per_row=2)
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cb:home")])
    await m.answer("Выберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@r_customer.callback_query(F.data.startswith("cbcat:"), CBNewOrder.waiting_category)
async def cb_new_cat(c: CallbackQuery, state: FSMContext):
    cat = c.data.split(":", 1)[1]
    await state.update_data(category=cat)
    await state.set_state(CBNewOrder.waiting_description)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cb:home")]])
    await c.message.answer("Коротко опишите, что нужно (объём, особенности):", reply_markup=kb)
    await c.answer()

@r_customer.message(CBNewOrder.waiting_description)
async def cb_new_descr(m: Message, state: FSMContext):
    await state.update_data(description=(m.text or "").strip())
    await state.set_state(CBNewOrder.waiting_address)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cb:home")]])
    await m.answer("Адрес (улица, дом; ориентиры по желанию):", reply_markup=kb)

@r_customer.message(CBNewOrder.waiting_address)
async def cb_new_addr(m: Message, state: FSMContext):
    await state.update_data(address=(m.text or "").strip())
    await state.set_state(CBNewOrder.waiting_date)
    await m.answer("Когда нужно начать работы?", reply_markup=cb_dates_menu())

@r_customer.callback_query(CBNewOrder.waiting_date, F.data.startswith("cbdate:"))
async def cb_new_date(c: CallbackQuery, state: FSMContext):
    val = c.data.split(":", 1)[1]
    if val == "week":
        await c.message.edit_text("Выберите день в течение недели:", reply_markup=cb_dates_week()); await c.answer(); return
    if val == "back":
        await c.message.edit_text("Когда нужно начать работы?", reply_markup=cb_dates_menu()); await c.answer(); return

    data = await state.get_data()
    phone = data["customer_phone"]; category = data["category"]
    description = data["description"]; address = data["address"]
    date_str = datetime.strptime(val, "%Y-%m-%d").strftime("%d.%m.%Y")

    order_id = next_order_id()
    ORDERS[order_id] = Order(
        id=order_id, customer_id=c.from_user.id, customer_phone=phone,
        category=category, description=description, address=address, date_str=date_str
    )

    await c.message.answer(
        f"✅ Заявка *#{order_id}* создана.\n\n"
        f"*Категория:* {category}\n"
        f"*Описание:* {description}\n"
        f"*Адрес:* {address}\n"
        f"*Дата:* {date_str}\n\n"
        "Мы отправили заявку подходящим исполнителям. Диспетчер свяжется с вами.",
        reply_markup=cb_main_menu()
    )
    await state.clear(); await c.answer()

    # разослать в ProBot по категории
    await send_order_to_executors(order_id)

# ===================== PRO BOT (исполнители) =====================
class ProReg(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    picking_categories = State()
    confirm = State()

def pro_main_menu(ex: Optional[Executor]) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(text="📋 Мои категории", callback_data="pro:cats")])
    rows.append([InlineKeyboardButton(text="📞 Мой телефон", callback_data="pro:phone")])
    rows.append([InlineKeyboardButton(text="ℹ️ Помощь", callback_data="pro:help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cats_keyboard(selected: Set[str]) -> InlineKeyboardMarkup:
    rows = []
    for c in CATEGORIES:
        mark = "✅ " if c in selected else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{c}", callback_data=f"procat:{c}")])
        rows.append([InlineKeyboardButton(text="Готово", callback_data="pro:cats_ok")]) if False else None
    rows.append([InlineKeyboardButton(text="Готово", callback_data="pro:cats_ok")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@r_pro.message(CommandStart())
async def pro_start(m: Message, state: FSMContext):
    # deep-link: /start exec
    payload = (m.text or "").split(maxsplit=1)
    if len(payload) > 1 and payload[1].strip().lower() == "exec":
        # регистрация
        await state.set_state(ProReg.waiting_name)
        await m.answer("Регистрация исполнителя.\nВведите ваше имя / компанию:")
        return

    ex = EXECUTORS.get(m.from_user.id)
    status = ex.status if ex else "не зарегистрирован"
    await m.answer(f"Привет! Статус: *{status}*.\nМеню ниже.", reply_markup=pro_main_menu(ex))

@r_pro.message(ProReg.waiting_name)
async def pro_reg_name(m: Message, state: FSMContext):
    await state.update_data(name=(m.text or "").strip())
    await state.set_state(ProReg.waiting_phone)
    await m.answer("Укажите телефон в формате `+375XXXXXXXXX`:")

@r_pro.message(ProReg.waiting_phone)
async def pro_reg_phone(m: Message, state: FSMContext):
    phone = (m.text or "").strip().replace(" ", "").replace("-", "")
    if not valid_by_fmt375(phone):
        await m.answer("Формат: `+375XXXXXXXXX` (ровно 9 цифр после +375)."); return
    await state.update_data(phone=phone, selected=set())
    await state.set_state(ProReg.picking_categories)
    await m.answer("Выберите ваши категории (можно несколько), затем нажмите *Готово*:",
                   reply_markup=cats_keyboard(set()))

@r_pro.callback_query(F.data.startswith("procat:"), ProReg.picking_categories)
async def pro_pick_cat(c: CallbackQuery, state: FSMContext):
    cat = c.data.split(":", 1)[1]
    data = await state.get_data()
    selected: Set[str] = set(data.get("selected") or set())
    if cat in selected: selected.remove(cat)
    else: selected.add(cat)
    await state.update_data(selected=list(selected))
    await c.message.edit_reply_markup(reply_markup=cats_keyboard(selected))
    await c.answer()

@r_pro.callback_query(F.data == "pro:cats_ok", ProReg.picking_categories)
async def pro_cats_ok(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    name = data["name"]; phone = data["phone"]; selected = set(data.get("selected") or [])
    if not selected:
        await c.answer("Выберите хотя бы одну категорию", show_alert=True); return
    EXECUTORS[c.from_user.id] = Executor(
        user_id=c.from_user.id, name=name, phone=phone, categories=selected, status="pending"
    )
    await state.clear()
    await c.message.answer("Спасибо! Заявка на регистрацию отправлена диспетчеру. Ожидайте одобрения.")
    who = mention(c.from_user.id, c.from_user.username, c.from_user.full_name or name)
    await notify_admins(
        "🆕 *Новая регистрация исполнителя*\n"
        f"{who}\nТелефон: *{phone}*\nКатегории: {', '.join(sorted(selected))}\n"
        f"Одобрить: /exec_approve {c.from_user.id}\nЗаблокировать: /exec_block {c.from_user.id}"
    )
    await c.answer()

@r_pro.callback_query(F.data == "pro:cats")
async def pro_show_cats(c: CallbackQuery):
    ex = EXECUTORS.get(c.from_user.id)
    if not ex:
        await c.message.answer("Вы ещё не зарегистрированы. Нажмите Start по диплинку регистрации.")
        await c.answer(); return
    await c.message.answer("Ваши категории:\n" + ("\n".join(sorted(ex.categories)) or "не выбраны"))
    await c.answer()

@r_pro.callback_query(F.data == "pro:phone")
async def pro_show_phone(c: CallbackQuery):
    ex = EXECUTORS.get(c.from_user.id)
    if not ex:
        await c.message.answer("Вы ещё не зарегистрированы."); await c.answer(); return
    await c.message.answer(f"Ваш номер: *{ex.phone}*")
    await c.answer()

@r_pro.callback_query(F.data == "pro:help")
async def pro_help(c: CallbackQuery):
    await c.message.answer("Здесь будут приходить подходящие вам заявки. Нажимайте 👍 если готовы взять задачу.")
    await c.answer()

# ---- Получение заданий в личку и реакция 👍/👎 ----
def order_card_text(o: Order) -> str:
    return (
        f"📥 *Заявка #{o.id}*\n"
        f"Категория: *{o.category}*\n"
        f"Дата: *{o.date_str}*\n"
        f"Адрес: {o.address}\n"
        f"Описание: {o.description}\n\n"
        "Готовы взяться?"
    )

def order_card_kb(o: Order) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Беру", callback_data=f"take:{o.id}"),
        InlineKeyboardButton(text="👎 Пропустить", callback_data=f"skip:{o.id}")
    ]])

@r_pro.callback_query(F.data.startswith("take:"))
async def pro_take(c: CallbackQuery):
    try:
        oid = int(c.data.split(":", 1)[1])
    except Exception:
        await c.answer("Ошибка", show_alert=True); return
    o = ORDERS.get(oid)
    if not o:
        await c.answer("Заявка недоступна", show_alert=True); return

    ex = EXECUTORS.get(c.from_user.id)
    if not ex or ex.status != "approved":
        await c.answer("Доступ только для одобренных исполнителей", show_alert=True); return

    o.likes.add(c.from_user.id)
    who = mention(c.from_user.id, c.from_user.username, c.from_user.full_name or ex.name)
    await notify_admins(
        f"✅ *Отклик (LIKE)* по заявке #{o.id} [{o.category}]\n"
        f"Исполнитель: {who}\nТел.: *{ex.phone}*\n"
        f"Клиент: *{o.customer_phone}*\nАдрес: {o.address}\nДата: {o.date_str}\nОписание: {o.description}"
    )
    await c.answer("Принято! Диспетчер с вами свяжется.")
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@r_pro.callback_query(F.data.startswith("skip:"))
async def pro_skip(c: CallbackQuery):
    await c.answer("Пропущено")
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# ===================== DISPATCHER BOT (админы) =====================
@r_dispatcher.message(CommandStart())
async def d_start(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Нет доступа."); return
    await m.answer("Панель диспетчера.\nКоманды:\n"
                   "/exec_list — список исполнителей\n"
                   "/exec_approve <id>\n/exec_block <id>\n/exec_info <id>")

@r_dispatcher.message(Command("exec_list"))
async def d_exec_list(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    pending = [e for e in EXECUTORS.values() if e.status == "pending"]
    approved = [e for e in EXECUTORS.values() if e.status == "approved"]
    blocked = [e for e in EXECUTORS.values() if e.status == "blocked"]
    def fmt(lst: List[Executor], title: str) -> str:
        if not lst: return f"{title}: —"
        return f"{title} ({len(lst)}):\n" + "\n".join([f"• {e.user_id} {e.name} {e.phone}" for e in lst])
    await m.answer("\n\n".join([fmt(pending, "Ожидают"), fmt(approved, "Одобренные"), fmt(blocked, "Заблокированные")]))

@r_dispatcher.message(Command("exec_approve"))
async def d_exec_approve(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Используйте: /exec_approve <user_id>"); return
    uid = int(parts[1]); ex = EXECUTORS.get(uid)
    if not ex: await m.answer("Исполнитель не найден"); return
    ex.status = "approved"
    await m.answer(f"Одобрен: {uid} {ex.name} {ex.phone}")

@r_dispatcher.message(Command("exec_block"))
async def d_exec_block(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Используйте: /exec_block <user_id>"); return
    uid = int(parts[1]); ex = EXECUTORS.get(uid)
    if not ex: await m.answer("Исполнитель не найден"); return
    ex.status = "blocked"
    await m.answer(f"Заблокирован: {uid} {ex.name} {ex.phone}")

@r_dispatcher.message(Command("exec_info"))
async def d_exec_info(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Используйте: /exec_info <user_id>"); return
    uid = int(parts[1]); ex = EXECUTORS.get(uid)
    if not ex: await m.answer("Исполнитель не найден"); return
    await m.answer(
        f"*{ex.name}* (id {ex.user_id})\nТел.: *{ex.phone}*\n"
        f"Категории: {', '.join(sorted(ex.categories)) or '—'}\nСтатус: {ex.status}"
    )

# ===================== GLUE: SEND ORDER TO EXECUTORS =====================
async def send_order_to_executors(order_id: int):
    o = ORDERS.get(order_id)
    if not o: return
    # Находим одобренных исполнителей с подходящей категорией
    targets = [e for e in EXECUTORS.values() if e.status == "approved" and o.category in e.categories]
    if not targets:
        await notify_admins(
            f"⚠️ Нет одобренных исполнителей по категории [{o.category}] для заявки #{o.id}.\n"
            f"Клиент: *{o.customer_phone}* — позвоните вручную."
        )
        return
    text = order_card_text(o)
    kb = order_card_kb(o)
    sent = 0
    for ex in targets:
        try:
            await pro_bot.send_message(ex.user_id, text, reply_markup=kb)
            sent += 1
        except Exception:
            # пользователь не нажал Start у ProBot — пропускаем
            pass
    if sent == 0:
        await notify_admins(
            f"⚠️ Ни одному исполнителю не доставлено в личку (никто не нажал Start у ProBot) по заявке #{o.id} [{o.category}]."
        )

# ===================== FASTAPI APP + WEBHOOKS =====================
app = FastAPI()

def check_secret(request: Request):
    if not WEBHOOK_SECRET:
        return
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if got != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Bad secret token")

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.post("/tg/customer")
async def tg_customer(request: Request):
    check_secret(request)
    data = await request.json()
    update = Update.model_validate(data)
    await dp_customer.feed_update(customer_bot, update)
    return JSONResponse({"ok": True})

@app.post("/tg/pro")
async def tg_pro(request: Request):
    check_secret(request)
    data = await request.json()
    update = Update.model_validate(data)
    await dp_pro.feed_update(pro_bot, update)
    return JSONResponse({"ok": True})

@app.post("/tg/dispatcher")
async def tg_dispatcher(request: Request):
    check_secret(request)
    data = await request.json()
    update = Update.model_validate(data)
    await dp_dispatcher.feed_update(dispatcher_bot, update)
    return JSONResponse({"ok": True})

async def setup_webhooks():
    # Remove existing, then set new webhooks for each bot
    await customer_bot.delete_webhook(drop_pending_updates=True)
    await pro_bot.delete_webhook(drop_pending_updates=True)
    await dispatcher_bot.delete_webhook(drop_pending_updates=True)

    await customer_bot.set_webhook(f"{BASE_WEBHOOK_URL}/tg/customer", secret_token=WEBHOOK_SECRET or None)
    await pro_bot.set_webhook(f"{BASE_WEBHOOK_URL}/tg/pro", secret_token=WEBHOOK_SECRET or None)
    await dispatcher_bot.set_webhook(f"{BASE_WEBHOOK_URL}/tg/dispatcher", secret_token=WEBHOOK_SECRET or None)

@app.on_event("startup")
async def on_startup():
    await setup_webhooks()

# Optional: manual reset endpoint (protect via secret in query)
@app.post("/setup")
async def manual_setup(request: Request):
    key = request.query_params.get("key", "")
    if WEBHOOK_SECRET and key != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    await setup_webhooks()
    return PlainTextResponse("webhooks set")
