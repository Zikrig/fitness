import re

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from urllib.parse import urlparse, parse_qs
from typing import Dict
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = Router()
# db и bot будут установлены из main.py
db = None
bot_instance = None


def set_database(database_instance):
    """Установить экземпляр базы данных"""
    global db
    db = database_instance
    logger.info("Экземпляр базы данных передан в handlers")


def set_bot(bot):
    """Установить экземпляр бота"""
    global bot_instance
    bot_instance = bot


def is_valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", slug))


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Основное меню"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Тренироваться", callback_data="start_questionnaire")],
            [InlineKeyboardButton(text="Сотрудничество", callback_data="cooperation")],
            [InlineKeyboardButton(text="Промокод", callback_data="enter_promo_code")],
            [InlineKeyboardButton(text="Примеры", callback_data="examples")],
        ]
    )

# Состояния для анкеты
class QuestionnaireStates(StatesGroup):
    waiting_gender = State()
    waiting_age = State()
    waiting_weight = State()
    waiting_workouts = State()
    waiting_diet = State()
    waiting_problem = State()

# Состояния для промокода
class PromoCodeStates(StatesGroup):
    waiting_promo_code = State()

# Состояния для админ-панели
class AdminStates(StatesGroup):
    managing_promo_codes = State()
    adding_promo_code = State()
    adding_promo_description = State()
    adding_promo_type = State()
    editing_promo_code = State()
    editing_promo_field = State()
    deleting_promo_code = State()
    managing_links = State()
    adding_link_slug = State()
    adding_link_description = State()
    editing_link_slug = State()
    editing_link_description = State()


def get_admin_ids():
    """Получить список ID админов"""
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    return [int(id.strip()) for id in admin_ids_str.split(",") if id.strip()]


