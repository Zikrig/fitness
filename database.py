import asyncpg
import os
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime, date

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Подключение к базе данных"""
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL не установлен в переменных окружения")
        
        logger.info(f"Подключение к базе данных: {database_url.split('@')[1] if '@' in database_url else 'скрыто'}")
        try:
            self.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
            logger.info("✅ Пул соединений создан")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к базе данных: {e}")
            raise

    async def close(self):
        """Закрытие соединения"""
        if self.pool:
            await self.pool.close()

    async def init_db(self):
        """Инициализация таблиц базы данных"""
        async with self.pool.acquire() as conn:
            # Таблица пользователей
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    utm_source VARCHAR(255),
                    utm_medium VARCHAR(255),
                    utm_campaign VARCHAR(255)
                )
            """)

            # Таблица анкет
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS questionnaires (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    gender VARCHAR(10),
                    age INTEGER,
                    weight DECIMAL(5,2),
                    workouts_per_week INTEGER,
                    diet VARCHAR(500),
                    problem_or_injury VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent_to_admin BOOLEAN DEFAULT FALSE
                )
            """)

            # Таблица промокодов
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS promo_codes (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(100) UNIQUE NOT NULL,
                    description TEXT,
                    is_single_use BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица использования промокодов
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS promo_code_usage (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    promo_code_id INTEGER REFERENCES promo_codes(id),
                    questionnaire_id INTEGER REFERENCES questionnaires(id),
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Создаем индекс для быстрого поиска
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_promo_usage_user_promo 
                ON promo_code_usage(user_id, promo_code_id, questionnaire_id)
            """)

            # Таблица статистики ссылок
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS link_stats (
                    id SERIAL PRIMARY KEY,
                    utm_source VARCHAR(255),
                    utm_medium VARCHAR(255),
                    utm_campaign VARCHAR(255),
                    user_id BIGINT REFERENCES users(user_id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица пользовательских ссылок
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS start_links (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(100) UNIQUE NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица кликов по пользовательским ссылкам
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS start_link_clicks (
                    id SERIAL PRIMARY KEY,
                    start_link_id INTEGER REFERENCES start_links(id) ON DELETE CASCADE,
                    user_id BIGINT REFERENCES users(user_id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    async def get_or_create_user(self, user_id: int, username: str = None, 
                                 first_name: str = None, utm_source: str = None,
                                 utm_medium: str = None, utm_campaign: str = None) -> Tuple[Dict, bool]:
        """Получить или создать пользователя. Возвращает (user, создан_ли)"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1", user_id
            )
            created = False
            
            if not user:
                await conn.execute("""
                    INSERT INTO users (user_id, username, first_name, utm_source, utm_medium, utm_campaign)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, user_id, username, first_name, utm_source, utm_medium, utm_campaign)
                created = True
                
                # Сохраняем статистику ссылки
                if utm_source or utm_medium or utm_campaign:
                    await conn.execute("""
                        INSERT INTO link_stats (utm_source, utm_medium, utm_campaign, user_id)
                        VALUES ($1, $2, $3, $4)
                    """, utm_source, utm_medium, utm_campaign, user_id)
                
                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id = $1", user_id
                )
            
            return dict(user), created

    async def create_questionnaire(self, user_id: int, gender: str = None, 
                                   age: int = None, weight: float = None,
                                   workouts_per_week: int = None, diet: str = None,
                                   problem_or_injury: str = None) -> int:
        """Создать анкету"""
        async with self.pool.acquire() as conn:
            questionnaire_id = await conn.fetchval("""
                INSERT INTO questionnaires 
                (user_id, gender, age, weight, workouts_per_week, diet, problem_or_injury)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """, user_id, gender, age, weight, workouts_per_week, diet, problem_or_injury)
            return questionnaire_id

    async def get_user_promo_codes(self, user_id: int) -> List[Dict]:
        """Получить все промокоды пользователя (без привязки к анкете)"""
        async with self.pool.acquire() as conn:
            promo_codes = await conn.fetch("""
                SELECT DISTINCT pc.*, pcu.used_at
                FROM promo_code_usage pcu
                JOIN promo_codes pc ON pcu.promo_code_id = pc.id
                WHERE pcu.user_id = $1 AND pcu.questionnaire_id IS NULL
                ORDER BY pcu.used_at DESC
            """, user_id)
            return [dict(pc) for pc in promo_codes]

    async def attach_user_promo_codes_to_questionnaire(self, user_id: int, 
                                                       questionnaire_id: int):
        """Привязать все промокоды пользователя к анкете"""
        async with self.pool.acquire() as conn:
            # Получаем все промокоды пользователя без привязки к анкете
            promo_usages = await conn.fetch("""
                SELECT pcu.promo_code_id, pc.is_single_use
                FROM promo_code_usage pcu
                JOIN promo_codes pc ON pcu.promo_code_id = pc.id
                WHERE pcu.user_id = $1 AND pcu.questionnaire_id IS NULL
            """, user_id)
            
            attached_count = 0
            for usage in promo_usages:
                promo_id = usage['promo_code_id']
                is_single_use = usage['is_single_use']
                
                # Проверяем, не использован ли одноразовый промокод в другой анкете
                if is_single_use:
                    existing = await conn.fetchrow("""
                        SELECT * FROM promo_code_usage 
                        WHERE promo_code_id = $1 AND questionnaire_id IS NOT NULL
                    """, promo_id)
                    if existing:
                        continue
                
                # Привязываем промокод к анкете
                try:
                    await conn.execute("""
                        INSERT INTO promo_code_usage (user_id, promo_code_id, questionnaire_id)
                        VALUES ($1, $2, $3)
                        ON CONFLICT DO NOTHING
                    """, user_id, promo_id, questionnaire_id)
                    attached_count += 1
                except:
                    pass
            
            return attached_count

    async def check_promo_code(self, promo_code: str) -> Optional[Dict]:
        """Проверить промокод"""
        async with self.pool.acquire() as conn:
            promo = await conn.fetchrow(
                "SELECT * FROM promo_codes WHERE UPPER(code) = UPPER($1)", promo_code
            )
            if promo:
                return dict(promo)
            return None

    async def get_new_questionnaires(self) -> List[Dict]:
        """Получить новые анкеты, которые еще не отправлены админам"""
        async with self.pool.acquire() as conn:
            questionnaires = await conn.fetch("""
                SELECT q.*, u.username, u.first_name,
                       ARRAY_AGG(pc.code) as promo_codes
                FROM questionnaires q
                JOIN users u ON q.user_id = u.user_id
                LEFT JOIN promo_code_usage pcu ON q.id = pcu.questionnaire_id
                LEFT JOIN promo_codes pc ON pcu.promo_code_id = pc.id
                WHERE q.sent_to_admin = FALSE
                GROUP BY q.id, u.username, u.first_name
                ORDER BY q.created_at DESC
            """)
            return [dict(q) for q in questionnaires]

    async def mark_questionnaires_sent(self, questionnaire_ids: List[int]):
        """Отметить анкеты как отправленные"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE questionnaires 
                SET sent_to_admin = TRUE 
                WHERE id = ANY($1::int[])
            """, questionnaire_ids)

    async def get_questionnaire_details(self, questionnaire_id: int) -> Optional[Dict]:
        """Получить детали анкеты"""
        async with self.pool.acquire() as conn:
            questionnaire = await conn.fetchrow("""
                SELECT q.*, u.username, u.first_name,
                       ARRAY_AGG(pc.code) AS promo_codes
                FROM questionnaires q
                JOIN users u ON q.user_id = u.user_id
                LEFT JOIN promo_code_usage pcu ON q.id = pcu.questionnaire_id
                LEFT JOIN promo_codes pc ON pcu.promo_code_id = pc.id
                WHERE q.id = $1
                GROUP BY q.id, u.username, u.first_name
            """, questionnaire_id)
            return dict(questionnaire) if questionnaire else None

    # Админские методы для промокодов
    async def get_all_promo_codes(self) -> List[Dict]:
        """Получить все промокоды"""
        async with self.pool.acquire() as conn:
            promo_codes = await conn.fetch("""
                SELECT pc.*, 
                       COUNT(pcu.id) as usage_count
                FROM promo_codes pc
                LEFT JOIN promo_code_usage pcu ON pc.id = pcu.promo_code_id
                GROUP BY pc.id
                ORDER BY pc.created_at DESC
            """)
            return [dict(pc) for pc in promo_codes]

    async def create_promo_code(self, code: str, description: str, 
                               is_single_use: bool = False) -> int:
        """Создать промокод"""
        async with self.pool.acquire() as conn:
            promo_id = await conn.fetchval("""
                INSERT INTO promo_codes (code, description, is_single_use)
                VALUES (UPPER($1), $2, $3)
                RETURNING id
            """, code, description, is_single_use)
            return promo_id

    async def update_promo_code(self, promo_id: int, code: str = None, 
                               description: str = None, 
                               is_single_use: bool = None):
        """Обновить промокод"""
        async with self.pool.acquire() as conn:
            updates = []
            values = []
            param_num = 1
            
            if code is not None:
                updates.append(f"code = UPPER(${param_num})")
                values.append(code)
                param_num += 1
            if description is not None:
                updates.append(f"description = ${param_num}")
                values.append(description)
                param_num += 1
            if is_single_use is not None:
                updates.append(f"is_single_use = ${param_num}")
                values.append(is_single_use)
                param_num += 1
            
            if updates:
                values.append(promo_id)
                await conn.execute(f"""
                    UPDATE promo_codes 
                    SET {', '.join(updates)}
                    WHERE id = ${param_num}
                """, *values)

    async def delete_promo_code(self, promo_id: int):
        """Удалить промокод"""
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM promo_codes WHERE id = $1", promo_id)

    # Статистика по ссылкам
    async def get_link_stats(self, period_days: int = None) -> List[Dict]:
        """Получить статистику по ссылкам"""
        async with self.pool.acquire() as conn:
            if period_days:
                stats = await conn.fetch("""
                    SELECT 
                        COALESCE(utm_source, 'direct') as source,
                        COALESCE(utm_medium, 'none') as medium,
                        COALESCE(utm_campaign, 'none') as campaign,
                        COUNT(*) as count
                    FROM link_stats
                    WHERE created_at >= CURRENT_DATE - ($1 || ' days')::INTERVAL
                    GROUP BY utm_source, utm_medium, utm_campaign
                    ORDER BY count DESC
                """, str(period_days))
            else:
                stats = await conn.fetch("""
                    SELECT 
                        COALESCE(utm_source, 'direct') as source,
                        COALESCE(utm_medium, 'none') as medium,
                        COALESCE(utm_campaign, 'none') as campaign,
                        COUNT(*) as count
                    FROM link_stats
                    GROUP BY utm_source, utm_medium, utm_campaign
                    ORDER BY count DESC
                """)
            return [dict(s) for s in stats]

    # Управление ссылками
    async def create_start_link(self, slug: str, description: str) -> int:
        async with self.pool.acquire() as conn:
            slug = slug.lower()
            link_id = await conn.fetchval("""
                INSERT INTO start_links (slug, description)
                VALUES ($1, $2)
                RETURNING id
            """, slug, description)
            return link_id

    async def get_all_start_links(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            links = await conn.fetch("""
                SELECT sl.*,
                       COUNT(slc.id) AS total_clicks,
                       COUNT(CASE WHEN slc.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN 1 END) AS month_clicks
                FROM start_links sl
                LEFT JOIN start_link_clicks slc ON sl.id = slc.start_link_id
                GROUP BY sl.id
                ORDER BY sl.created_at DESC
            """)
            return [dict(link) for link in links]

    async def get_start_link_by_slug(self, slug: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            link = await conn.fetchrow("""
                SELECT * FROM start_links WHERE slug = LOWER($1)
            """, slug)
            return dict(link) if link else None

    async def update_start_link(self, link_id: int, slug: str = None, description: str = None):
        updates = []
        values = []
        param_num = 1
        if slug is not None:
            updates.append(f"slug = LOWER(${param_num})")
            values.append(slug)
            param_num += 1
        if description is not None:
            updates.append(f"description = ${param_num}")
            values.append(description)
            param_num += 1
        if not updates:
            return
        values.append(link_id)
        async with self.pool.acquire() as conn:
            await conn.execute(f"""
                UPDATE start_links
                SET {", ".join(updates)}
                WHERE id = ${param_num}
            """, *values)

    async def delete_start_link(self, link_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM start_links WHERE id = $1", link_id)

    async def record_start_link_click(self, slug: str, user_id: int):
        async with self.pool.acquire() as conn:
            link = await conn.fetchrow("""
                SELECT * FROM start_links WHERE slug = LOWER($1)
            """, slug)
            if not link:
                return None
            await conn.execute("""
                INSERT INTO start_link_clicks (start_link_id, user_id)
                VALUES ($1, $2)
            """, link["id"], user_id)
            return dict(link)

    async def get_start_link_stats(self, period_days: int = None) -> List[Dict]:
        async with self.pool.acquire() as conn:
            if period_days:
                stats = await conn.fetch("""
                    SELECT sl.slug, sl.description,
                           COUNT(slc.id) AS click_count
                    FROM start_links sl
                    LEFT JOIN start_link_clicks slc ON sl.id = slc.start_link_id
                    WHERE slc.created_at >= CURRENT_DATE - ($1 || ' days')::INTERVAL OR slc.created_at IS NULL
                    GROUP BY sl.id
                    ORDER BY click_count DESC NULLS LAST
                """, str(period_days))
            else:
                stats = await conn.fetch("""
                    SELECT sl.slug, sl.description,
                           COUNT(slc.id) AS click_count
                    FROM start_links sl
                    LEFT JOIN start_link_clicks slc ON sl.id = slc.start_link_id
                    GROUP BY sl.id
                    ORDER BY click_count DESC NULLS LAST
                """)
            return [dict(stat) for stat in stats]

