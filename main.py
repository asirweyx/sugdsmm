import logging
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types, F, exceptions
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from s import BOT_TOKEN1, ADMIN_ID1, ADMIN_USE1 
import aiohttp 
import json



BOT_TOKEN = BOT_TOKEN1
ADMIN_ID = ADMIN_ID1
ADMIN_USE = ADMIN_USE1

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.INFO)

# ===== N1Panel API (auto-order for Instagram подписчик гарантия) =====
N1_API_KEY = "14c60f4519412e01fd5d0e1359bfcd48"
N1_API_URL = "https://n1panel.com/api/v2"

class N1Api:
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_url = N1_API_URL

    async def _connect(self, data):
        data = dict(data)
        data['key'] = self.api_key
        async with aiohttp.ClientSession() as session:
            async with session.post(self.api_url, data=data) as resp:
                text = await resp.text()
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    try:
                        return json.loads(text)
                    except Exception:
                        return {'raw': text}

    async def order(self, service, link, quantity=None, **kwargs):
        payload = {'action': 'add', 'service': service, 'link': link}
        if quantity is not None:
            payload['quantity'] = quantity
        payload.update(kwargs)
        return await self._connect(payload)
    
def ensure_orders_external_column():
    conn = sqlite3.connect('bot.db')
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE orders ADD COLUMN external_id TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()



class Form(StatesGroup):
    waiting_for_service = State()
    waiting_for_tier = State()
    waiting_for_quantity = State()
    waiting_for_url = State()
    waiting_for_amount = State()
    waiting_for_receipt = State()

class AdminForm(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_topup_amount = State()
    waiting_for_deduct_user_id = State()
    waiting_for_deduct_amount = State()
    waiting_for_advert_text = State()  # yangi holat: reklama yuborish uchun

def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0,
        operations_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'Новичок'
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        platform TEXT,
        service_type TEXT,
        service_tier TEXT,
        quantity INTEGER,
        url TEXT,
        total_cost REAL DEFAULT 0,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        amount REAL,
        uses_left INTEGER
    )''')

    # ✅ YANGI QO‘SHILADIGAN JADVAL — TO‘G‘RI JOY AYNAN SHU!
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS promo_used (
        user_id INTEGER,
        code TEXT
    )
    ''')

    conn.commit()
    conn.close()

def user_used_promo(user_id, code):
    conn = sqlite3.connect('bot.db')
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM promo_used WHERE user_id = ? AND code = ?", (user_id, code))
    res = cur.fetchone()
    conn.close()
    return res is not None


def mark_promo_used(user_id, code):
    conn = sqlite3.connect('bot.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO promo_used (user_id, code) VALUES (?, ?)", (user_id, code))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id, username):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def ensure_user(user_id: int, username: str = None):
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 0)",
        (user_id, username),
    )
    conn.commit()
    conn.close()

def deduct_balance(user_id: int, amount: float) -> bool:
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False

    try:
        balance = float(row[0])
    except Exception:
        balance = 0.0

    if balance < amount:
        conn.close()
        return False

    new_balance = balance - amount
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, user_id))
    conn.commit()
    conn.close()
    return True

def update_operations_count(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 0)', (user_id, None))
    cursor.execute('UPDATE users SET operations_count = operations_count + 1 WHERE user_id = ?', (user_id,))

    cursor.execute('SELECT operations_count FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    count = row[0] if row else 0

    if count >= 500:
        status = 'Премиум'
    elif count >= 200:
        status = 'Постоянный'
    elif count >= 100:
        status = 'Продвинутый'
    elif count >= 10:
        status = 'Активный'
    else:
        status = 'Новичок'

    cursor.execute('UPDATE users SET status = ? WHERE user_id = ?', (status, user_id))
    conn.commit()
    conn.close()

def get_balance(user_id: int) -> float:
    ensure_user(user_id)
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    try:
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0

def update_balance(user_id: int, amount: float):
    ensure_user(user_id)
    conn = sqlite3.connect('bot.db')
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def get_operations_count(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT operations_count FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def create_order(user_id, platform, service_type, service_tier, quantity, url, total_cost):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO orders (user_id, platform, service_type, service_tier, quantity, url, total_cost) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (user_id, platform, service_type, service_tier, quantity, url, total_cost)
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def set_order_status(order_id, status):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = ? WHERE order_id = ?', (status, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT order_id, user_id, total_cost, status FROM orders WHERE order_id = ?', (order_id,))
    row = cursor.fetchone()
    conn.close()
    return row

PRICES = {
    'instagram': {
        'подписчик': {'👤подписчик бе гарантия': 11, '👤подписчик бо гарантия': 15},
        'лайкхо': {'Лайкҳо ❤️': 4.5, 'Лайкҳо (⚡️Зудкор)': 5.5},
        'просмотрхо': {'👀Прасмотр (⚡️Суръати тез)': 3},
        'просмотр сторис': {'сторис👀': 3},
        'Статистика (охват)': {'📈Охват': 3},
    },
    'tiktok': {
        'подписчик': {'👤Подписчик бо гарантия': 12, '👤Подписчик бо гарантия': 17},
        'лайкхо': {'Лайкҳо ❤️': 3},
        'просмотры': {'ТикТок просмотр👀': 3},
        'комментарии': {'📦коммент для ТикТок': 13},
        'ТикТок LIVE просмотр': {'просмотр LIVE 15 минут': 13, 'Просмотр LIVE 30минут': 25},
    },
    'telegram': {
        'подписчик': {'👤подписчик! 60 руз гарантия ': 13, 'подписчик зуд⚡️': 10,},
        'реаксияхо 👍👎': {'любой намуд реаксия👍👎😂🤣🥲😄😀😆': 4.5, 'Лайкҳо (⚡️Зудкор)': 5.5},
        'просмотрхо': {'👁️ Прасмотр (⚡️Суръати тез)': 1},
        'комментарияхо': {'📦коментарияхои зудкор⚡️': 20},
        '⭐️Телеграм премиум': {'1 моха⭐️': 36},
    },
}

def main_keyboard(user_id=None):
    builder = ReplyKeyboardBuilder()
    buttons = ["Накрутка", "Пополнение баланса", "Баланс", "ПРОМОКОД ","Профиль", "Помощь"]
    # admin uchun maxsus tugma
    if user_id == ADMIN_ID:
        buttons.append("🛠 Admin Panel")
    for button in buttons:
        builder.add(types.KeyboardButton(text=button))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def back_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Назад"))
    return builder.as_markup(resize_keyboard=True)

def platform_keyboard():
    builder = ReplyKeyboardBuilder()
    buttons = ["Instagram", "TikTok", "Telegram", "Назад"]
    for button in buttons:
        builder.add(types.KeyboardButton(text=button))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


CHANNEL_ID = "@sugdnakrutka"

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    

    create_user(user_id, username)

    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status not in ["member", "administrator", "creator"]:
            join_btn = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Ба канал аъзо шудан", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
                [InlineKeyboardButton(text="✅ Санҷиш", callback_data="check_subscribe")]
            ])
            await message.answer("🚫 Лутфан, аввал ба канали расмии мо обуна шавед!", reply_markup=join_btn)
            return
    except Exception:
        await message.answer("⚠ Ҳангоми санҷиши канал хатогӣ рух дод. Баъдтар кӯшиш кунед.")
        return

    await message.answer("Хуш омадед! Амалро интихоб кунед:", reply_markup=main_keyboard(user_id))