def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь админом"""
    return user_id in get_admin_ids()


def parse_start_payload(message: Message):
    """Парсинг payload команды /start"""
    utm_source = utm_medium = utm_campaign = None
    start_link_slug = None
    if message.text and len(message.text.split()) > 1:
        args = message.text.split()[1]
        if args.startswith('?'):
            args = args[1:]
        parsed = {}
        if '=' in args:
            try:
                parsed = parse_qs(args)
            except Exception as e:
                logger.warning(f"Не удалось распарсить payload {args}: {e}")
                parsed = {}
        if parsed:
            start_param = parsed.get('start', [None])[0]
            if start_param:
                start_link_slug = start_param.lower()
            utm_source = parsed.get('utm_source', [None])[0]
            utm_medium = parsed.get('utm_medium', [None])[0]
            utm_campaign = parsed.get('utm_campaign', [None])[0]
        else:
            start_link_slug = args.lower()
    return utm_source, utm_medium, utm_campaign, start_link_slug


def build_questionnaire_text(questionnaire: Dict) -> str:
    text = "📋 Новая анкета:\n\n"
    username = questionnaire.get('username')
    name = questionnaire.get('first_name') or 'Не указано'
    text += f"👤 Пользователь: {name}"
    if username:
        text += f" (@{username})"
    text += f"\nID: {questionnaire.get('user_id')}\n\n"

    if questionnaire.get('gender'):
        text += f"Пол: {questionnaire['gender']}\n"
    if questionnaire.get('age'):
        text += f"Возраст: {questionnaire['age']}\n"
    if questionnaire.get('weight'):
        text += f"Вес: {questionnaire['weight']} кг\n"
    if questionnaire.get('workouts_per_week'):
        text += f"Тренировок в неделю: {questionnaire['workouts_per_week']}\n"
    if questionnaire.get('diet'):
        text += f"Рацион: {questionnaire['diet']}\n"
    if questionnaire.get('problem_or_injury'):
        text += f"Проблемы/травмы: {questionnaire['problem_or_injury']}\n"

    promo_codes = questionnaire.get('promo_codes', [])
    if promo_codes and promo_codes[0]:
        text += f"\nПромокоды: {', '.join([pc for pc in promo_codes if pc])}\n"

    created_at = questionnaire.get('created_at')
    if created_at:
        created_dt = None
        if isinstance(created_at, datetime):
            created_dt = created_at
        elif isinstance(created_at, str):
            try:
                created_dt = datetime.fromisoformat(created_at)
            except ValueError:
                created_dt = None
        if created_dt:
            text += f"\nДата: {created_dt.strftime('%d.%m.%Y %H:%M')}"
        else:
            text += f"\nДата: {created_at}"
    return text


async def notify_admins_about_questionnaire(questionnaire: Dict):
    admin_ids = get_admin_ids()
    if not admin_ids:
        return
    if bot_instance is None:
        logger.warning("Бот не установлен, анкеты не отправлены")
        return
    text = build_questionnaire_text(questionnaire)
    for admin_id in admin_ids:
        try:
            await bot_instance.send_message(admin_id, text)
        except Exception as e:
            logger.error(f"Не удалось отправить анкету админу {admin_id}: {e}")


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработка команды /start"""
    try:
        logger.info(f"Получена команда /start от пользователя {message.from_user.id} (@{message.from_user.username})")
        utm_source, utm_medium, utm_campaign, start_link_slug = parse_start_payload(message)
        if utm_source or utm_medium or utm_campaign:
            logger.info(f"UTM параметры: source={utm_source}, medium={utm_medium}, campaign={utm_campaign}")
        if start_link_slug:
            logger.info(f"Стартовая ссылка: {start_link_slug}")
        
        # Создаем или получаем пользователя
        _, created = await db.get_or_create_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign
        )
        if created:
            logger.info(f"Пользователь {message.from_user.id} создан в БД")
        else:
            logger.info(f"Пользователь {message.from_user.id} найден в БД")

        # Фиксируем клик по пользовательской ссылке
        if start_link_slug:
            link = await db.record_start_link_click(start_link_slug, message.from_user.id)
            if link:
                logger.info(f"Зафиксирован переход по ссылке {start_link_slug}")
            else:
                logger.warning(f"Ссылка {start_link_slug} не найдена")
        
        welcome_text = """Сильные результаты от нашего тренера Павла Васильченко! 

Чемпион строит чемпионов! Наш тренер Павел Васильченко не только побеждает на соревнованиях, но и помогает добиваться впечатляющих целей своим подопечным.

Всего за несколько месяцев работы его клиенты получают:

✅ Качественный набор мышечной массы

✅ Эффективное похудение и сушку

✅ Коррекцию фигуры и рельеф

Его чемпионские методики, проверенные на практике, дают гарантированный результат. Хватит сомневаться — пора меняться!

👉 Начните свою трансформацию сегодня — записывайтесь на пробную тренировку через наш сайт https://bogatyrmoscow.ru или по телефону +7 (968) 307-90-89"""

        # Отправляем приветствие с фото и кнопками
        photo_path = os.path.join("data", "main.png")
        keyboard = get_main_menu_keyboard()
        if os.path.exists(photo_path):
            logger.info(f"Отправка фото: {photo_path}")
            photo = FSInputFile(photo_path)
            try:
                await message.answer_photo(photo, caption=welcome_text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Не удалось отправить фото: {e}, отправляю текст без фото")
                await message.answer(welcome_text, reply_markup=keyboard)
        else:
            logger.warning(f"Файл фото не найден: {photo_path}")
            await message.answer(welcome_text, reply_markup=keyboard)

        logger.info(f"Приветственное сообщение отправлено пользователю {message.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /start: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data == "start_questionnaire")
async def start_questionnaire(callback: CallbackQuery, state: FSMContext):
    """Начало заполнения анкеты"""
    await callback.answer()
    await state.set_state(QuestionnaireStates.waiting_gender)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мужской", callback_data="gender_male")],
        [InlineKeyboardButton(text="Женский", callback_data="gender_female")]
    ])
    
    await callback.message.answer("Выберите ваш пол:", reply_markup=keyboard)


