#!/usr/bin/env python3
"""
@realDonaldTrump Truth Social Monitor
---------------------------------------
Fetches the latest post from @realDonaldTrump on Truth Social via the
Truth Social Mastodon-compatible REST API, routed through scrape.do
(US residential proxy) to bypass geo-restrictions.

Every run:
  - Fetches the latest post (text + image if any)
  - Compares with last_post_id.txt
  - If new: generates a polished post card PNG (Truth Social style),
            prepends to posts.md (keeping only the last 3 posts),
            saves state
  - If same: exits cleanly

Environment variables required:
  SCRAPEDO_TOKEN  – Your scrape.do API token
"""

import os
import re
import sys
import json
import io
import requests
from datetime import datetime, timezone
from html.parser import HTMLParser

# ── Pillow (for card rendering) ────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

# ── Configuration ──────────────────────────────────────────────────────────────
TRUTH_SOCIAL_BASE   = "https://truthsocial.com/api/v1"
TRUMP_ACCOUNT_ID    = "107780257626128497"   # @realDonaldTrump permanent ID
SCRAPEDO_API        = "https://api.scrape.do/"
POSTS_FILE          = "posts.md"
LAST_ID_FILE        = "last_post_id.txt"
IMAGES_DIR          = "images"
MAX_POSTS           = 3   # keep only the N most recent posts in posts.md

# Avatar URL – fetched once and cached locally as images/trump_avatar.png
TRUMP_AVATAR_URL = (
    "https://static-assets-1.truthsocial.com/tmtg:prime-ts-assets/"
    "accounts/avatars/107/780/257/626/128/497/original/454286ac07a6f6e6.jpeg"
)
AVATAR_CACHE = os.path.join(IMAGES_DIR, "trump_avatar.png")

# Font – Inter variable font bundled in the repo under fonts/
FONT_DIR     = os.path.join(os.path.dirname(__file__), "fonts")
INTER_VAR    = os.path.join(FONT_DIR, "Inter-Variable.ttf")
FALLBACK_REG = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
FALLBACK_BLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

# ── Timestamp helper ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# ── HTML → plain text ──────────────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str):
        self._parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p"):
            self._parts.append("\n")

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def html_to_text(html: str) -> str:
    """Convert Truth Social HTML post content to plain text."""
    if not html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html)
    text = stripper.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ── scrape.do fetcher ──────────────────────────────────────────────────────────