@dp.callback_query(F.data == "check_subscribe")
async def check_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    try:
        # Kanal a'zoligini tekshirish
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)

        if member.status in ["member", "administrator", "creator"]:
            # ✅ Agar obuna bo‘lgan bo‘lsa — menyuga o‘tkazish
            await callback.message.edit_text(
                "✅ Обуна тасдиқ шуд!\nБа меню хуш омадед:"
            )
            await callback.message.answer(
                "Амалиётро интихоб кунед:", reply_markup=main_keyboard(user_id)
            )
        else:
            # ❌ Agar obuna bo‘lmagan bo‘lsa
            await callback.answer(
                "🚫 Шумо ҳанӯз ба канал аъзо нашудаед!", show_alert=True
            )

    except Exception as e:
        # ⚠ Agar bot kanalni tekshira olmasa (masalan, admin emas yoki kanal yopiq)
        await callback.answer(
            "⚠ Хато: маълумоти каналро санҷида натавонистам.", show_alert=True
        )
@dp.message(F.text == "Назад")
async def universal_back(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    
    
    if current_state == Form.waiting_for_service:
        await message.answer("Платформа интихоб кунед:", reply_markup=platform_keyboard())
        await state.clear()




    elif current_state == Form.waiting_for_tier:
        data = await state.get_data()
        platform = data.get('platform')
        builder = ReplyKeyboardBuilder()
        for s in PRICES[platform].keys():
            builder.add(types.KeyboardButton(text=s))
        builder.add(types.KeyboardButton(text="Назад"))
        builder.adjust(2)
        await message.answer("Навъи хидматро интихоб кунед:", reply_markup=builder.as_markup(resize_keyboard=True))
        await state.set_state(Form.waiting_for_service)

    elif current_state == Form.waiting_for_quantity:
        data = await state.get_data()
        platform = data.get('platform')
        service = data.get('service')
        prices = PRICES.get(platform, {}).get(service, {})
        builder = ReplyKeyboardBuilder()
        for tier in prices.keys():
            builder.add(types.KeyboardButton(text=tier))
        builder.add(types.KeyboardButton(text="Назад"))
        builder.adjust(2)
        await message.answer("Тарифро интихоб кунед:", reply_markup=builder.as_markup(resize_keyboard=True))
        await state.set_state(Form.waiting_for_tier)

    elif current_state == Form.waiting_for_url:
        await message.answer("Миқдорро аз 500 то 100 000 ворид кунед:", reply_markup=back_keyboard())
        await state.set_state(Form.waiting_for_quantity)

    elif current_state == Form.waiting_for_amount:
        await message.answer("Менюи асосӣ:", reply_markup=main_keyboard(message.from_user.id))
        await state.clear()

    elif current_state == Form.waiting_for_receipt:
        await message.answer("Маблағи пуркуниро ворид кунед:", reply_markup=back_keyboard())
        await state.set_state(Form.waiting_for_amount)

    else:
        await state.clear()
        await message.answer("Менюи асосӣ:", reply_markup=main_keyboard(message.from_user.id))


@dp.message(F.text == "Накрутка")
async def cmd_boost(message: types.Message):
    await message.answer("Платформа интихоб кунед:", reply_markup=platform_keyboard())

@dp.message(F.text.in_(["Instagram", "TikTok", "Telegram"]))
async def process_platform(message: types.Message, state: FSMContext):
    platform = message.text.strip().lower()
    if platform not in PRICES:
        await message.answer("Ин платформа дастгирӣ намешавад.", reply_markup=platform_keyboard())
        return
    
    await state.update_data(platform=platform)
    builder = ReplyKeyboardBuilder()
    services = list(PRICES[platform].keys())
    for service in services:
        builder.add(types.KeyboardButton(text=service))
    builder.add(types.KeyboardButton(text="Назад"))
    builder.adjust(2)
    await state.set_state(Form.waiting_for_service)
    await message.answer("Навъи хидматро интихоб кунед:", reply_markup=builder.as_markup(resize_keyboard=True))

@dp.message(Form.waiting_for_service)
async def process_service(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await message.answer("Платформа интихоб кунед:", reply_markup=platform_keyboard())
        await state.set_state(None)
        return

    data = await state.get_data()
    platform = data.get('platform')
    if not platform:
        await message.answer("⚠ Хато: платформа интихоб нашудааст. Аз нав оғоз кунед.", reply_markup=platform_keyboard())
        await state.clear()
        return

    service = message.text.strip()
    if service not in PRICES[platform]:
        await message.answer("Лутфан, хидматро аз рӯйхат интихоб кунед:")
        return

    prices = PRICES[platform][service]
    builder = ReplyKeyboardBuilder()
    response = f"Нархҳо барои {service} (1000 адад):\n"
    for tier, price in prices.items():
        response += f"• {tier} — {price} сомонӣ\n"
        builder.add(types.KeyboardButton(text=tier))
    builder.add(types.KeyboardButton(text="Назад"))
    builder.adjust(2)
    await state.update_data(service=service)
    await message.answer(response, reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(Form.waiting_for_tier)

@dp.message(Form.waiting_for_tier)
async def process_tier(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        data = await state.get_data()
        platform = data.get('platform')
        if not platform:
            await state.clear()
            await message.answer("Платформа интихоб кунед:", reply_markup=platform_keyboard())
            return
        
        builder = ReplyKeyboardBuilder()
        services = list(PRICES[platform].keys())
        for s in services:
            builder.add(types.KeyboardButton(text=s))
        builder.add(types.KeyboardButton(text="Назад"))
        builder.adjust(2)
        await state.set_state(Form.waiting_for_service)
        await message.answer("Навъи хидматро интихоб кунед:", reply_markup=builder.as_markup(resize_keyboard=True))
        return

    data = await state.get_data()
    platform = data.get('platform')
    service = data.get('service')
    if not platform or not service:
        await message.answer("⚠ Ошибка: начните заказ заново.")
        await state.clear()
        return

    tier = message.text.strip()
    if tier not in PRICES[platform][service]:
        await message.answer("Пожалуйста, выберите тариф из списка:")
        return

    await state.update_data(tier=tier)
    await message.answer("Миқдорро аз 500 то 100 000 ворид кунед:", reply_markup=back_keyboard())
    await state.set_state(Form.waiting_for_quantity)

@dp.message(Form.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        data = await state.get_data()
        platform = data.get('platform')
        service = data.get('service')
        if not platform or not service:
            await state.clear()
            await message.answer("Платформа интихоб кунед:", reply_markup=platform_keyboard())
            return
        
        prices = PRICES.get(platform, {}).get(service, {})
        builder = ReplyKeyboardBuilder()
        for t in prices.keys():
            builder.add(types.KeyboardButton(text=t))
        builder.add(types.KeyboardButton(text="Назад"))
        builder.adjust(2)
        await state.set_state(Form.waiting_for_tier)
        await message.answer("Тарифро интихоб кунед:", reply_markup=builder.as_markup(resize_keyboard=True))
        return

    try:
        quantity = int(message.text.replace(" ", "").replace(",", ""))
        if quantity < 500 or quantity > 50000:
            await message.answer("Миқдор бояд аз 500 то 50000 бўлсин. Лутфан боз кўшиш қилинг:")
            return
    except ValueError:
        await message.answer("Лутфан, рақам ворид кунед:")
        return

    await state.update_data(quantity=quantity)
    await message.answer("ссылкаи саҳифаи худро ворид кунед:", reply_markup=back_keyboard())
    await state.set_state(Form.waiting_for_url)

@dp.message(Form.waiting_for_url)
async def process_url(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await state.set_state(Form.waiting_for_quantity)
        await message.answer("Миқдорро аз 500 то 50000 ворид кунед:", reply_markup=back_keyboard())
        return
    
    url = message.text.strip()
    data = await state.get_data()
    platform = data.get('platform')
    service = data.get('service')
    tier = data.get('tier')
    quantity = data.get('quantity')

    if not all([platform, service, tier, quantity]):
        await message.answer("⚠ Хатогӣ: баъзе маълумотҳо гум шудаанд. Лутфан, фармоишро аз нав оғоз кунед.", reply_markup=main_keyboard(message.from_user.id))
        await state.clear()
        return

    if platform == 'instagram' and 'instagram.com' not in url:
        await message.answer("Лутфан, ссылкаи дурусти Instagram ворид кунед:")
        return
    if platform == 'tiktok' and 'tiktok.com' not in url:
        await message.answer("Лутфан, ссылкаи дурусти TikTok ворид кунед:")
        return

    logging.info(f"Order calc: user={message.from_user.id} platform={platform} service={service} tier={tier} qty={quantity} url={url}")

    try:
        price_per_unit = float(PRICES[platform][service][tier])
        total_cost = price_per_unit * (float(quantity) / 1000.0)
        total_cost = round(total_cost, 2)
    except Exception as e:
        logging.exception("Ошибка при расчёте стоимости: %s", e)
        await message.answer("Хатогӣ ҳангоми ҳисоб кардани арзиш — ба админ муроҷиат кунед.", reply_markup=main_keyboard(message.from_user.id))
        await state.clear()
        return

    user_id = message.from_user.id
    balance = get_balance(user_id)
    logging.info(f"user balance={balance:.2f}, total_cost={total_cost:.2f}")

    if balance < total_cost:
        await message.answer(
            f"❌ Баланси шумо кофӣ нест.\nАрзиш: {total_cost:.2f} сомонӣ\nБаланси шумо: {balance:.2f} сомонӣ\n"
            "Балансро пур кунед ё миқдорро кам кунед.", reply_markup=main_keyboard(message.from_user.id)
        )
        await state.clear()
        return

    if not deduct_balance(user_id, total_cost):
        logging.error("Не удалось списать средства у пользователя %s (balance check passed).", user_id)
        await message.answer("Ҳангоми гирифтани маблағ хатогӣ рух дод. Ба админ муроҷиат кунед.", reply_markup=main_keyboard(message.from_user.id))
        await state.clear()
        return

    order_id = create_order(user_id, platform, service, tier, quantity, url, total_cost)
    update_operations_count(user_id)

 # --- Auto-send to N1Panel for specific Instagram подписчик гарантия (service 3479) ---
    try:
        # we have platform, service, tier, quantity, url, order_id, user_id available in scope
        try:
            plat = platform.lower() if platform else ''
            serv = service.lower() if service else ''
            tier_l = tier.lower() if tier else ''
        except Exception:
            plat = serv = tier_l = ''
        if plat == 'instagram' and serv == 'подписчик' and 'бо гарантия' in tier_l:
            api = N1Api(N1_API_KEY)
            try:
                resp = await api.order(service=3479, link=url, quantity=quantity)
            except Exception as e:
                resp = {'error': str(e)}
            # try to extract external order id
            ext_id = None
            if isinstance(resp, dict):
                ext_id = resp.get('order') or resp.get('id') or resp.get('data') or resp.get('result') or resp.get('0') or None
            # write external id to DB if found
            if ext_id:
                conn = sqlite3.connect('bot.db')
                cur = conn.cursor()
                cur.execute("UPDATE orders SET external_id = ? WHERE order_id = ?", (str(ext_id), order_id))
                conn.commit()
                conn.close()
                try:
                    await bot.send_message(ADMIN_ID, f"🌐 Order #{order_id} sent to N1Panel. External ID: {ext_id}")
                except Exception:
                    pass
            else:
                # notify admin about API response
                try:
                    await bot.send_message(ADMIN_ID, f"⚠️ N1Panel response for order #{order_id}: {resp}")
                except Exception:
                    pass
    except Exception:
        pass
    # --- end auto-send ---
    admin_message = (
        f"📦 Новый заказ #{order_id}!\n"
        f"👤 Пользователь: @{message.from_user.username or 'Без ника'}\n"
        f"🆔 ID: {user_id}\n"
        f"📱 Платформа: {platform}\n"
        f"🎯 Услуга: {service} ({tier})\n"
        f"🔢 Количество: {quantity}\n"
        f"💰 Стоимость: {total_cost:.2f} сомонӣ\n"
        f"🔗 Ссылка: {url}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data=f"confirm_order:{order_id}:{user_id}"),
        InlineKeyboardButton(text="❌ Отклонить заказ", callback_data=f"reject_order:{order_id}:{user_id}")
    ]])

    try:
        await bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)
    except Exception:
        logging.exception("Не удалось отправить сообщение администратору.")

    await message.answer("✅ Фармоиш қабул шуд! Маблағ аз баланс гирифта шуд, Дар хотир доред ❗️Акаунт набояд закрытие бошад", reply_markup=main_keyboard(message.from_user.id))
    await state.clear()

@dp.message(F.text == "Пополнение баланса")
async def cmd_topup(message: types.Message, state: FSMContext):
    # Karta tanlash uchun tugmalar
    buttons = [
        [InlineKeyboardButton(text="💳 DUSHANBE CITY", callback_data="pay_dushanbe")],
        [InlineKeyboardButton(text="🌍 VISA (международный)", callback_data="pay_visa")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="topup_back")]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        "💰 Бо кадом карта мехоҳед маблағ пур кунед?\n\n"
        "Картаро интихоб кунед.",
        reply_markup=kb
    )
@dp.callback_query(F.data == "pay_dushanbe")
async def pay_dushanbe(callback: types.CallbackQuery, state: FSMContext):
    text = (
        "🏦 <b>DUSHANBE CITY</b>\n\n"
        "💳 <b>Карта рақам:</b> <code>9762000157865352</code>\n\n"
        "⚠️ <b>Минимал сумма:</b> 3 сомонӣ\n\n"
        "💰 Чанд сум маблағ мехоҳед пур кунед?\n\n"
        "➡️ Рақамро ворид кунед.(фақат рақам):"
    )

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="topup_back")]
        ]
    )

    await callback.message.edit_text(text, reply_markup=back_kb)
    await state.set_state(Form.waiting_for_amount)
    await state.update_data(card="DUSHANBE CITY")
    await callback.answer()

