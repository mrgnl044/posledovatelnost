import asyncio
import os
import time
import logging
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InputMediaPhoto,
    InputMediaAudio,
    InputMediaVideo,
    InputMediaDocument,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from dotenv import load_dotenv


#  Настройка и запуск

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Не найден BOT_TOKEN в .env файле")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

bot = Bot(token=TOKEN)
dp = Dispatcher()


#  Глобальные переменные

user_sessions = {}
media_cache = defaultdict(list)
group_timers = {}
warning_cache = {}
WARNING_COOLDOWN = 1.5
SESSION_LIFETIME = 120  # секунд


#  Клавиатура

start_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🆘 Помощь"), KeyboardButton(text="↩️ Сбросить")],
    ],
    resize_keyboard=True
)


#  Команды

@dp.message(F.text.in_({"/start", "↩️ Сбросить"}))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_sessions.pop(user_id, None)
    
    gids_to_remove = []
    for gid, msgs in media_cache.items():
        if any(m.from_user.id == user_id for m in msgs):
            gids_to_remove.append(gid)
    
    for gid in gids_to_remove:
        media_cache.pop(gid, None)

    await message.answer(
        "Назначим новую последовательность!\n"
        "Отправьте сообщение с 2–10 файлами одного типа",
        reply_markup=start_kb
    )


@dp.message(F.text.in_({"/help", "🆘 Помощь"}))
async def cmd_help(message: types.Message):
    help_text = (
        "<b>Как пользоваться:</b>\n\n"
        "1. Отправьте медиагруппу - всё то, что вы загружаете файлом/документом (2–10 вложений <b>одинакового</b> типа: фото, видео, аудио, документы).\n"
        "2. Бот покажет текущий порядок файлов, а вы назначите новую последовательность.\n\n"
        "<b>Пример:</b> если файлов 3, введите '3 2 1'.\n"
        "После ввода вы получите файлы в новом порядке одним сообщением, которое остаётся только переслать"
    )
    await message.answer(help_text, parse_mode="HTML")


#  Обработка медиагрупп

async def process_media_group(group_id: str):
    messages = media_cache.pop(group_id, [])
    if not messages:
        return

    try:
        message = messages[0]
        user_id = message.from_user.id
        content_types = {m.content_type for m in messages}

        if len(content_types) > 1 or not (2 <= len(messages) <= 10):
            logging.warning(f"Неверная медиагруппа от {user_id}")
            await message.answer("Отправьте 2-10 файлов одного типа")
            return
    except Exception as e:
        logging.error(f"Ошибка обработки медиагруппы {group_id}: {e}")
        return

    content_type = messages[0].content_type

    if content_type == "photo":
        files = [m.photo[-1].file_id for m in messages]
    elif content_type == "video":
        files = [m.video.file_id for m in messages]
    elif content_type == "audio":
        files = [m.audio.file_id for m in messages]
    elif content_type == "document":
        files = [m.document.file_id for m in messages]
    else:
        return

    user_sessions[user_id] = {
        "files": files,
        "type": content_type,
        "expected_count": len(files),
        "timestamp": time.time(),
    }

    order_list = " ".join(str(i + 1) for i in range(len(files)))
    reverse_list = " ".join(str(i + 1) for i in reversed(range(len(files))))

    await message.answer(
        f"Получено файлов: {len(files)}\n\n"
        f"Текущий порядок: {order_list}\n"
        f"Введите новую последовательность (например: {reverse_list})"
    )

    logging.info(f"Media group processed from user {user_id}: {len(files)} files of type {content_type}")


@dp.message(F.media_group_id)
async def handle_media_group(message: types.Message):
    group_id = message.media_group_id
    media_cache[group_id].append(message)

    if group_id in group_timers:
        group_timers[group_id].cancel()

    group_timers[group_id] = asyncio.create_task(delayed_process(group_id, delay=0.7))


async def delayed_process(group_id: str, delay: float):
    try:
        await asyncio.sleep(delay)
        await process_media_group(group_id)
    except asyncio.CancelledError:
        pass
    finally:
        group_timers.pop(group_id, None)


#  Ввод новой последовательности

@dp.message(F.text.regexp(r"^(\d+\s*)+$"))
async def handle_order_input(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)

    if not session:
        await message.answer("Сначала отправьте медиагруппу")
        return

    if time.time() - session["timestamp"] > SESSION_LIFETIME:
        user_sessions.pop(user_id, None)
        await message.answer("Сессия устарела, отправьте новую медиагруппу")
        return

    files = session["files"]
    content_type = session["type"]
    expected_count = session["expected_count"]

    try:
        order_numbers = [int(x) for x in message.text.split()]
    except ValueError:
        await message.answer("Введите только числа, разделенные пробелами")
        return

    if len(order_numbers) != expected_count or len(set(order_numbers)) != expected_count:
        await message.answer(f"Введите числа от 1 до {expected_count} через пробел")
        return

    if sorted(order_numbers) != list(range(1, expected_count + 1)):
        await message.answer(f"Числа должны быть в диапазоне 1–{expected_count}")
        return

    ordered_files = [files[i - 1] for i in order_numbers]

    if content_type == "photo":
        media = [InputMediaPhoto(media=f) for f in ordered_files]
    elif content_type == "audio":
        media = [InputMediaAudio(media=f) for f in ordered_files]
    elif content_type == "video":
        media = [InputMediaVideo(media=f) for f in ordered_files]
    elif content_type == "document":
        media = [InputMediaDocument(media=f) for f in ordered_files]
    else:
        return

    try:
        await bot.send_media_group(chat_id=user_id, media=media)
        
    except Exception as e:
        logging.error(f"Ошибка отправки медиагруппы: {e}")
        await message.answer("Ошибка при отправке медиагруппы. Попробуйте еще раз")
    finally:
        
        user_sessions.pop(user_id, None)


#  Антиспам и fallback

@dp.message()
async def fallback_handler(message: types.Message):
    user_id = message.from_user.id
    now = time.time()

    if now - warning_cache.get(user_id, 0) < WARNING_COOLDOWN:
        return

    warning_cache[user_id] = now
    await message.answer("Некорректная команда. Используйте /help")


#  Очистка кэша

async def cleanup_cache():
    while True:
        now = time.time()
        expired = []
        for gid, msgs in media_cache.items():
            if msgs and hasattr(msgs[0], 'date') and msgs[0].date:
                if now - msgs[0].date.timestamp() > 30:
                    expired.append(gid)
        for gid in expired:
            media_cache.pop(gid, None)
        await asyncio.sleep(30)


#  main

async def main():
    logging.info("Бот запущен... (Ctrl + C для остановки)")
    cleanup_task = asyncio.create_task(cleanup_cache())
    try:
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(main())

