"""
English with Yev — автопостинг в канал.

Ежедневно:
  • Word of the Day        (слово дня)
  • Mistake of the Day     (типичная ошибка)
  • Phrase of the Day      (фраза / идиома дня)
  • Quiz                   (викторина-опрос Telegram)
  • Question of the Day    (вопрос для комментариев)
Еженедельно:
  • Weekly Test            (тест по словам недели — серия викторин)
  • Promo                  (анонс справочника, Копилки учителя и др.)

Контент берётся из content.json и идёт по кругу.
Настройки — через переменные окружения (см. README.md).
"""
import html
import json
import logging
import os
import random
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("yev-bot")

# ---------- настройки ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@english_with_yev")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Moscow"))
START_DATE = date.fromisoformat(os.environ.get("START_DATE", "2026-10-15"))
SIGNATURE = os.environ.get("SIGNATURE", "@English_with_Yev")
TEST_SIZE = int(os.environ.get("TEST_SIZE", "7"))

ALL_DAYS = "0,1,2,3,4,5,6"  # 0 = воскресенье, 1 = понедельник … 6 = суббота
DAY_NAMES = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]

# рубрика: (время по умолчанию, дни по умолчанию)
DEFAULTS = {
    "word":     ("09:00", ALL_DAYS),
    "mistake":  ("11:00", ALL_DAYS),
    "phrase":   ("13:00", ALL_DAYS),
    "quiz":     ("17:00", ALL_DAYS),
    "question": ("20:00", ALL_DAYS),
    "test":     ("12:00", "0"),   # воскресенье
    "promo":    ("19:00", "3"),   # среда
}
# Переопределение: TIME_WORD=10:00, DAYS_PROMO=3,6 и т.д. Пустой TIME_… выключает рубрику.
SCHEDULE = {
    k: (os.environ.get(f"TIME_{k.upper()}", t), os.environ.get(f"DAYS_{k.upper()}", d))
    for k, (t, d) in DEFAULTS.items()
}

CONTENT = json.loads((Path(__file__).parent / "content.json").read_text(encoding="utf-8"))
KEYS = {"word": "words", "phrase": "phrases", "quiz": "quizzes", "question": "questions",
        "mistake": "mistakes", "promo": "promos"}
KINDS = list(DEFAULTS)

e = html.escape


def today() -> date:
    return datetime.now(TZ).date()


def day_index(shift: int = 0) -> int:
    return (today() - START_DATE).days + shift


def pick(kind: str, shift: int = 0) -> dict:
    """Элемент на сегодня: номер дня от START_DATE по кругу."""
    items = CONTENT[KEYS[kind]]
    idx = day_index(shift)
    if kind == "promo":
        idx //= 7  # промо раз в неделю → своя нумерация по неделям
    return items[idx % len(items)]


def examples_block(examples) -> str:
    return "\n".join(f"• {e(en)}\n   <i>{e(ru)}</i>" for en, ru in examples)


# ---------- форматирование ----------
def fmt_word(w: dict) -> str:
    return (
        "📚 <b>WORD OF THE DAY</b>\n\n"
        f"🔤 <b>{e(w['word'])}</b> {e(w['ipa'])} — <i>{e(w['pos'])}</i>\n"
        f"🇷🇺 {e(w['ru'])}\n\n"
        f"💡 <i>{e(w['meaning'])}</i>\n\n"
        f"✏️ <b>Examples:</b>\n{examples_block(w['examples'])}\n\n"
        f"🔗 <b>Collocations:</b> {e(w['collocations'])}\n\n"
        "💬 Придумайте своё предложение с этим словом — пишите в комментариях, я проверю!\n"
        "🏆 Слова недели будут в воскресном тесте 😉\n\n"
        f"#wordoftheday {e(SIGNATURE)}"
    )


def fmt_mistake(m: dict) -> str:
    return (
        "🚫 <b>MISTAKE OF THE DAY</b>\n\n"
        f"❌ <s>{e(m['wrong'])}</s>\n"
        f"✅ <b>{e(m['right'])}</b>\n\n"
        f"📌 {e(m['why'])}\n\n"
        "💬 А вы так ошибались? Признавайтесь в комментариях 🙈\n\n"
        f"#commonmistakes {e(SIGNATURE)}"
    )


def fmt_phrase(p: dict) -> str:
    return (
        "🗣 <b>PHRASE OF THE DAY</b>\n\n"
        f"✨ <b>{e(p['phrase'])}</b>\n"
        f"🇷🇺 {e(p['ru'])}\n\n"
        f"📌 {e(p['note'])}\n\n"
        f"✏️ <b>Examples:</b>\n{examples_block(p['examples'])}\n\n"
        "💬 Как бы вы использовали эту фразу? Пишите в комментариях 👇\n\n"
        f"#phraseoftheday {e(SIGNATURE)}"
    )