@dp.callback_query(F.data == "pay_visa")
async def pay_visa(callback: types.CallbackQuery, state: FSMContext):
    text = (
        "🌍 <b>VISA (международный)</b>\n\n"
        "💳 <b>Карта рақам:</b> <code>4400430396394568</code>\n\n"
        "⚠️ <b>Минимал сумма:</b> 3 сомонӣ\n\n"
        "💰 Чанд сум маблағ мехоҳед пур кунед?\n\n"
        "➡️ Рақамро ворид кунед.(фақат рақам):"
    )

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="topup_back")]
        ]
    )

    await callback.message.edit_text(text, reply_markup=back_kb)
    await state.set_state(Form.waiting_for_amount)
    await state.update_data(card="VISA международный")
    await callback.answer()


@dp.callback_query(F.data == "topup_back")
async def topup_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Менюи асосӣ:", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()



@dp.message(Form.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await state.clear()
        await message.answer("Менюи асосӣ:", reply_markup=main_keyboard(message.from_user.id))
        return

    try:
        amount = float(message.text)
        if amount < 3:
            await message.answer("❌ Минимальная сумма — 3 сомонӣ Введите снова:")
            return
    except ValueError:
        await message.answer("❌ Рақами дурустро ворид кунед:")
        return

    await state.update_data(amount=amount)
    await message.answer(f"✅ Сумма {amount:.2f} сомонӣ кабул шуд.\nҲоло чекро фиристед (фото или документ):", reply_markup=back_keyboard())
    await state.set_state(Form.waiting_for_receipt)

@dp.message(Form.waiting_for_receipt, F.photo | F.document)
async def process_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username
    amount = data.get("amount", 0.0)

    admin_message = (
        f"💸 Новое пополнение!\n"
        f"👤 Пользователь: @{username or 'без ника'}\n"
        f"🆔 ID: {user_id}\n"
        f"💰 Сумма: {amount:.2f} сомонӣ"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_topup:{user_id}:{amount}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject_topup:{user_id}:{amount}")
        ]
    ])

    if message.photo:
        await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=admin_message, reply_markup=keyboard)
    elif message.document:
        await bot.send_document(ADMIN_ID, message.document.file_id, caption=admin_message, reply_markup=keyboard)
    else:
        await bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)

    await message.answer("✅ Чек барои санҷиш фиристода шуд. Лутфан, интизор шавед.", reply_markup=main_keyboard(message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("confirm_topup"), F.from_user.id == ADMIN_ID)
async def confirm_topup(callback: types.CallbackQuery):
    try:
        _, user_id_str, amount_str = callback.data.split(":")
        user_id = int(user_id_str)
        amount = float(amount_str)
    except Exception:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    update_balance(user_id, amount)
    try:
        await bot.send_message(user_id, f"✅ Баланси шумо пур карда шуд ба {amount:.2f} сомонӣ")
    except Exception:
        pass

    if callback.message and callback.message.caption is not None:
        await callback.message.edit_caption(callback.message.caption + "\n\n✅ Подтверждено", reply_markup=None)
    else:
        await callback.message.edit_text((callback.message.text or "") + "\n\n✅ Подтверждено", reply_markup=None)

    await callback.answer("Баланс пополнен ✅")

@dp.callback_query(F.data.startswith("reject_topup"), F.from_user.id == ADMIN_ID)
async def reject_topup(callback: types.CallbackQuery):
    try:
        _, user_id_str, amount_str = callback.data.split(":")
        user_id = int(user_id_str)
        amount = float(amount_str)
    except Exception:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        await bot.send_message(user_id, f"❌Пуркунии шумо ба маблағи {amount:.2f} сомонӣ рад карда шуд. Лутфан ба админ муроҷиат кунед.")
    except Exception:
        pass

    if callback.message and callback.message.caption is not None:
        await callback.message.edit_caption(callback.message.caption + "\n\n❌ Отклонено", reply_markup=None)
    else:
        await callback.message.edit_text((callback.message.text or "") + "\n\n❌ Отклонено", reply_markup=None)

    await callback.answer("Пополнение отклонено ❌")

@dp.callback_query(F.data.startswith("confirm_order"), F.from_user.id == ADMIN_ID)
async def confirm_order(callback: types.CallbackQuery):
    try:
        _, order_id_str, user_id_str = callback.data.split(":")
        order_id = int(order_id_str)
        user_id = int(user_id_str)
    except Exception:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    set_order_status(order_id, "confirmed")
    order = get_order(order_id)
    total_cost = order[2] if order else None

    try:
        await bot.send_message(user_id, f"✅ Закази шумо #{order_id} Иҷро шуд! Ташаккур барои истифодаи хизмат!.")
    except Exception:
        pass

    if callback.message and callback.message.text:
        await callback.message.edit_text((callback.message.text or "") + f"\n\n✅ Заказ #{order_id} подтверждён", reply_markup=None)
    elif callback.message and callback.message.caption is not None:
        await callback.message.edit_caption(callback.message.caption + f"\n\n✅ Заказ #{order_id} подтверждён", reply_markup=None)

    await callback.answer("Заказ подтверждён ✅")

@dp.callback_query(F.data.startswith("reject_order"), F.from_user.id == ADMIN_ID)
async def reject_order(callback: types.CallbackQuery):
    try:
        _, order_id_str, user_id_str = callback.data.split(":")
        order_id = int(order_id_str)
        user_id = int(user_id_str)
    except Exception:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    order = get_order(order_id)
    if order:
        total_cost = order[2]
        update_balance(user_id, total_cost)
        set_order_status(order_id, "rejected")
    else:
        total_cost = 0

    try:
        await bot.send_message(user_id, f"❌ Ваш заказ #{order_id} отклонён. Средства {total_cost:.2f} сомонӣ возвращены на баланс.")
    except Exception:
        pass

    if callback.message and callback.message.text:
        await callback.message.edit_text((callback.message.text or "") + f"\n\n❌ Заказ #{order_id} отклонён", reply_markup=None)
    elif callback.message and callback.message.caption is not None:
        await callback.message.edit_caption(callback.message.caption + f"\n\n❌ Заказ #{order_id} отклонён", reply_markup=None)

    await callback.answer("Заказ отклонён ❌")

@dp.message(F.from_user.id == ADMIN_ID, F.text == "/admin")
async def cmd_admin(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ручное пополнение", callback_data="manual_topup")],
        [InlineKeyboardButton(text="Списать с пользователя", callback_data="manual_deduct")]
    ])
    await message.answer("Панели маъмур:", reply_markup=kb)

