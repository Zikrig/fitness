import asyncio
import os
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from database import Database
import handlers
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Проверка обязательных переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен в переменных окружения!")
    logger.error("Проверьте файл .env и убедитесь, что BOT_TOKEN указан")
    sys.exit(1)

# Инициализация бота и диспетчера
try:
    bot = Bot(token=BOT_TOKEN)
    logger.info("✅ Бот инициализирован")
except Exception as e:
    logger.error(f"❌ Ошибка при инициализации бота: {e}")
    sys.exit(1)

dp = Dispatcher(storage=MemoryStorage())

db = Database()

# Передаем экземпляр БД и подключаем маршрутизатор
handlers.set_database(db)
handlers.set_bot(bot)
dp.include_router(handlers.router)
logger.info("✅ Роутер подключен")


def get_admin_ids():
    """Получить список ID админов"""
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    return [int(id.strip()) for id in admin_ids_str.split(",") if id.strip()]


async def send_daily_questionnaires():
    """Ежедневная отправка новых анкет админам"""
    try:
        questionnaires = await db.get_new_questionnaires()
        
        if not questionnaires:
            return
        
        questionnaire_ids = []
        
        for questionnaire in questionnaires:
            questionnaire_ids.append(questionnaire['id'])
            await handlers.notify_admins_about_questionnaire(questionnaire)
        
        if questionnaire_ids:
            await db.mark_questionnaires_sent(questionnaire_ids)
            
    except Exception as e:
        logger.error(f"Ошибка при отправке анкет: {e}", exc_info=True)


async def on_startup():
    """Действия при запуске бота"""
    try:
        logger.info("🔄 Подключение к базе данных...")
        await db.connect()
        logger.info("✅ Подключение к базе данных установлено")
        
        logger.info("🔄 Инициализация таблиц...")
        await db.init_db()
        logger.info("✅ Таблицы инициализированы")
        
        # Проверяем, что бот работает
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username} (ID: {bot_info.id})")
        logger.info("✅ Бот готов к работе!")
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске: {e}", exc_info=True)
        raise


async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("🔄 Остановка бота...")
    try:
        await db.close()
        logger.info("✅ Соединение с базой данных закрыто")
    except Exception as e:
        logger.error(f"❌ Ошибка при закрытии соединения: {e}")
    logger.info("✅ Бот остановлен")


async def main():
    """Главная функция"""
    try:
        # Настройка планировщика для ежедневной рассылки
        logger.info("🔄 Настройка планировщика...")
        scheduler = AsyncIOScheduler()
        # Отправка каждый день в 20:00
        scheduler.add_job(
            send_daily_questionnaires,
            trigger=CronTrigger(hour=20, minute=0),
            id='daily_questionnaires'
        )
        scheduler.start()
        logger.info("✅ Планировщик запущен")
        
        # Запуск бота
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)
        
        logger.info("🔄 Запуск polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("⚠️ Получен сигнал остановки")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        raise
    finally:
        logger.info("🔄 Остановка планировщика...")
        scheduler.shutdown()
        logger.info("✅ Планировщик остановлен")


if __name__ == "__main__":
    asyncio.run(main())