def fmt_question(q: dict) -> str:
    return (
        "☕️ <b>QUESTION OF THE DAY</b>\n\n"
        f"❓ <b>{e(q['q'])}</b>\n"
        f"<i>{e(q['ru'])}</i>\n\n"
        f"🧩 Start like this: <i>{e(q['starter'])}</i>\n\n"
        "💬 Отвечайте на английском в комментариях — ошибки не страшны, я поправлю 🙌\n\n"
        f"#speakingpractice {e(SIGNATURE)}"
    )


FORMATTERS = {"word": fmt_word, "mistake": fmt_mistake, "phrase": fmt_phrase, "question": fmt_question}


def week_words(shift: int = 0) -> list[dict]:
    """Слова за последние TEST_SIZE дней (без повторов)."""
    seen, result = set(), []
    for s in range(shift - TEST_SIZE + 1, shift + 1):
        if day_index(s) < 0:
            continue  # слова до запуска бота ещё не публиковались
        w = pick("word", s)
        if w["word"] not in seen:
            seen.add(w["word"])
            result.append(w)
    return result


def test_polls(shift: int = 0) -> list[dict]:
    """Вопросы теста: «что значит слово?» + 3 неверных перевода из других слов."""
    rng = random.Random(day_index(shift))  # одинаковый тест при повторном вызове в тот же день
    all_words = CONTENT["words"]
    polls = []
    for w in week_words(shift):
        wrong = rng.sample([x["ru"] for x in all_words if x["word"] != w["word"]], 3)
        options = wrong + [w["ru"]]
        rng.shuffle(options)
        polls.append({
            "question": f"🔤 What does «{w['word']}» mean?",
            "options": [o[:100] for o in options],
            "correct": options.index(w["ru"]),
            "explain": f"{w['word']} {w['ipa']} — {w['ru']}"[:200],
        })
    return polls


# ---------- отправка ----------
async def send_quiz(bot, chat_id, question, options, correct, explain):
    await bot.send_poll(
        chat_id=chat_id, question=question[:300], options=options, type="quiz",
        correct_option_id=correct, explanation=explain[:200], is_anonymous=True,
    )


async def send(kind: str, bot, chat_id, shift: int = 0):
    if kind == "quiz":
        q = pick("quiz", shift)
        await send_quiz(bot, chat_id, f"🧠 Quiz: {q['q']}", q["options"], q["correct"], q["explain"])
        return

    if kind == "test":
        polls = test_polls(shift)
        await bot.send_message(
            chat_id=chat_id, parse_mode=ParseMode.HTML,
            text=("🏆 <b>WEEKLY TEST</b>\n\n"
                  f"Проверим, запомнили ли вы слова недели? {len(polls)} вопросов ниже 👇\n"
                  "Отвечайте честно, без подсказок 😉\n\n"
                  f"#weeklytest {e(SIGNATURE)}"),
        )
        for p in polls:
            await send_quiz(bot, chat_id, p["question"], p["options"], p["correct"], p["explain"])
        await bot.send_message(
            chat_id=chat_id, parse_mode=ParseMode.HTML,
            text="💬 Сколько правильных ответов у вас получилось? Пишите результат в комментариях: <b>__/"
                 f"{len(polls)}</b> 🔥",
        )
        return

    if kind == "promo":
        p = pick("promo", shift)
        markup = None
        if p.get("url"):
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(p.get("button") or "Подробнее", url=p["url"])]])
        # текст промо пишется в HTML вручную (в content.json), поэтому не экранируем
        await bot.send_message(chat_id=chat_id, text=p["text"], parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    text = FORMATTERS[kind](pick(kind, shift))
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)


# ---------- плановые публикации ----------
async def scheduled(ctx: ContextTypes.DEFAULT_TYPE):
    kind = ctx.job.data
    try:
        await send(kind, ctx.bot, CHANNEL_ID)
        log.info("Posted %s", kind)
    except Exception as exc:
        log.exception("Failed to post %s", kind)
        if ADMIN_ID:
            await ctx.bot.send_message(ADMIN_ID, f"⚠️ Не получилось опубликовать «{kind}».\n\n{explain_error(exc)}")


def explain_error(exc: Exception) -> str:
    """Понятное объяснение частых ошибок Telegram."""
    msg = str(exc)
    low = msg.lower()
    if "chat not found" in low:
        hint = (f"Канал «{CHANNEL_ID}» не найден. Проверьте CHANNEL_ID в Railway: это должен быть "
                "публичный username канала (например @english_with_yev), а для приватного канала — "
                "числовой ID вида -100…")
    elif "not enough rights" in low or "need administrator rights" in low or "forbidden" in low:
        hint = ("У бота нет прав в канале. Канал → Изменить → Администраторы → добавьте бота "
                "и включите «Публикация сообщений».")
    elif "can't parse entities" in low:
        hint = "Ошибка разметки в тексте поста — проверьте теги <b>, <i> в content.json."
    else:
        hint = "Пришлите этот текст ошибки в чат с Claude — разберёмся."
    return f"Ошибка Telegram: {msg}\n\n💡 {hint}\n\nПроверить настройки: /check"