@router.callback_query(F.data.in_(["gender_male", "gender_female"]))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора пола"""
    await callback.answer()
    gender = "Мужской" if callback.data == "gender_male" else "Женский"
    await state.update_data(gender=gender)
    await state.set_state(QuestionnaireStates.waiting_age)
    await callback.message.answer("Укажите ваш возраст (число):")


@router.message(QuestionnaireStates.waiting_age)
async def process_age(message: Message, state: FSMContext):
    """Обработка возраста"""
    try:
        age = int(message.text)
        if age < 1 or age > 150:
            await message.answer("Пожалуйста, введите корректный возраст (от 1 до 150):")
            return
        await state.update_data(age=age)
        await state.set_state(QuestionnaireStates.waiting_weight)
        await message.answer("Укажите ваш вес в килограммах (например, 75.5):")
    except ValueError:
        await message.answer("Пожалуйста, введите число:")


@router.message(QuestionnaireStates.waiting_weight)
async def process_weight(message: Message, state: FSMContext):
    """Обработка веса"""
    try:
        weight = float(message.text.replace(",", "."))
        if weight < 1 or weight > 500:
            await message.answer("Пожалуйста, введите корректный вес (от 1 до 500 кг):")
            return
        await state.update_data(weight=weight)
        await state.set_state(QuestionnaireStates.waiting_workouts)
        await message.answer("Сколько тренировок в неделю вы хотите? (введите число):")
    except ValueError:
        await message.answer("Пожалуйста, введите число (можно с десятичной точкой):")


@router.message(QuestionnaireStates.waiting_workouts)
async def process_workouts(message: Message, state: FSMContext):
    """Обработка количества тренировок"""
    try:
        workouts = int(message.text)
        if workouts < 1 or workouts > 7:
            await message.answer("Пожалуйста, введите число от 1 до 7:")
            return
        await state.update_data(workouts_per_week=workouts)
        await state.set_state(QuestionnaireStates.waiting_diet)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_diet")]
        ])
        await message.answer("Опишите ваш текущий рацион питания (можно пропустить):", reply_markup=keyboard)
    except ValueError:
        await message.answer("Пожалуйста, введите число:")


@router.callback_query(F.data == "skip_diet")
async def skip_diet(callback: CallbackQuery, state: FSMContext):
    """Пропуск вопроса о рационе"""
    await callback.answer()
    await state.update_data(diet=None)
    await state.set_state(QuestionnaireStates.waiting_problem)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_problem")]
    ])
    await callback.message.answer("Есть ли у вас проблемы со здоровьем или травмы? (можно пропустить):", reply_markup=keyboard)


@router.message(QuestionnaireStates.waiting_diet)
async def process_diet(message: Message, state: FSMContext):
    """Обработка рациона питания"""
    diet = message.text[:500]  # Ограничиваем длину
    await state.update_data(diet=diet)
    await state.set_state(QuestionnaireStates.waiting_problem)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_problem")]
    ])
    await message.answer("Есть ли у вас проблемы со здоровьем или травмы? (можно пропустить):", reply_markup=keyboard)


@router.callback_query(F.data == "skip_problem")
async def skip_problem(callback: CallbackQuery, state: FSMContext):
    """Пропуск вопроса о проблемах/травмах"""
    await callback.answer()
    await state.update_data(problem_or_injury=None)
    await finish_questionnaire(callback, state)


@router.message(QuestionnaireStates.waiting_problem)
async def process_problem(message: Message, state: FSMContext):
    """Обработка проблем/травм"""
    problem = message.text[:500]  # Ограничиваем длину
    await state.update_data(problem_or_injury=problem)
    await finish_questionnaire(message, state)


async def finish_questionnaire(message_or_callback, state: FSMContext):
    """Завершение анкеты"""
    data = await state.get_data()
    
    # Получаем промокоды пользователя
    user_id = message_or_callback.from_user.id if hasattr(message_or_callback, 'from_user') else message_or_callback.message.from_user.id
    
    # Создаем анкету
    questionnaire_id = await db.create_questionnaire(
        user_id=user_id,
        gender=data.get("gender"),
        age=data.get("age"),
        weight=data.get("weight"),
        workouts_per_week=data.get("workouts_per_week"),
        diet=data.get("diet"),
        problem_or_injury=data.get("problem_or_injury")
    )
    
    # Привязываем промокоды к анкете
    await db.attach_user_promo_codes_to_questionnaire(user_id, questionnaire_id)
    
    questionnaire = await db.get_questionnaire_details(questionnaire_id)
    if questionnaire:
        await notify_admins_about_questionnaire(questionnaire)
        await db.mark_questionnaires_sent([questionnaire_id])

    await state.clear()
    
    text = "Спасибо! Мы свяжемся с вами в ближайшее время!"
    
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.answer(text)
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text)


@router.callback_query(F.data == "cooperation")
async def show_cooperation(callback: CallbackQuery):
    """Показать информацию о сотрудничестве"""
    await callback.answer()
    contact_phone = os.getenv("CONTACT_PHONE", "+7 (968) 307-90-89")
    contact_website = os.getenv("CONTACT_WEBSITE", "https://bogatyrmoscow.ru")
    
    text = f"""Для сотрудничества свяжитесь с нами:

