import os
from dotenv import load_dotenv
from pathlib import Path

# грузим .env из папки, где лежит app.py, независимо от рабочей директории
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ChatAction
from avatars import AVATARS
import storage as db
from llm import chat_complete, available as llm_available
from images import generate_image, available as images_available
from telegram import InputFile
from io import BytesIO

TEXT_DAILY_LIMIT = int(os.getenv("TEXT_DAILY_LIMIT", "200"))
CHAT_WINDOW_LIMIT = int(os.getenv("CHAT_WINDOW_LIMIT", "100"))
IMAGE_DAILY_LIMIT = int(os.getenv("IMAGE_DAILY_LIMIT", "10"))


def _split_chunks(s: str, size: int = 4096):
    return [s[i:i+size] for i in range(0, len(s), size)]


BOT_TOKEN = os.getenv("BOT_TOKEN")

# ---- Команды ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.init_db()
    user = update.effective_user
    db.ensure_user(user.id, user.username or "")
    await update.message.reply_text(
        "Привет! Я персональный бот с аватарами.\n"
        "Команды:\n"
        "/avatars — выбрать персонажа\n"
        "/profile — посмотреть текущего\n"
        "/limit — показать остатки лимитов (текст и картинки)\n"
        "/reset — начать новый чат\n"
        "/image <промпт> — сгенерировать картинку\n"
        "Просто напиши мне — поболтаем!"
    )

async def limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    used_text = db.get_daily_used(uid)
    remain_text = max(0, TEXT_DAILY_LIMIT - used_text)

    used_img = db.get_images_used(uid)
    remain_img = max(0, IMAGE_DAILY_LIMIT - used_img)

    await update.message.reply_text(
        "Лимиты на сегодня:\n"
        f"• Текст: {remain_text} из {TEXT_DAILY_LIMIT}\n"
        f"• Картинки: {remain_img} из {IMAGE_DAILY_LIMIT}"
    )



def generate_text(system_prompt: str, dialog: list[tuple[str, str]], user_text: str) -> str:
    style_hint = f"[{system_prompt}]"
    return f"Ты сказал: “{user_text}”. {style_hint} Мой ответ: держись курса, всё получится! 🚀"

async def avatars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = []
    row = []
    for i, av in enumerate(AVATARS, start=1):
        row.append(InlineKeyboardButton(av.name, callback_data=f"avatar:{av.key}"))
        if i % 2 == 0:
            kb.append(row); row = []
    if row: kb.append(row)
    await update.message.reply_text("Выбери аватар:", reply_markup=InlineKeyboardMarkup(kb))

async def on_avatar_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, key = q.data.split(":", 1)
    user = q.from_user
    db.set_avatar(user.id, user.username or "", key)
    await q.edit_message_text(f"Готово! Текущий аватар: {next(a.name for a in AVATARS if a.key==key)}")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = db.get_avatar(update.effective_user.id)
    if not key:
        await update.message.reply_text("Аватар не выбран. Нажми /avatars")
        return
    av = next(a for a in AVATARS if a.key == key)
    await update.message.reply_text(f"Текущий аватар: {av.name}\nСтиль: {av.system_prompt}")

# ---- Обработка текста ----
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    # суточный лимит (проверка до инкремента)
    used = db.get_daily_used(user.id)
    if used >= TEXT_DAILY_LIMIT:
        await update.message.reply_text(
            f"Лимит на сегодня исчерпан ({TEXT_DAILY_LIMIT}). Приходи завтра или набери /reset (начать новый чат)."
        )
        return

    # профиль и активный чат
    db.ensure_user(user.id, user.username or "")
    key = db.get_avatar(user.id)
    if not key:
        await update.message.reply_text("Сначала выбери аватар: /avatars")
        return
    avatar = next(a for a in AVATARS if a.key == key)

    chat_id = db.get_chat_id(user.id)

    # фиксируем факт пользовательского сообщения
    used = db.inc_daily_usage(user.id)
    if used > TEXT_DAILY_LIMIT:
        # кто-то дожал лимит  — откатывать нечего, просто сообщим
        await update.message.reply_text(
            f"Лимит на сегодня исчерпан ({TEXT_DAILY_LIMIT})."
        )
        return

    # сохраняем реплику пользователя в ТЕКУЩИЙ чат
    db.save_message(user.id, chat_id, "user", text)

    # грузим контекст текущего чата (до 100 сообщений)
    dialog = db.load_recent_chat(user.id, chat_id, limit=CHAT_WINDOW_LIMIT)

    system_prompt = (
        "Ты — персональный ассистент. "
        "Отвечай дружелюбно, коротко и по делу, учитывая стиль персонажа.\n"
        f"Стиль персонажа: {avatar.system_prompt}\n"
    )

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        if llm_available():
            reply = chat_complete(system_prompt=system_prompt, dialog=dialog, user_text=text)
        else:
            # локальный фолбэк
            reply = f"Ты сказал: “{text}”. [{avatar.system_prompt}] Мой ответ: держись курса, всё получится! 🚀"
    except Exception as e:
        reply = f"🙈 Ошибка при обращении к ИИ: {e}"

    # сохраняем ответ ассистента
    db.save_message(user.id, chat_id, "assistant", reply)

    # отправляем с разбиением
    for chunk in _split_chunks(reply):
        await update.message.reply_text(chunk)

    # проверяем длину чата и при необходимости начинаем новый
    total = db.count_chat_messages(user.id, chat_id)
    if total >= CHAT_WINDOW_LIMIT:
        new_id = db.new_chat(user.id)
        await update.message.reply_text(
            f"Порог памяти чата достигнут ({CHAT_WINDOW_LIMIT} сообщений). "
            f"Начинаю новый чат #{new_id} — контекст очищен."
        )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.reset_dialog(update.effective_user.id)
    used = db.get_daily_used(update.effective_user.id)
    remain = max(0, TEXT_DAILY_LIMIT - used)
    await update.message.reply_text(f"История сброшена. Остаток на сегодня: {remain} сообщений. ✅")

async def image_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args or []
    prompt = " ".join(args).strip()

    if not images_available():
        await update.message.reply_text("Генерация изображений сейчас не настроена.")
        return

    if not prompt:
        await update.message.reply_text("Использование: /image <описание картинки>")
        return

    # дневной лимит
    used = db.get_images_used(user.id)
    if used >= IMAGE_DAILY_LIMIT:
        await update.message.reply_text(f"Лимит изображений на сегодня исчерпан ({IMAGE_DAILY_LIMIT}).")
        return

    # инкремент (фиксируем попытку)
    used = db.inc_images_usage(user.id)
    if used > IMAGE_DAILY_LIMIT:
        await update.message.reply_text(f"Лимит изображений на сегодня исчерпан ({IMAGE_DAILY_LIMIT}).")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
        filename, img_bytes = generate_image(prompt, size="1024x1024")
        bio = BytesIO(img_bytes)
        bio.name = filename
        await update.message.reply_photo(photo=InputFile(bio), caption=f"🖼 {prompt}")
    except Exception as e:
        await update.message.reply_text(f"🙈 Ошибка генерации изображения: {e}")


# ---- main ----
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в .env")
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("avatars", avatars))
    app.add_handler(CallbackQueryHandler(on_avatar_pick, pattern=r"^avatar:"))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("limit", limit_cmd))
    app.add_handler(CommandHandler("image", image_cmd))

    print("Bot is running (polling). Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()

