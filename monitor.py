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
  - If new: prepends to posts.md (keeping only the last 3 posts),
            downloads the image, saves state
  - If same: exits cleanly

Environment variables required:
  SCRAPEDO_TOKEN  – Your scrape.do API token
"""

import os
import re
import sys
import json
import hashlib
import requests
from datetime import datetime, timezone
from html.parser import HTMLParser

# ── Configuration ──────────────────────────────────────────────────────────────
TRUTH_SOCIAL_BASE   = "https://truthsocial.com/api/v1"
TRUMP_ACCOUNT_ID    = "107780257626128497"   # @realDonaldTrump permanent ID
SCRAPEDO_API        = "https://api.scrape.do/"
POSTS_FILE          = "posts.md"
LAST_ID_FILE        = "last_post_id.txt"
IMAGES_DIR          = "images"
MAX_POSTS           = 3   # keep only the N most recent posts in posts.md

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
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ── scrape.do fetcher ──────────────────────────────────────────────────────────

def _scrapedo_get_json(url: str, token: str) -> list | dict:
    """
    Fetch a Truth Social API URL through scrape.do (US proxy, no JS rendering)
    and return parsed JSON.
    """
    params = {
        "token":   token,
        "url":     url,
        "geoCode": "us",
        "render":  "false",   # Truth Social API returns JSON — no JS needed
        "super":   "true",    # residential proxies for reliability
    }
    resp = requests.get(SCRAPEDO_API, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

# ── Post fetcher ───────────────────────────────────────────────────────────────

def fetch_latest_post(token: str) -> dict | None:
    """
    Fetch the latest post from @realDonaldTrump and return a normalised dict.
    Returns None if the fetch fails or no posts are found.
    """
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

    # If it's a reblog (retruth), use the reblogged post's content
    if not text and raw.get("reblog"):
        reblog = raw["reblog"]
        text = html_to_text(reblog.get("content", ""))
        text = f"[Retruth from @{reblog.get('account', {}).get('acct', 'unknown')}]\n\n{text}"

    # ── Extract image ─────────────────────────────────────────────────────────
    image_url = ""
    media = raw.get("media_attachments", [])
    for m in media:
        if m.get("type") in ("image", "gifv"):
            image_url = m.get("url", "")
            break

    # Fallback: card image (link preview thumbnail)
    if not image_url and raw.get("card"):
        card_img = raw["card"].get("image", "")
        if card_img:
            image_url = card_img

    # ── Build normalised post dict ────────────────────────────────────────────
    post_id = raw.get("id", "")
    post_url = raw.get("url", f"https://truthsocial.com/@realDonaldTrump/{post_id}")
    created_at = raw.get("created_at", "")

    # Parse and reformat the timestamp
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        created_at_fmt = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        created_at_fmt = created_at

    return {
        "post_id":    post_id,
        "text":       text,
        "image_url":  image_url,
        "post_url":   post_url,
        "created_at": created_at_fmt,
        "fetched_at": _now(),
    }

# ── File I/O ───────────────────────────────────────────────────────────────────

def load_last_post_id() -> str:
    if os.path.exists(LAST_ID_FILE):
        with open(LAST_ID_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_last_post_id(post_id: str) -> None:
    with open(LAST_ID_FILE, "w", encoding="utf-8") as f:
        f.write(post_id)


def download_image(image_url: str, post_id: str) -> str:
    """Download the post image and return the local relative file path."""
    if not image_url:
        return ""
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Guess extension from URL
    ext = "jpg"
    clean_url = image_url.split("?")[0].lower()
    for candidate in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"):
        if clean_url.endswith(candidate):
            ext = candidate.lstrip(".")
            break

    filename = os.path.join(IMAGES_DIR, f"{post_id}.{ext}")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(image_url, headers=headers, timeout=60)
        resp.raise_for_status()
        with open(filename, "wb") as f:
            f.write(resp.content)
        print(f"[{_now()}] Image saved → {filename}")
        return filename
    except Exception as e:
        print(f"[{_now()}] WARNING: Could not download image: {e}", file=sys.stderr)
        return ""


def _render_post(post: dict, image_path: str) -> str:
    """Render a post dict as a Markdown section string."""
    lines = []
    lines.append(f"\n## Post detected at {post['fetched_at']}\n")
    lines.append(f"**Posted on Truth Social:** {post['created_at']}\n")
    lines.append(f"**Source:** [{post['post_url']}]({post['post_url']})\n")
    lines.append(f"**Post ID:** `{post['post_id']}`\n")
    lines.append("### Text\n")
    lines.append(post["text"] + "\n")
    if image_path:
        lines.append("### Image\n")
        lines.append(f"![Post image]({image_path})\n")
        lines.append(f"*Original URL:* {post['image_url']}\n")
    elif post["image_url"]:
        lines.append("### Image\n")
        lines.append(f"![Post image]({post['image_url']})\n")
    lines.append("\n---")
    return "\n".join(lines)


def save_posts_file(new_post_block: str) -> None:
    """
    Prepend the new post to posts.md and keep only the MAX_POSTS most recent.
    """
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

    # ── Download image (if any) ───────────────────────────────────────────────
    image_path = download_image(post["image_url"], post["post_id"])

    # ── Save to posts.md ──────────────────────────────────────────────────────
    post_block = _render_post(post, image_path)
    save_posts_file(post_block)
    save_last_post_id(post["post_id"])

    print(f"[{_now()}] ── Done ──")


if __name__ == "__main__":
    main()