📞 Телефон: {contact_phone}
🌐 Сайт: {contact_website}"""
    
    await callback.message.answer(text)


@router.callback_query(F.data == "enter_promo_code")
async def enter_promo_code(callback: CallbackQuery, state: FSMContext):
    """Начало ввода промокода"""
    await callback.answer()
    await state.set_state(PromoCodeStates.waiting_promo_code)
    await callback.message.answer("Введите промокод:")


@router.message(PromoCodeStates.waiting_promo_code)
async def process_promo_code(message: Message, state: FSMContext):
    """Обработка промокода"""
    promo_code = message.text.strip()
    
    # Проверяем промокод
    promo = await db.check_promo_code(promo_code)
    
    if promo:
        # Пытаемся добавить промокод к текущей анкете пользователя (если есть)
        user_id = message.from_user.id
        
        # Проверяем, не использован ли одноразовый промокод
        if promo['is_single_use']:
            # Проверяем использование
            async with db.pool.acquire() as conn:
                existing = await conn.fetchrow("""
                    SELECT * FROM promo_code_usage 
                    WHERE promo_code_id = $1
                """, promo['id'])
                if existing:
                    await message.answer("Этот промокод уже был использован.")
                    await state.clear()
                    return
        
        # Сохраняем промокод для пользователя (будет привязан к следующей анкете)
        async with db.pool.acquire() as conn:
            # Проверяем, не использован ли уже этот промокод пользователем
            existing = await conn.fetchrow("""
                SELECT * FROM promo_code_usage 
                WHERE user_id = $1 AND promo_code_id = $2 AND questionnaire_id IS NULL
            """, user_id, promo['id'])
            
            if not existing:
                await conn.execute("""
                    INSERT INTO promo_code_usage (user_id, promo_code_id, questionnaire_id)
                    VALUES ($1, $2, NULL)
                """, user_id, promo['id'])
        
        await message.answer(f"✅ Промокод '{promo_code.upper()}' успешно применен!\n\nОписание: {promo.get('description', 'Нет описания')}")
    else:
        await message.answer("❌ Промокод не найден. Проверьте правильность ввода.")
    
    await state.clear()


@router.callback_query(F.data == "examples")
async def show_examples(callback: CallbackQuery):
    """Показать примеры"""
    await callback.answer()
    
    example_files = ["ex.png", "ex2.png", "ex3.png", "ex4.png"]
    media_group = []
    for filename in example_files:
        photo_path = os.path.join("data", filename)
        if os.path.exists(photo_path):
            media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))

    if media_group:
        await callback.message.answer_media_group(media_group)
    else:
        await callback.message.answer("Примеры пока недоступны.")

    await callback.message.answer("Что-то еще?", reply_markup=get_main_menu_keyboard())


@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Админ-панель"""
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к админ-панели.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Управление промокодами", callback_data="admin_promo_codes")],
        [InlineKeyboardButton(text="Управление ссылками", callback_data="admin_links")]
    ])
    
    await message.answer("Админ-панель", reply_markup=keyboard)


