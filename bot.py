import os
import re
import sys
import traceback
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

URL_REGEX = re.compile(r"https?://\S+")
ASIN_REGEX = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|gp/offer-listing|exec/obidos/ASIN|product|d)/([A-Z0-9]{10})(?:[/?#]|$)",
    re.IGNORECASE,
)
AMAZON_DOMAINS = ("amazon.", "amzn.to", "amzn.in", "a.co")

BLOAT_PREFIXES = ("pf_rd_", "pd_rd_", "utm_", "hv", "gad_", "mk_")
BLOAT_KEYS = {
    "ref", "ref_", "qid", "sr", "crid", "sprefix", "dib", "dib_tag",
    "content-id", "_encoding", "encoding", "ie", "linkcode", "linkid",
    "camp", "creative", "creativeasin", "ascsubtag", "gclid", "fbclid",
    "social_share", "starsleft", "skiptwisterog", "dchild",
    "nsdoptoutparam", "sbo", "mcid", "hydadcr", "th", "psc", "keywords",
    "smid", "ufe",
}

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


def normalize_channel_id(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    raw = re.sub(r"^(https?://)?(www\.)?t\.me/", "", raw, flags=re.IGNORECASE).strip("/")
    if raw.startswith("@") or raw.lstrip("-").isdigit():
        return raw
    return f"@{raw}"


CHANNEL_ID = normalize_channel_id(os.environ.get("CHANNEL_ID", ""))


def extract_asin(parsed):
    match = ASIN_REGEX.search(parsed.path)
    if match:
        return match.group(1).upper()

    query = parse_qs(parsed.query)
    for key in ("url", "redirectUrl", "u"):
        if key in query and query[key]:
            nested = unquote(query[key][0])
            match = ASIN_REGEX.search(nested)
            if match:
                return match.group(1).upper()

    if "asin" in query and query["asin"]:
        candidate = query["asin"][0].strip().upper()
        if re.fullmatch(r"[A-Z0-9]{10}", candidate):
            return candidate

    return None


async def expand_short_url(url: str) -> str:
    current = url
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=10.0, headers=HEADERS
        ) as client:
            for _ in range(5):
                response = await client.get(current)
                location = response.headers.get("location")
                if response.is_redirect and location:
                    current = str(response.url.join(location))
                    parsed = urlparse(current)
                    if "amazon." in parsed.netloc.lower() and extract_asin(parsed):
                        return current
                else:
                    break
    except Exception:
        pass
    return current


async def convert_amazon_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if not any(domain in host for domain in AMAZON_DOMAINS):
        return url

    if any(short in host for short in ("amzn.to", "amzn.in", "a.co")):
        url = await expand_short_url(url)
        parsed = urlparse(url)
        host = parsed.netloc.lower()

    if "amazon." not in host:
        return url

    short_host = host[4:] if host.startswith("www.") else host

    asin = extract_asin(parsed)
    if asin:
        return f"https://{short_host}/dp/{asin}?tag={AFFILIATE_TAG}"

    clean_path = re.sub(r"/ref=[^/?#]*", "", parsed.path)
    raw_query = parse_qs(parsed.query)
    clean_query = {}
    for k, v in raw_query.items():
        kl = k.lower()
        if kl in BLOAT_KEYS or kl.startswith(BLOAT_PREFIXES):
            continue
        clean_query[k] = v

    clean_query["tag"] = [AFFILIATE_TAG]
    new_query = urlencode(clean_query, doseq=True)
    return urlunparse(("https", short_host, clean_path, "", new_query, ""))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or message.chat.type != "private":
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

    if not changed:
        return

    if CHANNEL_ID:
        try:
            if message.photo:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=message.photo[-1].file_id,
                    caption=new_text,
                )
            elif message.video:
                await context.bot.send_video(
                    chat_id=CHANNEL_ID,
                    video=message.video.file_id,
                    caption=new_text,
                )
            else:
                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=new_text,
                )
            await message.reply_text(f"✅ Posted to {CHANNEL_ID}!\n\n{new_text}")
        except Exception as e:
            await message.reply_text(
                f"⚠️ Link converted, but could not post to {CHANNEL_ID}.\n"
                f"Reason: {e}\n\n"
                "Check that your bot is added as an Admin in your channel with 'Post Messages' turned ON."
            )
    else:
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