@dp.callback_query(F.data == "manual_topup", F.from_user.id == ADMIN_ID)
async def manual_topup_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("ID-и истифодабарандаро ворид кунед для пополнения:")
    await state.set_state(AdminForm.waiting_for_user_id)
    await callback.answer()

@dp.message(AdminForm.waiting_for_user_id, F.from_user.id == ADMIN_ID)
async def process_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(user_id=user_id)
        await message.answer("Маблағи пуркуниро ворид кунед:")
        await state.set_state(AdminForm.waiting_for_topup_amount)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный ID (число):")

@dp.message(AdminForm.waiting_for_topup_amount, F.from_user.id == ADMIN_ID)
async def process_topup_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        data = await state.get_data()
        user_id = data['user_id']
        update_balance(user_id, amount)
        await message.answer(f"✅ Баланс пользователя {user_id} пополнен на {amount:.2f} сомонӣ")
        try:
            await bot.send_message(user_id, f"✅ Баланси шумо пур карда шуд ба {amount:.2f} сомонӣ администратором")
        except Exception:
            pass
        await state.clear()
    except ValueError:
        await message.answer("Пожалуйста, введите корректную сумму (число):")

@dp.callback_query(F.data == "manual_deduct", F.from_user.id == ADMIN_ID)
async def manual_deduct_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("ID-и истифодабарандаро ворид кунед, у которого хотите списать средства:")
    await state.set_state(AdminForm.waiting_for_deduct_user_id)
    await callback.answer()