@router.callback_query(F.data == "admin_promo_codes")
async def admin_promo_codes_menu(callback: CallbackQuery):
    """Меню управления промокодами"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    promo_codes = await db.get_all_promo_codes()
    
    keyboard_buttons = []
    for promo in promo_codes:
        text = f"{promo['code']} {'(одноразовый)' if promo['is_single_use'] else ''}"
        keyboard_buttons.append([InlineKeyboardButton(
            text=text,
            callback_data=f"admin_promo_{promo['id']}"
        )])
    
    keyboard_buttons.append([InlineKeyboardButton(text="➕ Добавить промокод", callback_data="admin_add_promo")])
    keyboard_buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    text = "Управление промокодами:\n\n"
    if promo_codes:
        for promo in promo_codes:
            text += f"• {promo['code']} - {promo['description'] or 'Без описания'}\n"
            text += f"  {'Одноразовый' if promo['is_single_use'] else 'Многоразовый'}\n"
            text += f"  Использований: {promo['usage_count']}\n\n"
    else:
        text += "Промокодов пока нет."
    
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "admin_add_promo")
async def admin_add_promo_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления промокода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(AdminStates.adding_promo_code)
    await callback.message.answer("Введите промокод:")


@router.message(AdminStates.adding_promo_code)
async def admin_add_promo_code(message: Message, state: FSMContext):
    """Обработка добавления промокода - код"""
    await state.update_data(promo_code=message.text.strip().upper())
    await state.set_state(AdminStates.adding_promo_description)
    await message.answer("Введите описание промокода:")


@router.message(AdminStates.adding_promo_description)
async def admin_add_promo_description(message: Message, state: FSMContext):
    """Обработка добавления промокода - описание"""
    description = message.text.strip()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data="promo_single_yes")],
        [InlineKeyboardButton(text="Нет", callback_data="promo_single_no")]
    ])
    
    await state.update_data(description=description)
    await state.set_state(AdminStates.adding_promo_type)
    await message.answer("Это одноразовый промокод?", reply_markup=keyboard)


@router.callback_query(F.data.in_(["promo_single_yes", "promo_single_no"]))
async def admin_add_promo_finish(callback: CallbackQuery, state: FSMContext):
    """Завершение добавления промокода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    data = await state.get_data()
    
    is_single_use = callback.data == "promo_single_yes"
    
    try:
        await db.create_promo_code(
            code=data['promo_code'],
            description=data['description'],
            is_single_use=is_single_use
        )
        await callback.message.answer("✅ Промокод успешно создан!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при создании промокода: {str(e)}")
    
    await state.clear()


@router.callback_query(F.data.startswith("admin_promo_"))
async def admin_promo_details(callback: CallbackQuery):
    """Детали промокода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    promo_id = int(callback.data.split("_")[-1])
    
    promo_codes = await db.get_all_promo_codes()
    promo = next((p for p in promo_codes if p['id'] == promo_id), None)
    
    if not promo:
        await callback.message.answer("Промокод не найден.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin_edit_promo_{promo_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_delete_promo_{promo_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promo_codes")]
    ])
    
    text = f"""Промокод: {promo['code']}
Описание: {promo['description'] or 'Нет описания'}
Тип: {'Одноразовый' if promo['is_single_use'] else 'Многоразовый'}
Использований: {promo['usage_count']}"""
    
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("admin_delete_promo_"))
async def admin_delete_promo(callback: CallbackQuery):
    """Удаление промокода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    promo_id = int(callback.data.split("_")[-1])
    
    try:
        await db.delete_promo_code(promo_id)
        await callback.message.answer("✅ Промокод удален!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при удалении: {str(e)}")


