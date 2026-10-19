import asyncio
import contextlib
import logging
import os
import time
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from dotenv import load_dotenv
from openai import AsyncOpenAI
from core import SYSTEM, NOTICE_EN, NOTICE_RU, Session, chunks

load_dotenv()
dp = Dispatcher()
sessions = {}
client = None
model = ''
provider = 'ollama'
allowed = set()
TTL = 1800


def local(message, en, ru):
    return ru if (message.from_user.language_code or '').startswith('ru') else en


@dp.message()
async def handle(message: Message):
    if message.chat.type != 'private' or not message.from_user:
        return
    uid = message.from_user.id
    if allowed and uid not in allowed:
        return
    text = message.text or ''
    command = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
    if command in ('/start', '/privacy'):
        await message.answer(local(message, NOTICE_EN.replace('{provider}', os.getenv('AI_PROVIDER', 'ollama')), NOTICE_RU.replace('{provider}', os.getenv('AI_PROVIDER', 'ollama'))))
        return
    if command == '/help':
        await message.answer(local(message,
            '/start — consent & privacy\n/agree — confirm 18+ and consent\n/new — clear context\n/delete — clear context and revoke consent\n\nIf you are in immediate danger, contact local emergency services and someone you trust nearby. This bot cannot contact help for you.',
            '/start — согласие и приватность\n/agree — подтвердить 18+ и согласие\n/new — очистить контекст\n/delete — очистить контекст и отозвать согласие\n\nПри непосредственной опасности свяжитесь с местной экстренной службой и человеком рядом, которому доверяете. Бот не может вызвать помощь.'))
        return
    now = time.monotonic()
    s = sessions.get(uid)
    if s and now - s.touched >= TTL:
        s.clear(revoke=True)
        sessions.pop(uid, None)
        s = None
    if command in ('/new', '/delete'):
        if s:
            s.clear(revoke=command == '/delete')
            if command == '/delete':
                sessions.pop(uid, None)
        await message.answer(local(message,
            'App context cleared. Telegram and provider records are not deleted. Use /start to see consent details.',
            'Контекст приложения очищен. Записи Telegram и провайдера не удалены. Условия согласия: /start.'))
        return
    if command == '/agree':
        if not s:
            if len(sessions) >= 500:
                await message.answer('Capacity reached. Please try later. /help')
                return
            s = sessions[uid] = Session()
        s.consent = True
        s.touched = now
        await message.answer(local(message, 'What is on your mind? Write in your preferred language.',
                                    'Что тебя сейчас беспокоит? Пиши на удобном тебе языке.'))
        return
    if not s or not s.consent:
        await message.answer(local(message, NOTICE_EN.replace('{provider}', os.getenv('AI_PROVIDER', 'ollama')), NOTICE_RU.replace('{provider}', os.getenv('AI_PROVIDER', 'ollama'))))
        return
    if command:
        await message.answer('/help')
        return
    if not text or len(text) > 4000:
        await message.answer(local(message, 'Please send text up to 4000 characters.', 'Отправь текст до 4000 символов.'))
        return
    if s.busy or now - s.last_request < 3:
        await message.answer(local(message, 'Please wait a moment. /help remains available.', 'Подожди немного. /help доступна.'))
        return
    s.busy = True
    s.last_request = s.touched = now
    generation = s.generation
    try:
        instructions = SYSTEM + '\nTelegram language hint: ' + (message.from_user.language_code or 'en')
        messages = s.history + [{'role':'user', 'content':text}]
        if provider == 'openai':
            result = await client.responses.create(
                model=model, instructions=instructions, input=messages,
                store=False, max_output_tokens=1200,
            )
            answer = result.output_text.strip()
            complete = result.status == 'completed'
        else:
            result = await client.chat.completions.create(
                model=model,
                messages=[{'role':'system', 'content':instructions}] + messages,
                max_tokens=1200,
            )
            answer = (result.choices[0].message.content or '').strip()
            complete = result.choices[0].finish_reason == 'stop'
        # Deletion/new conversation during a request invalidates its reply and memory.
        if sessions.get(uid) is not s or generation != s.generation or not s.consent:
            return
        if not answer or not complete:
            raise RuntimeError('Incomplete model response')
        for part in chunks(answer):
            if sessions.get(uid) is not s or generation != s.generation:
                return
            await message.answer(part)
        s.remember(text, answer)
        s.touched = time.monotonic()
    except Exception:
        # Do not log request bodies, exception strings, tokens or user identifiers.
        if sessions.get(uid) is s and generation == s.generation:
            await message.answer(local(message,
                'I cannot reply right now. Please try again. If you feel unsafe, contact someone nearby or local emergency services. /help',
                'Сейчас не получается ответить. Попробуй ещё раз. Если ты в опасности, обратись к человеку рядом или местной экстренной службе. /help'))
    finally:
        s.busy = False


async def expire():
    while True:
        await asyncio.sleep(15)
        now = time.monotonic()
        for uid, s in list(sessions.items()):
            if now - s.touched >= TTL:
                s.clear(revoke=True)
                sessions.pop(uid, None)


async def main():
    global client, model, allowed, provider
    provider = os.getenv('AI_PROVIDER', 'ollama')
    if provider not in ('ollama', 'groq', 'openai'):
        raise SystemExit('AI_PROVIDER must be ollama, groq or openai')
    required = ('TELEGRAM_BOT_TOKEN', 'AI_MODEL', 'ALLOWED_USER_IDS')
    if provider != 'ollama':
        required += ('AI_API_KEY',)
    missing = [name for name in required if not os.getenv(name, '').strip()]
    if missing:
        raise SystemExit('Set in .env: ' + ', '.join(missing))
    # Private pilot only: require an explicit nonempty allowlist.
    allowed = {int(item.strip()) for item in os.environ['ALLOWED_USER_IDS'].split(',')}
    model = os.environ['AI_MODEL']
    endpoints = {'ollama': 'http://localhost:11434/v1', 'groq': 'https://api.groq.com/openai/v1', 'openai': 'https://api.openai.com/v1'}
    client = AsyncOpenAI(base_url=endpoints[provider], api_key=os.getenv('AI_API_KEY') or 'ollama', timeout=90, max_retries=1)
    bot = Bot(os.environ['TELEGRAM_BOT_TOKEN'])
    cleanup = asyncio.create_task(expire())
    try:
        await dp.start_polling(bot, tasks_concurrency_limit=32)
    finally:
        cleanup.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup
        sessions.clear()
        await client.close()
        await bot.session.close()


if __name__ == '__main__':
    # Suppress framework exception dumps that can contain message data.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise SystemExit('Startup/polling failed. Check tokens, network and existing webhook; no secrets logged.') from None
