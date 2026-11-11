"""
🎨 Telegram Colorful Buttons (Bot API `style` field)
=====================================================
Telegram Bot API এখন বাটনে রঙ সাপোর্ট করে:
    "primary" → 🔵 নীল   (সাধারণ / মেনু অ্যাকশন)
    "success" → 🟢 সবুজ  (কনফার্ম / যোগ / ডাউনলোড / চালু)
    "danger"  → 🔴 লাল   (বাতিল / মুছুন / ব্যান / বন্ধ)

এই মডিউল pyTelegramBotAPI (telebot) এবং python-telegram-bot — দুই লাইব্রেরির
InlineKeyboardButton / KeyboardButton এর `to_dict()` কে র‍্যাপ করে, বাটনের টেক্সট
(ইমোজি + কিওয়ার্ড) দেখে অটোমেটিক রঙ বসিয়ে দেয়। ফলে কোনো বটের হ্যান্ডলার,
callback_data বা বাটন টেক্সট পরিবর্তন করতে হয় না।

ম্যানুয়ালি রঙ দিতে চাইলে:
    telebot : btn = InlineKeyboardButton("..", callback_data=".."); btn.style = "danger"
    PTB     : InlineKeyboardButton("..", callback_data="..", api_kwargs={"style": "danger"})
রঙ বন্ধ রাখতে চাইলে style = "" দিন, অথবা env এ BUTTON_COLORS=off সেট করুন।
"""

import os
import logging

logger = logging.getLogger(__name__)

VALID_STYLES = ("primary", "success", "danger")

# ── নেভিগেশন বাটন (ডিফল্ট/নিউট্রাল রঙেই থাকবে যাতে রঙিন বাটনগুলো আলাদা দেখায়) ──
_NAV_PREFIX = ("🔙", "⬅️", "↩️")
_NAV_EXACT = {"◀️", "▶️", "⬅️", "➡️", "«", "»", "‹", "›"}
_NAV_WORDS = ("ব্যাক", "ফিরুন", "Back")

# ── স্ট্যাটাস আইকন (সবচেয়ে বেশি অগ্রাধিকার) ──
_STATUS_OFF = ("🔴",)
_STATUS_ON = ("🟢",)

# ── লাল (danger) ──
_DANGER_MARKS = ("❌", "🗑", "🚫", "⛔", "🛑", "⏸", "✖")
_DANGER_WORDS = (
    "বাতিল", "মুছুন", "মুছে", "ডিলিট", "সরিয়ে", "বন্ধ করুন", "ব্যান",
    "Cancel", "Delete", "Remove", "Ban", "Stop",
)

# ── সবুজ (success) ──
_SUCCESS_MARKS = ("✅", "➕", "🔓", "📥", "🚀", "💾", "⬇️", "▶️", "✔")
_SUCCESS_WORDS = (
    "যোগ", "একসেপ্ট", "ডাউনলোড", "চালু করুন", "Join", "Finish", "Confirm",
    "Download", "Save", "Accept", "Approve", "সেভ", "পোস্ট করো", "পোস্ট করুন",
)


def detect_style(text):
    """বাটনের টেক্সট দেখে রঙ নির্ধারণ করে। None = ডিফল্ট রঙ।"""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    if not t:
        return None
    # ডিভাইডার / নন-অ্যাকশন বাটন
    if set(t) <= set("─━—-_=·• "):
        return None
    # পেজ নম্বর (যেমন 1/3)
    if all(c.isdigit() or c in "/ " for c in t):
        return None
    # নেভিগেশন
    if t in _NAV_EXACT or t.startswith(_NAV_PREFIX) or any(w in t for w in _NAV_WORDS):
        return None
    # স্ট্যাটাস টগল (🟢/🔴)
    if any(m in t for m in _STATUS_OFF):
        return "danger"
    if any(m in t for m in _STATUS_ON):
        return "success"
    # অ্যাকশন অনুযায়ী
    if any(m in t for m in _DANGER_MARKS) or any(w in t for w in _DANGER_WORDS):
        return "danger"
    if any(m in t for m in _SUCCESS_MARKS) or any(w in t for w in _SUCCESS_WORDS):
        return "success"
    return "primary"


def _apply_style(btn, data):
    """to_dict() এর আউটপুটে style যোগ করে (আগে থেকে থাকলে হাত দেয় না)।"""
    try:
        if not isinstance(data, dict):
            return data
        manual = getattr(btn, "style", None)
        if "style" in data:
            if data["style"] not in VALID_STYLES:
                data.pop("style", None)
            return data
        if manual is not None:
            if manual in VALID_STYLES:
                data["style"] = manual
            return data
        style = detect_style(data.get("text"))
        if style:
            data["style"] = style
    except Exception as e:  # কখনোই বাটন পাঠানো আটকাবে না
        logger.debug(f"button_colors: style apply failed: {e}")
    return data


def _wrap(cls):
    if cls is None or getattr(cls, "_hk_colored", False):
        return False
    original = getattr(cls, "to_dict", None)
    if original is None:
        return False

    def to_dict(self, *args, **kwargs):
        return _apply_style(self, original(self, *args, **kwargs))

    to_dict.__doc__ = original.__doc__
    cls.to_dict = to_dict
    try:
        cls._hk_colored = True
    except Exception:
        pass
    return True


_applied = False


def apply():
    """সব লাইব্রেরিতে রঙিন বাটন চালু করে। একাধিকবার কল করলেও সমস্যা নেই।"""
    global _applied
    if _applied:
        return
    if os.environ.get("BUTTON_COLORS", "on").strip().lower() in ("0", "off", "false", "no"):
        logger.info("🎨 Colorful buttons disabled via BUTTON_COLORS env")
        _applied = True
        return

    patched = []
    # pyTelegramBotAPI (Shortener bot, Web bot)
    try:
        from telebot import types as tb_types
        for name in ("InlineKeyboardButton", "KeyboardButton"):
            if _wrap(getattr(tb_types, name, None)):
                patched.append(f"telebot.{name}")
    except Exception as e:
        logger.debug(f"button_colors: telebot not patched: {e}")

    # python-telegram-bot (Approve bot)
    try:
        import telegram as ptb
        for name in ("InlineKeyboardButton", "KeyboardButton"):
            if _wrap(getattr(ptb, name, None)):
                patched.append(f"ptb.{name}")
    except Exception as e:
        logger.debug(f"button_colors: python-telegram-bot not patched: {e}")

    _applied = True
    if patched:
        logger.info(f"🎨 Colorful buttons enabled: {', '.join(patched)}")