@router.callback_query(F.data.startswith("admin_edit_promo_"))
async def admin_edit_promo_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования промокода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    promo_id = int(callback.data.split("_")[-1])
    await state.update_data(promo_id=promo_id)
    await state.set_state(AdminStates.editing_promo_code)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Код", callback_data="edit_field_code")],
        [InlineKeyboardButton(text="Описание", callback_data="edit_field_description")],
        [InlineKeyboardButton(text="Тип (одноразовый/многоразовый)", callback_data="edit_field_type")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promo_codes")]
    ])
    
    await callback.message.answer("Что вы хотите изменить?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("edit_field_"))
async def admin_edit_promo_field(callback: CallbackQuery, state: FSMContext):
    """Выбор поля для редактирования"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    field = callback.data.replace("edit_field_", "")
    await state.update_data(editing_field=field)
    await state.set_state(AdminStates.editing_promo_field)
    
    if field == "code":
        await callback.message.answer("Введите новый код промокод:")
    elif field == "description":
        await callback.message.answer("Введите новое описание промокода:")
    elif field == "type":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одноразовый", callback_data="set_single_yes")],
            [InlineKeyboardButton(text="Многоразовый", callback_data="set_single_no")]
        ])
        await callback.message.answer("Выберите тип промокода:", reply_markup=keyboard)


@router.message(AdminStates.editing_promo_field)
async def admin_edit_promo_save(message: Message, state: FSMContext):
    """Сохранение изменений промокода"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    promo_id = data['promo_id']
    field = data['editing_field']
    
    try:
        if field == "code":
            await db.update_promo_code(promo_id, code=message.text.strip().upper())
            await message.answer("✅ Код промокода обновлен!")
        elif field == "description":
            await db.update_promo_code(promo_id, description=message.text.strip())
            await message.answer("✅ Описание промокода обновлено!")
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении: {str(e)}")
    
    await state.clear()


@router.callback_query(F.data.in_(["set_single_yes", "set_single_no"]))
async def admin_edit_promo_type(callback: CallbackQuery, state: FSMContext):
    """Изменение типа промокода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    data = await state.get_data()
    promo_id = data['promo_id']
    is_single_use = callback.data == "set_single_yes"
    
    try:
        await db.update_promo_code(promo_id, is_single_use=is_single_use)
        await callback.message.answer("✅ Тип промокода обновлен!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при обновлении: {str(e)}")
    
    await state.clear()


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    """Возврат в главное меню админ-панели"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Управление промокодами", callback_data="admin_promo_codes")],
        [InlineKeyboardButton(text="Управление ссылками", callback_data="admin_links")]
    ])
    
    await callback.message.answer("Админ-панель", reply_markup=keyboard)


