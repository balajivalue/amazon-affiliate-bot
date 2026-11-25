import os
import re
import sys
import traceback
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

URL_REGEX = re.compile(r"https?://\S+")
ASIN_REGEX = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/([A-Z0-9]{10})",
    re.IGNORECASE,
)
AMAZON_DOMAINS = ("amazon.", "amzn.to", "amzn.in", "a.co")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "").strip()
RAW_SECRET = os.environ.get("WEBHOOK_SECRET", "myaffiliatebot2026").strip()
WEBHOOK_SECRET = re.sub(r"[^A-Za-z0-9_-]", "", RAW_SECRET) or "myaffiliatebot2026"


async def convert_amazon_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if not any(domain in host for domain in AMAZON_DOMAINS):
        return url

    if any(short in host for short in ("amzn.to", "amzn.in", "a.co")):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=10.0, headers=HEADERS
            ) as client:
                response = await client.get(url)
                url = str(response.url)
                parsed = urlparse(url)
                host = parsed.netloc.lower()
        except Exception:
            return url

    asin_match = ASIN_REGEX.search(parsed.path)
    if asin_match and "amazon." in host:
        asin = asin_match.group(1).upper()
        return f"https://{host}/dp/{asin}?tag={AFFILIATE_TAG}"

    if "amazon." in host:
        query = parse_qs(parsed.query)
        query["tag"] = [AFFILIATE_TAG]
        new_query = urlencode(query, doseq=True)
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, "", new_query, "")
        )

    return url


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    text = message.text or message.caption
    if not text:
        return

    urls = URL_REGEX.findall(text)
    if not urls:
        return

    new_text = text
    changed = False

    for url in urls:
        clean_url = url.rstrip(".,)!?")
        converted = await convert_amazon_url(clean_url)
        if converted != clean_url:
            new_text = new_text.replace(clean_url, converted)
            changed = True

    if changed:
        await message.reply_text(new_text)


def main():
    print("Starting bot script...", flush=True)

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing in Render Environment Variables.", flush=True)
        sys.exit(1)
    if not AFFILIATE_TAG:
        print("ERROR: AFFILIATE_TAG is missing in Render Environment Variables.", flush=True)
        sys.exit(1)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            handle_message,
        )
    )

    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

    if render_url:
        port = int(os.environ.get("PORT", 10000))
        webhook_url = f"{render_url.rstrip('/')}/webhook"
        print(f"Starting webhook on port {port} ({webhook_url})", flush=True)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
    else:
        print("Starting in polling mode...", flush=True)
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)