@dp.message(AdminForm.waiting_for_deduct_user_id, F.from_user.id == ADMIN_ID)
async def manual_deduct_user(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(user_id=user_id)
        await message.answer("Маблағи гирифтани маблағро ворид кунед:")
        await state.set_state(AdminForm.waiting_for_deduct_amount)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный ID (число):")

@dp.message(AdminForm.waiting_for_deduct_amount, F.from_user.id == ADMIN_ID)
async def manual_deduct_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        data = await state.get_data()
        user_id = data['user_id']
        ok = deduct_balance(user_id, amount)
        if ok:
            await message.answer(f"✅ У пользователя {user_id} списано {amount:.2f} сомонӣ")
            try:
                await bot.send_message(user_id, f"❗ Аз бақияи шумо маблағ кам карда шуд {amount:.2f} сомонӣ (админ).")
            except Exception:
                pass
        else:
            await message.answer(f"❌ Не удалось списать: возможно, недостаточно средств у пользователя {user_id}")
        await state.clear()
    except ValueError:
        await message.answer("Пожалуйста, введите корректную сумму (число):")

@dp.message(F.text == "Баланс")
async def cmd_balance(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)

    if user:
        balance = user[2]
        operations = user[3]
        status = user[4]

        # ⚙️ Agar sizda referral daromad saqlanmasa, hozircha 0 qo‘yamiz
        referral_income = 0.00  

        text = (
            f"👤 <b>Профили шумо</b>\n\n"
            f"💰 <b>Баланс:</b> {balance:.2f} сомонӣ\n"
            f"🫂 <b>Реферал даромад:</b> {referral_income:.2f} сомонӣ\n"
            f"🔢 <b>Амалиётҳо:</b> {operations}\n"
            f"📊 <b>Статус:</b> {status}"
        )

        # Inline tugma — balansni to‘ldirish
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Баланс пуркунӣ", callback_data="go_topup")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="balance_back")]
            ]
        )

        await message.answer(text, reply_markup=kb)
    else:
        await message.answer("Профил ёфт нашуд. /start нависед то сабт шавед.")