def _scrapedo_get_json(url: str, token: str) -> list | dict:
    params = {
        "token":   token,
        "url":     url,
        "geoCode": "us",
        "render":  "false",
        "super":   "true",
    }
    resp = requests.get(SCRAPEDO_API, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

# ── Post fetcher ───────────────────────────────────────────────────────────────

def fetch_latest_post(token: str) -> dict | None:
    url = (
        f"{TRUTH_SOCIAL_BASE}/accounts/{TRUMP_ACCOUNT_ID}/statuses"
        f"?exclude_replies=true&limit=1"
    )
    print(f"[{_now()}] Fetching latest post from Truth Social …")
    try:
        posts = _scrapedo_get_json(url, token)
    except Exception as e:
        print(f"[{_now()}] ERROR: API request failed: {e}", file=sys.stderr)
        return None

    if not isinstance(posts, list) or len(posts) == 0:
        print(f"[{_now()}] No posts returned from API.")
        return None

    raw = posts[0]

    # ── Extract text ──────────────────────────────────────────────────────────
    text = html_to_text(raw.get("content", ""))
    if not text and raw.get("reblog"):
        reblog = raw["reblog"]
        text = html_to_text(reblog.get("content", ""))
        text = f"[Retruth from @{reblog.get('account', {}).get('acct', 'unknown')}]\n\n{text}"

    # ── Extract post image URL (for embedding inside the card) ────────────────
    image_url = ""
    media = raw.get("media_attachments", [])
    for m in media:
        if m.get("type") in ("image", "gifv"):
            image_url = m.get("url", "")
            break
    if not image_url and raw.get("card"):
        card_img = raw["card"].get("image", "")
        if card_img:
            image_url = card_img

    # ── Account info ──────────────────────────────────────────────────────────
    account      = raw.get("account", {})
    display_name = account.get("display_name", "Donald J. Trump")
    username     = account.get("username", "realDonaldTrump")
    avatar_url   = account.get("avatar", TRUMP_AVATAR_URL)

    # ── Timestamp ─────────────────────────────────────────────────────────────
    created_at = raw.get("created_at", "")
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        created_at_fmt = dt.strftime("%b %-d, %Y · %-I:%M %p UTC")
    except Exception:
        created_at_fmt = created_at

    post_id  = raw.get("id", "")
    post_url = raw.get("url", f"https://truthsocial.com/@realDonaldTrump/{post_id}")

    return {
        "post_id":      post_id,
        "text":         text,
        "image_url":    image_url,
        "post_url":     post_url,
        "created_at":   created_at_fmt,
        "fetched_at":   _now(),
        "display_name": display_name,
        "username":     username,
        "avatar_url":   avatar_url,
    }

# ── Avatar cache ───────────────────────────────────────────────────────────────

def ensure_avatar(avatar_url: str) -> str:
    """Download avatar fresh on every run. Returns local path or empty string."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    try:
        print(f"[{_now()}] Downloading avatar from: {avatar_url[:80]}")
        r = requests.get(avatar_url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (compatible; bot/1.0)"
        })
        r.raise_for_status()
        # Save as JPEG to avoid format confusion
        avatar_jpeg = os.path.join(IMAGES_DIR, "trump_avatar.jpg")
        with open(avatar_jpeg, "wb") as f:
            f.write(r.content)
        print(f"[{_now()}] Avatar downloaded → {avatar_jpeg} ({len(r.content):,} bytes)")
        return avatar_jpeg
    except Exception as e:
        print(f"[{_now()}] WARNING: Could not download avatar: {e}", file=sys.stderr)
        return ""

# ── Font loader ────────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False):
    if not PILLOW_OK:
        return None
    try:
        if os.path.exists(INTER_VAR) and os.path.getsize(INTER_VAR) > 10000:
            return ImageFont.truetype(INTER_VAR, size)
    except Exception:
        pass
    fallback = FALLBACK_BLD if bold else FALLBACK_REG
    try:
        return ImageFont.truetype(fallback, size)
    except Exception:
        return ImageFont.load_default()

# ── Card renderer ──────────────────────────────────────────────────────────────

def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for word in words:
        test = (current + " " + word).strip()
        w = dummy_draw.textlength(test, font=font)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_card(post: dict, avatar_path: str) -> str:
    """
    Render a polished Truth Social-style post card PNG.
    Returns the local file path of the saved card.
    """
    if not PILLOW_OK:
        print(f"[{_now()}] WARNING: Pillow not available – skipping card render.")
        return ""

    S = 2   # 2× retina scale

    # Palette
    BG         = (255, 255, 255)
    BORDER     = (219, 219, 219)
    TEXT_COL   = (15,  20,  25)
    META_COL   = (83,  100, 113)
    NAME_COL   = (15,  20,  25)
    HANDLE_COL = (83,  100, 113)
    TS_PURPLE  = (98,  0,   238)
    BADGE_BLUE = (29,  155, 240)

    # Layout
    CARD_W      = 600 * S
    PAD         = 20 * S
    AVATAR_SIZE = 48 * S
    HEADER_H    = AVATAR_SIZE + 16 * S
    LINE_GAP    = 6 * S
    SECTION_GAP = 14 * S
    BRAND_BAR_H = 4 * S

    # Fonts
    font_name   = _font(17 * S, bold=True)
    font_handle = _font(14 * S)
    font_text   = _font(16 * S)
    font_meta   = _font(13 * S)
    font_brand  = _font(11 * S, bold=True)

    # Wrap text
    text_area_w  = CARD_W - 2 * PAD
    text_lines   = _wrap_text(post["text"], font_text, text_area_w)
    line_h       = int(font_text.size * 1.45)
    text_block_h = len(text_lines) * line_h + max(0, len(text_lines) - 1) * LINE_GAP

    # Load post image (if any)
    post_img = None
    if post["image_url"]:
        try:
            r = requests.get(post["image_url"], timeout=30)
            r.raise_for_status()
            post_img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img_w = CARD_W - 2 * PAD
            ratio = img_w / post_img.width
            img_h = int(post_img.height * ratio)
            if img_h > 400 * S:
                img_h = 400 * S
                img_w = int(post_img.width * (img_h / post_img.height))
            post_img = post_img.resize((img_w, img_h), Image.LANCZOS)
        except Exception as e:
            print(f"[{_now()}] WARNING: Could not load post image: {e}")
            post_img = None

    post_img_h = (post_img.height + SECTION_GAP) if post_img else 0

    # Card height
    CARD_H = (
        BRAND_BAR_H + PAD
        + HEADER_H + SECTION_GAP
        + text_block_h + SECTION_GAP
        + post_img_h
        + 20 * S   # timestamp row
        + PAD
    )

    # Canvas
    canvas = Image.new("RGB", (CARD_W, CARD_H), BG)
    draw   = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, CARD_W - 1, CARD_H - 1], outline=BORDER, width=S)
    draw.rectangle([0, 0, CARD_W, BRAND_BAR_H], fill=TS_PURPLE)

    y = BRAND_BAR_H + PAD

    # Avatar
    av_x, av_y = PAD, y
    if avatar_path and os.path.exists(avatar_path):
        try:
            av_img = Image.open(avatar_path)
            print(f"[{_now()}] Avatar loaded: mode={av_img.mode}, size={av_img.size}")
            av = _circle_crop(av_img, AVATAR_SIZE)
            canvas.paste(av, (av_x, av_y), av)
            print(f"[{_now()}] Avatar pasted onto card OK")
        except Exception as e:
            print(f"[{_now()}] WARNING: Avatar render failed: {e}", file=sys.stderr)
            draw.ellipse([av_x, av_y, av_x + AVATAR_SIZE, av_y + AVATAR_SIZE], fill=(180, 180, 180))
    else:
        print(f"[{_now()}] WARNING: Avatar path not found: {avatar_path!r}")
        draw.ellipse([av_x, av_y, av_x + AVATAR_SIZE, av_y + AVATAR_SIZE], fill=(180, 180, 180))

    # Name + verified badge + handle
    name_x = av_x + AVATAR_SIZE + 12 * S
    name_y = av_y + 4 * S
    draw.text((name_x, name_y), post["display_name"], font=font_name, fill=NAME_COL)
    name_w   = int(draw.textlength(post["display_name"], font=font_name))
    badge_r  = 7 * S
    badge_cx = name_x + name_w + 8 * S + badge_r
    badge_cy = name_y + font_name.size // 2
    draw.ellipse(
        [badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r],
        fill=BADGE_BLUE
    )
    tick = [
        (badge_cx - 4 * S, badge_cy),
        (badge_cx - 1 * S, badge_cy + 3 * S),
        (badge_cx + 4 * S, badge_cy - 3 * S),
    ]
    draw.line(tick, fill=(255, 255, 255), width=max(1, 2 * S))
    draw.text((name_x, name_y + font_name.size + 4 * S),
              f"@{post['username']}", font=font_handle, fill=HANDLE_COL)

    y += HEADER_H + SECTION_GAP

    # Post text
    for line in text_lines:
        draw.text((PAD, y), line, font=font_text, fill=TEXT_COL)
        y += line_h + LINE_GAP
    y += SECTION_GAP - LINE_GAP

    # Attached image
    if post_img:
        canvas.paste(post_img, (PAD, y))
        draw.rectangle([PAD, y, PAD + post_img.width, y + post_img.height],
                       outline=BORDER, width=S)
        y += post_img.height + SECTION_GAP

    # Timestamp + brand
    draw.text((PAD, y), post["created_at"], font=font_meta, fill=META_COL)
    brand_w = int(draw.textlength("TRUTH SOCIAL", font=font_brand))
    draw.text((CARD_W - PAD - brand_w, y), "TRUTH SOCIAL", font=font_brand, fill=TS_PURPLE)

    # Save
    os.makedirs(IMAGES_DIR, exist_ok=True)
    card_path = os.path.join(IMAGES_DIR, f"{post['post_id']}_card.png")
    canvas.save(card_path, "PNG", dpi=(144 * S, 144 * S))
    print(f"[{_now()}] Card rendered → {card_path}")
    return card_path

# ── File I/O ───────────────────────────────────────────────────────────────────

def load_last_post_id() -> str:
    if os.path.exists(LAST_ID_FILE):
        with open(LAST_ID_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_last_post_id(post_id: str) -> None:
    with open(LAST_ID_FILE, "w", encoding="utf-8") as f:
        f.write(post_id)


def _render_post_md(post: dict, card_path: str) -> str:
    """Render a post dict as a Markdown section string."""
    lines = []
    lines.append(f"\n## Post detected at {post['fetched_at']}\n")
    lines.append(f"**Posted on Truth Social:** {post['created_at']}\n")
    lines.append(f"**Source:** [{post['post_url']}]({post['post_url']})\n")
    lines.append(f"**Post ID:** `{post['post_id']}`\n")
    lines.append("### Text\n")
    lines.append(post["text"] + "\n")
    if card_path:
        lines.append("### Post Card\n")
        lines.append(f"![Post card]({card_path})\n")
    lines.append("\n---")
    return "\n".join(lines)


def save_posts_file(new_post_block: str) -> None:
    existing_blocks: list[str] = []
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
        parts = re.split(r"(?=\n## Post detected at |\A## Post detected at )", raw)
        existing_blocks = [p for p in parts if p.strip() and "## Post detected at" in p]

    all_blocks = [new_post_block] + existing_blocks
    all_blocks = all_blocks[:MAX_POSTS]

    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(all_blocks) + "\n")

    print(f"[{_now()}] posts.md updated ({len(all_blocks)} post(s) kept, max {MAX_POSTS})")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"[{_now()}] ── @realDonaldTrump Truth Social Monitor starting ──")

    token = os.environ.get("SCRAPEDO_TOKEN", "")
    if not token:
        print("ERROR: SCRAPEDO_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # ── Fetch latest post ─────────────────────────────────────────────────────
    post = fetch_latest_post(token)
    if not post:
        print(f"[{_now()}] Could not fetch post – exiting without changes.")
        sys.exit(0)

    print(f"[{_now()}] Post ID   : {post['post_id']}")
    print(f"[{_now()}] Created   : {post['created_at']}")
    print(f"[{_now()}] Text      : {post['text'][:120]!r}")
    print(f"[{_now()}] Image URL : {(post['image_url'][:80] + '…') if post['image_url'] else '(none)'}")

    # ── Compare with last saved post ──────────────────────────────────────────
    last_id = load_last_post_id()
    if post["post_id"] == last_id:
        print(f"[{_now()}] Post unchanged – nothing to do.")
        sys.exit(0)

    print(f"[{_now()}] New post detected (previous: {last_id or 'none'}) – saving …")

    # ── Ensure avatar is cached ───────────────────────────────────────────────
    avatar_path = ensure_avatar(post["avatar_url"])

    # ── Render post card ──────────────────────────────────────────────────────
    card_path = render_card(post, avatar_path)

    # ── Save to posts.md ──────────────────────────────────────────────────────
    post_block = _render_post_md(post, card_path)
    save_posts_file(post_block)
    save_last_post_id(post["post_id"])

    print(f"[{_now()}] ── Done ──")


if __name__ == "__main__":
    main()