@router.callback_query(F.data == "admin_links")
async def admin_links_menu(callback: CallbackQuery):
    """Управление ссылками"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    links = await db.get_all_start_links()
    bot_info = await callback.bot.get_me()
    base_link = f"https://t.me/{bot_info.username}?start="
    text = "Управление ссылками:\n\n"
    keyboard_buttons = []
    if links:
        for link in links:
            text += f"• {link['slug']} - {link.get('description') or 'Без описания'}\n"
            text += f"  Ссылка: {base_link}{link['slug']}\n"
            text += f"  Переходов всего: {link['total_clicks'] or 0}, за 30 дней: {link['month_clicks'] or 0}\n\n"
            keyboard_buttons.append([InlineKeyboardButton(text=f"{link['slug']}", callback_data=f"admin_link_{link['id']}")])
    else:
        text += "Ссылок пока нет.\n\n"
    keyboard_buttons.append([InlineKeyboardButton(text="➕ Добавить ссылку", callback_data="admin_add_link")])
    keyboard_buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "admin_add_link")
async def admin_add_link_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminStates.adding_link_slug)
    await callback.message.answer("Введите уникальный идентификатор ссылки (например, youtube2025):")


@router.message(AdminStates.adding_link_slug)
async def admin_add_link_slug(message: Message, state: FSMContext):
    slug = message.text.strip().lower()
    if not is_valid_slug(slug):
        await message.answer("Слаг может содержать только буквы, цифры, -, _. Попробуйте снова:")
        return
    await state.update_data(link_slug=slug)
    await state.set_state(AdminStates.adding_link_description)
    await message.answer("Введите описание ссылки:")


@router.message(AdminStates.adding_link_description)
async def admin_add_link_description(message: Message, state: FSMContext):
    data = await state.get_data()
    slug = data.get('link_slug')
    description = message.text.strip()
    try:
        link_id = await db.create_start_link(slug, description)
        bot_info = await message.bot.get_me()
        share_link = f"https://t.me/{bot_info.username}?start={slug}"
        await message.answer(f"✅ Ссылка создана!\nID: {link_id}\nСсылка: {share_link}")
    except Exception as e:
        await message.answer(f"❌ Ошибка при создании ссылки: {e}")
    await state.clear()


@router.callback_query(F.data.startswith("admin_link_"))
async def admin_link_details(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    link_id = int(callback.data.split("_")[-1])
    links = await db.get_all_start_links()
    link = next((l for l in links if l['id'] == link_id), None)
    if not link:
        await callback.message.answer("Ссылка не найдена.")
        return
    bot_info = await callback.bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={link['slug']}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin_edit_link_{link_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_delete_link_{link_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_links")]
    ])
    text = (f"Ссылка: {link['slug']}\n"
            f"Описание: {link.get('description') or 'Без описания'}\n"
            f"Ссылка для sharing: {share_link}\n"
            f"Переходов всего: {link.get('total_clicks') or 0}\n"
            f"Переходов за 30 дней: {link.get('month_clicks') or 0}")
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("admin_edit_link_"))
async def admin_edit_link(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    link_id = int(callback.data.split("_")[-1])
    links = await db.get_all_start_links()
    link = next((l for l in links if l['id'] == link_id), None)
    if not link:
        await callback.message.answer("Ссылка не найдена.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить ключевое слово", callback_data=f"edit_link_slug_{link_id}")],
        [InlineKeyboardButton(text="Изменить описание", callback_data=f"edit_link_desc_{link_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_links")]
    ])
    text = f"Что изменить у ссылки {link['slug']}?"
    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("edit_link_slug_"))
async def admin_edit_link_slug(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    link_id = int(callback.data.split("_")[-1])
    await state.update_data(link_id=link_id)
    await state.set_state(AdminStates.editing_link_slug)
    await callback.message.answer("Введите новый слаг (допустимы буквы, цифры, -, _):")


@router.message(AdminStates.editing_link_slug)
async def save_link_slug(message: Message, state: FSMContext):
    data = await state.get_data()
    link_id = data.get('link_id')
    slug = message.text.strip().lower()
    if not is_valid_slug(slug):
        await message.answer("Слаг может содержать только буквы, цифры, -, _. Попробуйте снова:")
        return
    try:
        await db.update_start_link(link_id, slug=slug)
        await message.answer("✅ Ключевое слово обновлено.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении: {e}")
    await state.clear()


@router.callback_query(F.data.startswith("edit_link_desc_"))
async def admin_edit_link_desc(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    link_id = int(callback.data.split("_")[-1])
    await state.update_data(link_id=link_id)
    await state.set_state(AdminStates.editing_link_description)
    await callback.message.answer("Введите новое описание ссылки:")


@router.message(AdminStates.editing_link_description)
async def save_link_description(message: Message, state: FSMContext):
    data = await state.get_data()
    link_id = data.get('link_id')
    description = message.text.strip()
    try:
        await db.update_start_link(link_id, description=description)
        await message.answer("✅ Описание обновлено.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении: {e}")
    await state.clear()


@router.callback_query(F.data.startswith("admin_delete_link_"))
async def admin_delete_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    link_id = int(callback.data.split("_")[-1])
    try:
        await db.delete_start_link(link_id)
        await callback.message.answer("✅ Ссылка удалена.")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при удалении: {e}")