@dp.callback_query(F.data == "go_topup")
async def go_topup(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await cmd_topup(callback.message, state)  # mavjud funksiyani chaqiramiz
    await callback.answer()

@dp.callback_query(F.data == "balance_back")
async def balance_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Менюи асосӣ:", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()


@dp.message(F.text == "Профиль")
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if user:
        response = (
            f"👤 Профили шумо\n"
            f"🆔 ID: {user_id}\n"
            f"📊 Статус: {user[4]}\n"
            f"💰 Баланс: {user[2]:.2f} сомонӣ\n"
            f"🔢 Операций: {user[3]}"
        )
        await message.answer(response)
    else:
        await message.answer("Профил ёфт нашуд. /start нависед то сабт шавед.")

@dp.message(F.text == "Реферал")
async def cmd_referral(message: types.Message):
    bot_username = (await bot.get_me()).username
    user_id = message.from_user.id
    ref_link = f"https://t.me/{bot_username}?start=user{user_id}"

    # Inline tugmalar
    share_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=ref_link),
            InlineKeyboardButton(text="🔙 Назад", callback_data="ref_back")
        ]
    ])

    text = (
        "🫂 Ссылка барои даъвати дустон\n\n"
        "👍 Бо ин ссылка дӯстони худро даъват кунед ва барои ҳар як дӯсти даъват кардаатон соҳиби 0,5 сомонӣ шавед!\n\n"
        "🔗 Барои копия кардан ба болои ссылка пахш кунед 👇\n"
        f"{ref_link}\n\n"
        "🌐 Ё ин ки тугмаи Поделиться-ро пахш кунед.\n\n"
        "❗️Шартҳои ҳатми!\n"
        "Дӯстоне ки шумо даъват мекунед то ба канали расмии мо обуна нашаванд ва тугмаи санҷишро пахш накунанд ба шумо маблағ намедиҳем!"
    )

    await message.answer(text, reply_markup=share_keyboard)