# ---------- команды для админа ----------
def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id == ADMIN_ID:
            return await func(update, ctx)
        await update.message.reply_text("Этот бот публикует посты в канал English with Yev 🇬🇧")
    return wrapper


def days_label(days: str) -> str:
    if days.replace(" ", "") == ALL_DAYS:
        return "каждый день"
    return ", ".join(DAY_NAMES[int(d)] for d in days.split(",") if d.strip())


KINDS_HINT = " | ".join(KINDS)


@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sched = "\n".join(
        f"• {k}: {t} ({days_label(d)})" if t else f"• {k}: выключено" for k, (t, d) in SCHEDULE.items()
    )
    await update.message.reply_text(
        "Привет! Я публикую посты в канал.\n\n"
        f"Расписание ({TZ.key}):\n{sched}\n\n"
        f"Типы: {KINDS_HINT}\n\n"
        "Команды:\n"
        "/preview <тип> — показать сегодняшний пост мне\n"
        "/tomorrow <тип> — показать завтрашний\n"
        "/post <тип> — опубликовать в канал прямо сейчас\n"
        "/check — проверить, видит ли бот канал и есть ли права"
    )


def get_kind(ctx) -> str | None:
    kind = ctx.args[0].lower() if ctx.args else ""
    return kind if kind in KINDS else None


def make_cmd(target: str, shift: int, name: str):
    @admin_only
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        kind = get_kind(ctx)
        if not kind:
            return await update.message.reply_text(f"Укажите тип: /{name} {KINDS_HINT}")
        chat = CHANNEL_ID if target == "channel" else update.effective_chat.id
        try:
            await send(kind, ctx.bot, chat, shift=shift)
        except Exception as exc:
            log.exception("Command %s %s failed", name, kind)
            return await update.message.reply_text(f"⚠️ Не получилось.\n\n{explain_error(exc)}")
        if target == "channel":
            await update.message.reply_text("✅ Опубликовано в канал")
    return handler


@admin_only
async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Проверка: видит ли бот канал и может ли в нём публиковать."""
    lines = [f"CHANNEL_ID = {CHANNEL_ID}"]
    try:
        chat = await ctx.bot.get_chat(CHANNEL_ID)
        lines.append(f"✅ Канал найден: «{chat.title}» (id {chat.id})")
    except Exception as exc:
        lines.append(f"❌ Канал не найден: {exc}")
        lines.append("💡 Впишите в Railway → Variables → CHANNEL_ID точный @username канала "
                     "(не бота!). Его видно в канале: нажмите на название → «Ссылка» t.me/…")
        return await update.message.reply_text("\n".join(lines))
    try:
        me = await ctx.bot.get_me()
        member = await ctx.bot.get_chat_member(chat.id, me.id)
        if member.status == "administrator":
            can_post = getattr(member, "can_post_messages", None)
            if can_post is False:
                lines.append("❌ Бот админ, но право «Публикация сообщений» выключено")
            else:
                lines.append("✅ Бот — админ с правом публикации. Всё готово! Попробуйте /post word")
        else:
            lines.append(f"❌ Бот не админ канала (статус: {member.status}). "
                         "Канал → Изменить → Администраторы → добавьте бота с правом «Публикация сообщений»")
    except Exception as exc:
        lines.append(f"❌ Не удалось проверить права: {exc}\n💡 Скорее всего, бот не добавлен в администраторы канала")
    await update.message.reply_text("\n".join(lines))


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("preview", make_cmd("me", 0, "preview")))
    app.add_handler(CommandHandler("tomorrow", make_cmd("me", 1, "tomorrow")))
    app.add_handler(CommandHandler("post", make_cmd("channel", 0, "post")))
    app.add_handler(CommandHandler("check", cmd_check))

    for kind, (hhmm, days) in SCHEDULE.items():
        if not hhmm:
            continue  # пустое значение = рубрика выключена
        h, m = map(int, hhmm.split(":"))
        day_tuple = tuple(int(d) for d in days.split(",") if d.strip())
        app.job_queue.run_daily(scheduled, time=time(h, m, tzinfo=TZ), days=day_tuple, data=kind, name=kind)
        log.info("Scheduled %s at %s on %s (%s)", kind, hhmm, days_label(days), TZ.key)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