@dp.callback_query(F.data == "ref_back")
async def ref_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Менюи асосӣ:", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()

        
@dp.message(F.text == "Помощь")
async def cmd_help(message: types.Message):
    response = (
        "❓ Помощь\n\n"
        "Агар ба шумо кӯмак лозим бошад — бо админ тамос гиред.\n"
        f"Юз админ: {ADMIN_USE}"
    )
    await message.answer(response)

# ------------------ Qo'shilgan: Admin panel uchun tugma va reklama funksiyalari ------------------

@dp.message(F.text == "🛠 Admin Panel", F.from_user.id == ADMIN_ID)
async def open_admin_panel(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Foydalanuvchilar soni", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Balans boshqarish", callback_data="manual_topup")],
        [InlineKeyboardButton(text="❌ Balans yechish", callback_data="manual_deduct")],
        [InlineKeyboardButton(text="📢 Реклама юбориш", callback_data="send_advert")],
        [InlineKeyboardButton(text="🎁 Промокодлар", callback_data="promo_menu")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])
    await message.answer("🛠 Admin panel:", reply_markup=kb)

async def promo_admin_menu(callback: types.CallbackQuery):
    promos = list_promos()

    text = "🎁 Промокодлар:\n\n"
    if not promos:
        text += "Промокодлар йўқ.\n"
    else:
        for idx,(pid,code,amount,left) in enumerate(promos,1):
            text += f"{idx}. ID:{pid} | {code} | {amount} сом | {left} та қолган\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Промокод қўшиш", callback_data="promo_add")],
        [InlineKeyboardButton(text="🗑 Промокод ўчириш", callback_data="promo_delete")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])

    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == "admin_users", F.from_user.id == ADMIN_ID)
async def show_user_count(callback: types.CallbackQuery):
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    await callback.message.answer(f"👥 Botdagi foydalanuvchilar soni: {count}")
    await callback.answer()

@dp.callback_query(F.data == "admin_back", F.from_user.id == ADMIN_ID)
async def admin_back(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Менюи асосӣ:", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "send_advert", F.from_user.id == ADMIN_ID)
async def start_advert(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("✍️ Рекламани матнини ёзинг ё расм юборинг (бу хабар барчага юборилади).")
    await state.set_state(AdminForm.waiting_for_advert_text)
    await callback.answer()

@dp.message(AdminForm.waiting_for_advert_text, F.from_user.id == ADMIN_ID)
async def process_advert(message: types.Message, state: FSMContext):
    # Odatda katta foydalanuvchi bazasida bu ishni background job sifatida bajarish yaxshiroq,
    # ammo bu yerda soddalashtirilgan sync loop orqali yuboramiz.
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()

    sent = 0
    failed = 0

    await message.answer("📨 Реклама юборилаяпти... Илтимос кутиб туринг ⏳")

    for (user_id,) in users:
        try:
            if message.photo:
                # agar rasm bilan yuborilgan bo'lsa
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "")
            elif message.document:
                await bot.send_document(user_id, message.document.file_id, caption=message.caption or "")
            else:
                await bot.send_message(user_id, message.text or message.caption or "")
            sent += 1
            await asyncio.sleep(0.05)  # spam limiti uchun kichik kutish
        except exceptions.TelegramForbiddenError:
            # foydalanuvchi botni bloklagan yoki chiqib ketgan
            failed += 1
            continue
        except Exception:
            failed += 1
            continue

    await message.answer(f"✅ Реклама юборилди!\n\n📬 Юборилди: {sent}\n❌ Хато: {failed}")
    await state.clear()

# -----------------------------------------------------------------------------------------------


# ========================= PROMOKOD SYSTEM FULL ===============================

class PromoForm(StatesGroup):
    waiting_for_user_promo = State()

class AdminPromoForm(StatesGroup):
    waiting_for_new_code = State()
    waiting_for_new_amount = State()
    waiting_for_new_uses = State()
    waiting_for_delete_id = State()

def create_promo(code, amount, uses):
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO promo_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code, amount, uses))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid

def list_promos():
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("SELECT id, code, amount, uses_left FROM promo_codes ORDER BY id ASC")
    data = cur.fetchall()
    conn.close()
    return data

def delete_promo(pid):
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM promo_codes WHERE id=?", (pid,))
    ok = cur.rowcount
    conn.commit()
    conn.close()
    return ok > 0

def get_promo_by_code(code):
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("SELECT id, code, amount, uses_left FROM promo_codes WHERE code=?", (code,))
    row = cur.fetchone()
    conn.close()
    return row

def use_promo(pid):
    conn = sqlite3.connect("bot.db")
    cur = conn.cursor()
    cur.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE id=?", (pid,))
    conn.commit()
    cur.execute("SELECT uses_left FROM promo_codes WHERE id=?", (pid,))
    row = cur.fetchone()
    if row and row[0] <= 0:
        cur.execute("DELETE FROM promo_codes WHERE id=?", (pid,))
        conn.commit()
    conn.close()
    return row[0] if row else 0

@dp.message(F.text.in_(["ПРОМОКОД", "ПРОМОКОД"]))
async def promo_input(message: types.Message, state: FSMContext):
    await message.answer("🔑 Промокодро ворид кунед:", reply_markup=back_keyboard())
    await state.set_state(PromoForm.waiting_for_user_promo)

@dp.message(PromoForm.waiting_for_user_promo)
async def promo_apply(message: types.Message, state: FSMContext):
    code = message.text.strip()
    if user_used_promo(message.from_user.id, code):
        await message.answer("❗Шумо ин промокодро аллакай истифода бурдаед!")
        return
    if code == "Назад":
        await state.clear()
        await message.answer("Меню:", reply_markup=main_keyboard(message.from_user.id))
        return
    promo = get_promo_by_code(code)
    if not promo:
        await message.answer("❌ Промокоди нодуруст.")
        await state.clear()
        return
    pid, c, amount, left = promo
    if left <= 0:
        await message.answer("❌ Ин промокод тамом шудааст.")
        await state.clear()
        return
    update_balance(message.from_user.id, amount)
    remain = use_promo(pid)
    await message.answer(f"✅ Ба баланс +{amount} сомон илова шуд!")
    try:
        await bot.send_message(ADMIN_ID, f"🎁 Промокод {c} ишлатилди. Қолди: {remain}")
    except: pass
    await state.clear()


@dp.callback_query(F.data == "promo_menu", F.from_user.id == ADMIN_ID)
async def promo_admin_menu(callback: types.CallbackQuery):
    promos = list_promos()
    lines = ["🎁 Промокодлар:"]
    if not promos:
        lines.append("— Ҳеч қандай промокод йўқ.")
    else:
        for idx, (pid, code, amount, left) in enumerate(promos, start=1):
            lines.append(f"{idx}. ID:{pid} — {code} | {amount} сом | {left} та")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Қўшиш", callback_data="promo_add")],
        [InlineKeyboardButton(text="🗑 Ўчириш", callback_data="promo_delete")],
        [InlineKeyboardButton(text="🔙 Орқа", callback_data="admin_back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data=="promo_add", F.from_user.id==ADMIN_ID)
async def promo_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Кодни киритинг:")
    await state.set_state(AdminPromoForm.waiting_for_new_code)

@dp.message(AdminPromoForm.waiting_for_new_code)
async def promo_add_code(message: types.Message, state: FSMContext):
    await state.update_data(code=message.text.strip())
    await message.answer("Суммасини киритинг:")
    await state.set_state(AdminPromoForm.waiting_for_new_amount)

@dp.message(AdminPromoForm.waiting_for_new_amount)
async def promo_add_amount(message: types.Message, state: FSMContext):
    try: amount=float(message.text)
    except:
        await message.answer("Рақам киритинг.")
        return
    await state.update_data(amount=amount)
    await message.answer("Нечта фойдаланувчи ишлата олади?")
    await state.set_state(AdminPromoForm.waiting_for_new_uses)

@dp.message(AdminPromoForm.waiting_for_new_uses)
async def promo_add_uses(message: types.Message, state: FSMContext):
    try: uses=int(message.text)
    except:
        await message.answer("Рақам киритинг.")
        return
    data=await state.get_data()
    pid=create_promo(data['code'], data['amount'], uses)
    await message.answer("✅ Промокод қўшилди!")
    await bot.send_message(ADMIN_ID, f"✅ Промокод {data['code']} сақланди.")
    await state.clear()


@dp.callback_query(F.data=="promo_delete", F.from_user.id==ADMIN_ID)
async def promo_delete_menu(callback: types.CallbackQuery, state: FSMContext):
    promos = list_promos()
    if not promos:
        await callback.message.answer("Промokodлар йўқ.")
        return
    lines = ["🗑 Ўчириш учун ID киритинг:"]
    for pid, code, _, _ in promos:
        lines.append(f"ID:{pid} — {code}")
    text = "\n".join(lines)
    await callback.message.answer(text)
    await state.set_state(AdminPromoForm.waiting_for_delete_id)

@dp.message(AdminPromoForm.waiting_for_delete_id)
async def promo_delete_do(message: types.Message, state: FSMContext):
    try: pid=int(message.text)
    except:
        await message.answer("ID рақамини киритинг.")
        return
    if delete_promo(pid):
        await message.answer("✅ Ўчирилди.")
    else:
        await message.answer("❌ Бундай ID йўқ.")
    await state.clear()

# ========================================================================
async def main():
    init_db()
    logging.info("Bot started")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
   