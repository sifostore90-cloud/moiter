
import os
import re
import json
import time
import threading
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
 
# curl_cffi يقلّد بصمة اتصال متصفح كروم (إنستغرام يحظر بصمة مكتبة requests العادية).
# اختياري: إذا ما انثبّتت المكتبة يشتغل البوت بـ requests العادية.
CFFI_ERROR = ""
try:
    from curl_cffi import requests as cffi_requests
except BaseException as e:
    cffi_requests = None
    CFFI_ERROR = f"{type(e).__name__}: {e}"[:200]
    print("curl_cffi import failed:", CFFI_ERROR)
 
# ============================================================
#                       الإعدادات الثابتة
# ============================================================
API_TOKEN = "9151782620:AAGo3gxIS7I7__VT7emjGLsu0tG5NjZ7kYw"
ADMIN_ID = 8462628086            # المالك (له كل الصلاحيات ويضيف/يحذف الأدمنية)
 
# sessionid افتراضي لحساب إنستغرام ثانوي (تقدر تغيّره من لوحة الأدمن بدون تعديل الملف)
IG_SESSIONID = "14552003496%3A1ZaNMsxYL8QwNm%3A0%3AAYmeOatGf2wJB9__npwPAtvqHJeIvlm6PM03LULn4Q"
 
# بروكسي اختياري لو الـ IP محظور، مثال: "http://user:pass@host:port"
PROXY = ""
 
VERSION = "v9-premium-emoji"
DATA_FILE = "data.json"
DELAY_BETWEEN = 3        # ثواني بين فحص كل حساب
CONFIRM_NEEDED = 2       # لازم يطلع شغال مرتين ورا بعض حتى نأكد الفك
COOLDOWN_SEC = 300       # إذا إنستغرام رجّع 429 نوقف الفحص هذي المدة
COOLDOWN = {"until": 0.0}
LAST = {"info": ""}
START_TIME = time.time()
 
# ألوان الأزرار (Bot API 9.4): primary أزرق، success أخضر، danger أحمر
BLUE, GREEN, RED = "primary", "success", "danger"
 
BAN_TYPES = [
    "حسابات جديده", "حسابات زائفه", "خداع او احتيال",
    "معايير (هيومن)", "حقوق", "اباحي", "هارم",
    "منظمات خطره", "سايبر",
]
 
# وحدات الاشتراك: (الاسم، عدد الثواني)
UNITS = {
    "s": ("ثانية", 1),
    "n": ("دقيقة", 60),
    "h": ("ساعة", 3600),
    "d": ("يوم", 86400),
    "w": ("أسبوع", 7 * 86400),
    "m": ("شهر", 30 * 86400),
    "y": ("سنة", 365 * 86400),
}
MAX_SUB = 10 * 365 * 86400  # أقصى مدة اشتراك: 10 سنين
 
DEFAULT_WELCOME = "✨ مرحباً بك في بوت رصد حسابات إنستغرام المتبندة\n\n📌 اختر من الأزرار أدناه:"
 
SETTINGS = {
    "channel": "i_8rn",         # قناة النتائج (ينشر فيها فك الباند)
    "force_channels": ["i_8rn"],  # قنوات الاشتراك الإجباري
    "welcome": None,            # {"type": ..., "file_id": ...}
    "welcome_text": DEFAULT_WELCOME,
    "gif_once": False,          # True = الـ GIF يطلع للمستخدم أول مرة فقط
    "bot_on": True,             # تشغيل / إيقاف البوت للمستخدمين
    "check_interval": 90,       # ثواني بين كل دورة فحص
    "confirm_needed": 1,        # كم مرة لازم يطلع شغال حتى نبلّغ بالفك (1 أسرع، 2 أدق)
    "ig_session": "",           # sessionid من لوحة الأدمن (يغلب الموجود بالملف)
    "unbans": 0,                # عدد فكوك الباند المرصودة
    "btn_emojis": {},           # إيموجيات مميزة للأزرار {slot: custom_emoji_id}
    "icons_on": True,           # تشغيل/إيقاف الإيموجيات المميزة
}
 
tracked = {}     # "user_id:username" -> info
subs = {}        # str(user_id) -> وقت انتهاء الاشتراك
seen = set()     # من استلم الـ GIF (للوضع "أول مرة فقط")
users = set()    # كل من ضغط /start
admins = set()   # أدمنية إضافيون (غير المالك)
blocked = set()  # المحظورون
user_states = {}
temp_data = {}
sub_cache = {}   # uid -> وقت آخر تحقق ناجح من قنوات الاشتراك
lock = threading.RLock()
 
bot = telebot.TeleBot(API_TOKEN)
 
 
# ============================================================
#                       الحفظ والتحميل
# ============================================================
def save_data():
    with lock:
        data = {
            "settings": SETTINGS,
            "tracked": tracked,
            "subs": subs,
            "seen": list(seen),
            "users": list(users),
            "admins": list(admins),
            "blocked": list(blocked),
        }
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
 
 
def load_data():
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved = data.get("settings", {})
        SETTINGS.update(saved)
        if "force_channels" not in saved:
            SETTINGS["force_channels"] = [SETTINGS["channel"]] if SETTINGS.get("channel") else []
        tracked.update(data.get("tracked", {}))
        subs.update(data.get("subs", {}))
        seen.update(data.get("seen", []))
        users.update(data.get("users", []))
        admins.update(data.get("admins", []))
        blocked.update(data.get("blocked", []))
    except Exception as e:
        print("load_data error:", e)
 
 
def ig_session() -> str:
    return SETTINGS.get("ig_session") or IG_SESSIONID
 
 
# ====== فحص إنستغرام ======
def http_get(url, params, headers, proxies, use_cffi):
    if use_cffi and cffi_requests is not None:
        return cffi_requests.get(url, params=params, headers=headers, timeout=15,
                                 impersonate="chrome", proxies=proxies)
    return requests.get(url, params=params, headers=headers, timeout=15, proxies=proxies)
 
 
def check_via_api(username: str) -> str:
    """يرجع: banned / active / unknown
    unknown = فشل الفحص (حظر مؤقت، انترنت...) ولا يُعتبر فك باند أبداً.
    يجرّب 3 طرق مختلفة لإنستغرام وأي واحدة تعطي جواب واضح نأخذه."""
    sess = ig_session()
    cookie = {"Cookie": f"sessionid={sess}"} if sess else {}
    web_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "x-ig-app-id": "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.instagram.com/{username}/",
        **cookie,
    }
    app_headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; "
                      "samsung; SM-S908B; b0q; qcom; en_US; 458229237)",
        "x-ig-app-id": "567067343352427",
        "Accept-Language": "en-US",
        **cookie,
    }
    page_headers = {
        "User-Agent": web_headers["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        **cookie,
    }
    attempts = [
        ("web-www", f"https://www.instagram.com/api/v1/users/web_profile_info/", {"username": username}, web_headers, "web"),
        ("web-i", f"https://i.instagram.com/api/v1/users/web_profile_info/", {"username": username}, web_headers, "web"),
        ("page", f"https://www.instagram.com/{username}/", None, page_headers, "page"),
        ("app", f"https://i.instagram.com/api/v1/users/{username}/usernameinfo/", None, app_headers, "app"),
    ]
    proxies = {"http": PROXY, "https": PROXY} if PROXY else None
 
    log, codes = [], []
    result = "unknown"
    for name, url, params, headers, kind in attempts:
        try:
            r = http_get(url, params, headers, proxies, use_cffi=(kind in ("web", "page")))
        except Exception as e:
            log.append(f"{name}: error {type(e).__name__}")
            continue
 
        codes.append(r.status_code)
        log.append(f"{name}: HTTP {r.status_code} | {r.text[:80]!r}")
 
        if kind == "page":
            low = r.text.lower()
            if r.status_code == 404:
                result = "banned"
                break
            if r.status_code == 200 and (f'"username":"{username}"' in low or f"(@{username})" in low):
                result = "active"
                break
            continue
 
        if r.status_code == 404:
            body = r.text.lstrip()[:15].lower()
            if body.startswith("<!doctype") or body.startswith("<html"):
                continue  # صفحة 404 عامة من إنستغرام، ليست جواب الـ API عن الحساب
            result = "banned"
            break
        if r.status_code == 200:
            try:
                js = r.json()
            except ValueError:
                continue
            # 200 مع رسالة خطأ (status != ok) ليس جواباً: نتجاهله ونجرّب الطريقة التالية
            if not isinstance(js, dict) or js.get("status") != "ok":
                continue
            if kind == "web":
                user = (js.get("data") or {}).get("user")
            else:
                user = js.get("user")
            result = "active" if user else "banned"
            break
 
    LAST["info"] = f"VERSION: {VERSION}\n" + ("TLS: curl_cffi" if cffi_requests else f"TLS: requests (curl_cffi فشل: {CFFI_ERROR})") + "\n" + "\n".join(log)
    print(f"[API] @{username} -> {result}\n" + LAST["info"])
    if result == "unknown" and codes and all(c == 429 for c in codes):
        COOLDOWN["until"] = time.time() + COOLDOWN_SEC
        print(f"[API] rate limited, pausing checks for {COOLDOWN_SEC}s")
    return result
 
 
CONTROL_USER = "instagram"   # حساب شغال أكيد نستخدمه للتحقق من سلامة الفحص
CONTROL_OK = {"until": 0.0}
 
 
def check_instagram_status(username: str) -> str:
    if time.time() < COOLDOWN["until"]:
        return "unknown"
    res = check_via_api(username)
    if res == "banned" and time.time() > CONTROL_OK["until"]:
        first = LAST["info"]
        ctrl = check_via_api(CONTROL_USER)
        LAST["info"] = first + f"\n--- فحص الحساب الشاهد @{CONTROL_USER}: {ctrl} ---\n" + LAST["info"]
        if ctrl != "active":
            LAST["info"] += "\n⚠️ الفحص غير موثوق حالياً (الحساب الشاهد ما طلع active) فالنتيجة unknown"
            return "unknown"
        CONTROL_OK["until"] = time.time() + 300  # نثق بالفحص 5 دقائق
    return res
 
 
 
# ============================================================
#                       أدوات مساعدة
# ============================================================
def is_owner(uid: int) -> bool:
    return uid == ADMIN_ID
 
 
def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID or uid in admins
 
 
def has_sub(uid: int) -> bool:
    """البوت خاص: الأدمنية أو صاحب اشتراك فعّال فقط"""
    if is_admin(uid):
        return True
    return subs.get(str(uid), 0) > time.time()
 
 
# الأزرار اللي تقدر تحط لها إيموجي مميز من لوحة الأدمن
EMOJI_SLOTS = [
    ("btn_add", "➕ إضافة يوزر"),
    ("btn_list", "📋 قائمة اليوزرات"),
    ("btn_mysub", "💳 اشتراكي"),
    ("admin_panel", "🛠️ لوحة الأدمن"),
    ("adm_stats", "📊 الإحصائيات"),
    ("adm_users", "👥 المستخدمون"),
    ("adm_subs", "💳 الاشتراكات"),
    ("adm_track", "🔍 المراقبة"),
    ("adm_force", "📢 الإجباري"),
    ("adm_bc", "📣 إذاعة"),
    ("adm_welcome", "🎞 الترحيب"),
    ("adm_settings", "⚙️ الإعدادات"),
    ("adm_admins", "👑 الأدمنية"),
]
 
 
def icon_for(slot):
    if not slot or not SETTINGS.get("icons_on", True):
        return None
    return (SETTINGS.get("btn_emojis") or {}).get(slot)
 
 
def strip_lead_emoji(text: str) -> str:
    """نشيل الإيموجي العادي من أول النص إذا راح نحط مكانه إيموجي مميز"""
    parts = text.split(" ", 1)
    if len(parts) == 2 and not any(ch.isalnum() for ch in parts[0]):
        return parts[1]
    return text
 
 
def B(text, cb=None, url=None, style=None, slot=None, icon=None):
    """زر ملوّن (style) وبإيموجي مميز اختياري (icon_custom_emoji_id).
    إذا نسخة المكتبة ما تدعم الحقول نضيفها يدوياً بالـ to_dict."""
    icon = icon or icon_for(slot)
    orig_text = text
    if icon:
        text = strip_lead_emoji(text)
    extras = {k: v for k, v in (("style", style), ("icon_custom_emoji_id", icon)) if v}
    try:
        b = InlineKeyboardButton(text, url=url, callback_data=cb, **extras)
    except TypeError:
        b = InlineKeyboardButton(text, url=url, callback_data=cb)
        if extras:
            try:
                orig = b.to_dict
                b.to_dict = lambda: {**orig(), **extras}
            except Exception:
                pass
    b._orig_text, b._icon, b._extras = orig_text, icon, extras
    return b
 
 
def strip_icons(markup) -> bool:
    """يشيل الإيموجيات المميزة من لوحة أزرار (إذا تلجرام رفضها). يرجع True إذا كان بيها."""
    changed = False
    for row in getattr(markup, "keyboard", None) or []:
        for b in row:
            if getattr(b, "_icon", None):
                changed = True
                b._icon = None
                b.text = b._orig_text
                if hasattr(b, "icon_custom_emoji_id"):
                    b.icon_custom_emoji_id = None
                ex = getattr(b, "_extras", None)
                if ex:
                    ex.pop("icon_custom_emoji_id", None)
    return changed
 
 
RAW = {}
 
 
def on_icons_failed(err):
    if SETTINGS.get("icons_on", True):
        SETTINGS["icons_on"] = False
        save_data()
        try:
            RAW["send_message"](ADMIN_ID,
                                "⚠️ تلجرام رفض الإيموجيات المميزة (غالباً اشتراك Premium انتهى)، "
                                "فعطّلتها تلقائياً والبوت يشتغل بدونها.\n"
                                "ترجعها من: الإعدادات ← إيموجي مميزة.\n\n" + str(err)[:150])
        except Exception:
            pass
 
 
def install_icon_safety():
    """إذا فشل إرسال رسالة بسبب إيموجي مميز، نعيد الإرسال بدونه حتى ما يتعطل البوت"""
    for name in ("send_message", "send_animation", "send_photo", "send_video",
                 "send_document", "edit_message_text", "edit_message_caption"):
        orig = getattr(bot, name)
        RAW[name] = orig
 
        def make(orig):
            def wrapper(*a, **k):
                try:
                    return orig(*a, **k)
                except Exception as e:
                    mk = k.get("reply_markup")
                    if "not modified" in str(e).lower() or mk is None or not strip_icons(mk):
                        raise
                    r = orig(*a, **k)   # إذا فشلت مرة ثانية يطلع الخطأ الأصلي
                    on_icons_failed(e)
                    return r
            return wrapper
        setattr(bot, name, make(orig))
 
 
def test_icon(chat_id, emoji_id):
    """نجرّب إرسال زر بالإيموجي قبل ما نحفظه"""
    kb = InlineKeyboardMarkup()
    kb.add(B("اختبار", "noop", icon=emoji_id))
    try:
        m = RAW["send_message"](chat_id, "🧪 اختبار الإيموجي المميز", reply_markup=kb)
        try:
            bot.delete_message(chat_id, m.message_id)
        except Exception:
            pass
        return True, ""
    except Exception as e:
        return False, str(e)[:200]
 
 
def back_kb(cb="btn_back", label="🔙 رجوع"):
    kb = InlineKeyboardMarkup()
    kb.add(B(label, cb))
    return kb
 
 
def fmt_left(sec: float) -> str:
    sec = max(int(sec), 0)
    d, h, m, x = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60, sec % 60
    if d:
        return f"{d} يوم و {h} ساعة"
    if h:
        return f"{h} ساعة و {m} دقيقة"
    if m:
        return f"{m} دقيقة و {x} ثانية"
    return f"{x} ثانية"
 
 
def fmt_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
 
 
def parse_channel(t):
    t = (t or "").strip()
    for p in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        t = t.replace(p, "")
    t = t.split("/")[0].split("?")[0]
    return t if re.fullmatch(r"[A-Za-z0-9_]{4,32}", t) else None
 
 
def bot_is_admin_in(ch: str):
    try:
        me = bot.get_me()
        m = bot.get_chat_member(f"@{ch}", me.id)
        return m.status in ("administrator", "creator"), ""
    except Exception as e:
        return False, str(e)[:150]
 
 
def missing_channels(uid: int, use_cache: bool = True):
    """القنوات الإجبارية اللي المستخدم مو مشترك بيها"""
    if is_admin(uid):
        return []
    if use_cache and time.time() - sub_cache.get(uid, 0) < 60:
        return []
    out = []
    for ch in SETTINGS.get("force_channels", []):
        try:
            m = bot.get_chat_member(f"@{ch}", uid)
            ok = m.status in ("creator", "administrator", "member") or \
                (m.status == "restricted" and getattr(m, "is_member", False))
            if not ok:
                out.append(ch)
        except Exception as e:
            # ما نقدر نتحقق (البوت مو أدمن بالقناة مثلاً): نتجاوزها حتى ما ينقفل البوت على الكل
            print(f"[force] cannot verify @{ch}: {str(e)[:100]}")
    if not out:
        sub_cache[uid] = time.time()
    return out
 
 
def access_denied(uid: int):
    """نص الرفض إذا المستخدم ما يحق له الاستخدام، وإلا None"""
    if is_admin(uid):
        return None
    if uid in blocked:
        return "🚫 تم حظرك من استخدام البوت."
    if not SETTINGS.get("bot_on", True):
        return "🔧 البوت متوقف مؤقتاً للصيانة، حاول لاحقاً."
    if not has_sub(uid):
        return ("⛔ هذا البوت خاص بالمشتركين فقط.\n"
                f"للاشتراك تواصل مع الأدمن وأرسل له آيديك:\n\n🆔 {uid}")
    return None
 
 
def revoke_user(uid: int, notify_text: str):
    """طرد مستخدم: يحذف اشتراكه ويوقف كل حساباته المراقبة ويبلغه"""
    subs.pop(str(uid), None)
    stop_tracking(uid)
    user_states.pop(uid, None)
    temp_data.pop(uid, None)
    temp_data.pop(f"{uid}:unchecked", None)
    save_data()
    try:
        bot.send_message(uid, notify_text)
    except Exception:
        pass
 
 
def stop_tracking(uid: int):
    with lock:
        for k in [k for k, v in tracked.items() if v["chat_id"] == uid]:
            tracked.pop(k, None)
 
 
def edit_or_send(chat_id, message_id, text, markup=None):
    """يعدّل الرسالة (نصية أو GIF/صورة مع كابشن) وإذا ما أمكن يرسل جديدة"""
    def not_modified(e):
        return "not modified" in str(e).lower()
 
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
        return
    except Exception as e:
        if not_modified(e):
            return
    if len(text) <= 1024:
        try:
            bot.edit_message_caption(text, chat_id, message_id, reply_markup=markup)
            return
        except Exception as e:
            if not_modified(e):
                return
    bot.send_message(chat_id, text, reply_markup=markup)
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass
 
 
def send_media(chat_id, w, caption=None, markup=None):
    t, fid = w["type"], w["file_id"]
    if t == "animation":
        return bot.send_animation(chat_id, fid, caption=caption, reply_markup=markup)
    if t == "photo":
        return bot.send_photo(chat_id, fid, caption=caption, reply_markup=markup)
    if t == "video":
        return bot.send_video(chat_id, fid, caption=caption, reply_markup=markup)
    return bot.send_document(chat_id, fid, caption=caption, reply_markup=markup)
 
 
# ============================================================
#                         الواجهات
# ============================================================
def welcome_text() -> str:
    return SETTINGS.get("welcome_text") or DEFAULT_WELCOME
 
 
def main_keyboard(uid: int):
    kb = InlineKeyboardMarkup()
    if is_admin(uid):
        kb.add(B("🛠️ لوحة تحكم الأدمن", "admin_panel", style=RED, slot="admin_panel"))
    kb.add(B("➕ إضافة يوزر للمراقبة", "btn_add", style=GREEN, slot="btn_add"))
    kb.add(B("📋 قائمة اليوزرات المراقبة", "btn_list", style=BLUE, slot="btn_list"))
    if not is_admin(uid):
        kb.add(B("💳 اشتراكي", "btn_mysub", slot="btn_mysub"))
    return kb
 
 
def send_main_menu(chat_id: int, uid: int):
    """القائمة الرئيسية: الـ GIF + النص + الأزرار برسالة وحدة"""
    text, kb = welcome_text(), main_keyboard(uid)
    w = SETTINGS.get("welcome")
    once_skip = SETTINGS.get("gif_once") and uid in seen and not is_admin(uid)
    if w and not once_skip:
        try:
            send_media(chat_id, w, caption=text[:1024], markup=kb)
            if uid not in seen:
                seen.add(uid)
                save_data()
            return
        except Exception as e:
            print("welcome media error:", e)
    bot.send_message(chat_id, text, reply_markup=kb)
 
 
def send_start(chat_id: int, uid: int):
    user_states.pop(uid, None)
    if uid not in users:
        users.add(uid)
        save_data()
 
    denied = access_denied(uid)
    if denied:
        bot.send_message(chat_id, denied)
        return
 
    missing = missing_channels(uid, use_cache=False)
    if missing:
        kb = InlineKeyboardMarkup()
        for ch in missing:
            kb.add(B(f"📢 اشترك في @{ch}", url=f"https://t.me/{ch}", style=BLUE))
        kb.add(B("🔄 تحقق من الاشتراك", "check_sub", style=GREEN))
        bot.send_message(chat_id,
                         "⚠️ لازم تشترك بالقنوات التالية حتى تستخدم البوت:\n\n"
                         + "\n".join(f"• @{c}" for c in missing),
                         reply_markup=kb)
        return
 
    send_main_menu(chat_id, uid)
 
 
def active_subs_count() -> int:
    now = time.time()
    return sum(1 for e in subs.values() if e > now)
 
 
def stats_text() -> str:
    now = time.time()
    return (
        "📊 الإحصائيات\n\n"
        f"👥 المستخدمون: {len(users)}\n"
        f"💳 المشتركون الفعّالون: {active_subs_count()}\n"
        f"🔍 حسابات قيد المراقبة: {len(tracked)}\n"
        f"✅ فكوك باند تم رصدها: {SETTINGS.get('unbans', 0)}\n"
        f"🚫 المحظورون: {len(blocked)}\n"
        f"👑 الأدمنية: {len(admins) + 1}\n"
        f"📢 قنوات إجبارية: {len(SETTINGS.get('force_channels', []))}\n"
        f"⏱ مدة التشغيل: {fmt_left(now - START_TIME)}\n"
        f"🤖 الإصدار: {VERSION}"
    )
 
 
# ---------- شاشات لوحة الأدمن ----------
def admin_panel_view(chat_id, mid, uid):
    kb = InlineKeyboardMarkup()
    kb.row(B("📊 الإحصائيات", "adm_stats", style=BLUE, slot="adm_stats"), B("👥 المستخدمون", "adm_users", style=BLUE, slot="adm_users"))
    kb.row(B("💳 الاشتراكات", "adm_subs", style=GREEN, slot="adm_subs"), B("🔍 المراقبة", "adm_track", style=BLUE, slot="adm_track"))
    kb.row(B("📢 الاشتراك الإجباري", "adm_force", style=GREEN, slot="adm_force"), B("📣 إذاعة", "adm_bc", style=BLUE, slot="adm_bc"))
    kb.row(B("🎞 الترحيب والـ GIF", "adm_welcome", slot="adm_welcome"), B("⚙️ الإعدادات", "adm_settings", slot="adm_settings"))
    if is_owner(uid):
        kb.add(B("👑 الأدمنية", "adm_admins", style=RED, slot="adm_admins"))
    kb.add(B("🔙 القائمة الرئيسية", "btn_back"))
    edit_or_send(chat_id, mid,
                 "🛠️ لوحة التحكم الكاملة\n\n"
                 f"🤖 حالة البوت: {'يعمل ✅' if SETTINGS.get('bot_on', True) else 'متوقف ⏸'}\n"
                 f"👥 المستخدمون: {len(users)}  |  💳 المشتركون: {active_subs_count()}\n"
                 f"🔍 قيد المراقبة: {len(tracked)}",
                 kb)
 
 
def subs_menu_view(chat_id, mid):
    kb = InlineKeyboardMarkup()
    kb.add(B("➕ منح / تمديد اشتراك", "adm_sub_add", style=GREEN))
    kb.add(B("📋 قائمة المشتركين", "adm_sub_list", style=BLUE))
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid,
                 "💳 قائمة الاشتراكات\n\n"
                 "🔒 البوت خاص: لا يستخدمه إلا المشتركون.\n"
                 f"المشتركون الفعّالون: {active_subs_count()}",
                 kb)
 
 
def users_menu_view(chat_id, mid):
    kb = InlineKeyboardMarkup()
    kb.row(B("🚫 حظر مستخدم", "adm_ban", style=RED), B("📋 المحظورون", "adm_banlist"))
    kb.add(B("🔎 معلومات مستخدم", "adm_uinfo", style=BLUE))
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid,
                 f"👥 المستخدمون\n\nالعدد الكلي: {len(users)}\nالمحظورون: {len(blocked)}", kb)
 
 
def banlist_view(chat_id, mid):
    kb = InlineKeyboardMarkup()
    if blocked:
        text = "🚫 المحظورون:\n\n" + "\n".join(f"• {u}" for u in list(blocked)[:30])
        for u in list(blocked)[:30]:
            kb.add(B(f"✅ فك حظر {u}", f"adm_unban_{u}", style=GREEN))
    else:
        text = "❌ لا يوجد محظورون."
    kb.add(B("🔙 رجوع", "adm_users"))
    edit_or_send(chat_id, mid, text, kb)
 
 
def track_view(chat_id, mid):
    with lock:
        items = list(tracked.items())
    kb = InlineKeyboardMarkup()
    if not items:
        text = "❌ لا توجد حسابات قيد المراقبة."
    else:
        text = f"🔍 كل الحسابات قيد المراقبة ({len(items)}):\n\n"
        for key, info in items[:20]:
            text += (f"• @{info['username']} | 👤 {info['chat_id']} | {info['ban_type']} | "
                     f"{fmt_left(time.time() - info['start_time'])}\n")
            kb.add(B(f"🗑 إيقاف @{info['username']} ({info['chat_id']})", f"adm_tdel_{key}", style=RED))
        if len(items) > 20:
            text += f"\n... و {len(items) - 20} غيرها"
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid, text, kb)
 
 
def force_view(chat_id, mid):
    chs = SETTINGS.get("force_channels", [])
    kb = InlineKeyboardMarkup()
    if chs:
        text = "📢 قنوات الاشتراك الإجباري:\n\n" + "\n".join(f"• @{c}" for c in chs)
        for c in chs:
            kb.add(B(f"🗑 حذف @{c}", f"adm_fdel_{c}", style=RED))
    else:
        text = "📢 الاشتراك الإجباري متوقف (ما في قنوات)."
    text += "\n\n⚠️ لازم البوت يكون أدمن بالقناة حتى يتحقق من المشتركين."
    kb.add(B("➕ إضافة قناة", "adm_fadd", style=GREEN))
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid, text, kb)
 
 
def welcome_view(chat_id, mid):
    kb = InlineKeyboardMarkup()
    kb.row(B("🎞 تغيير الـ GIF", "adm_gif", style=GREEN), B("🗑 حذف الـ GIF", "adm_gif_del", style=RED))
    kb.add(B("✏️ تغيير نص الترحيب", "adm_wtext", style=BLUE))
    kb.add(B(f"🔁 الـ GIF يطلع: {'أول مرة فقط' if SETTINGS.get('gif_once') else 'كل مرة'}", "adm_gifonce"))
    kb.add(B("👁 معاينة", "adm_wpreview"))
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid,
                 "🎞 الترحيب والـ GIF\n\n"
                 f"الـ GIF: {'موجود ✅' if SETTINGS.get('welcome') else 'غير محدد'}\n"
                 f"النص الحالي:\n{welcome_text()[:300]}",
                 kb)
 
 
def settings_view(chat_id, mid):
    on = SETTINGS.get("bot_on", True)
    kb = InlineKeyboardMarkup()
    kb.row(B("📢 قناة النتائج", "adm_channel"), B("⏱ فترة الفحص", "adm_interval"))
    kb.row(B("🔑 تغيير sessionid", "adm_session"), B("🧪 فحص يوزر", "adm_test", style=BLUE))
    kb.add(B("✨ إيموجيات مميزة", "adm_emoji", style=BLUE))
    conf = int(SETTINGS.get("confirm_needed", 1))
    kb.add(B(f"✔️ تأكيد الفك: {'مرة وحدة (أسرع)' if conf == 1 else 'مرتين (أدق)'}", "adm_confirm", style=BLUE))
    kb.add(B("⏸ إيقاف البوت" if on else "▶️ تشغيل البوت", "adm_toggle", style=RED if on else GREEN))
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid,
                 "⚙️ الإعدادات\n\n"
                 f"📢 قناة النتائج: @{SETTINGS.get('channel')}\n"
                 f"⏱ فترة الفحص: {SETTINGS.get('check_interval', 90)} ثانية\n"
                 f"✔️ تبليغ الفك بعد: {int(SETTINGS.get('confirm_needed', 1))} نتيجة شغّال\n"
                 f"🔑 sessionid: {'مخصص من اللوحة' if SETTINGS.get('ig_session') else 'من الملف'}\n"
                 f"🤖 حالة البوت: {'يعمل' if on else 'متوقف'}",
                 kb)
 
 
def emoji_view(chat_id, mid):
    em = SETTINGS.setdefault("btn_emojis", {})
    on = SETTINGS.get("icons_on", True)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(*[B(("✅ " if sl in em else "") + lab, f"adm_eslot_{i}") for i, (sl, lab) in enumerate(EMOJI_SLOTS)])
    kb.add(B("🔎 جيب ID إيموجي", "adm_eid", style=BLUE))
    kb.add(B("⏸ إيقاف الإيموجيات المميزة" if on else "▶️ تشغيل الإيموجيات المميزة", "adm_eton",
             style=RED if on else GREEN))
    kb.add(B("🗑 حذف كل الإيموجيات", "adm_eclear", style=RED))
    kb.add(B("🔙 رجوع", "adm_settings"))
    edit_or_send(chat_id, mid,
                 "✨ إيموجيات الأزرار المميزة\n\n"
                 f"الحالة: {'شغّالة' if on else 'متوقفة'}  |  المضافة: {len(em)}\n\n"
                 "اضغط على الزر اللي تريده وأرسل له إيموجي Premium (المتحرك).\n"
                 "⚠️ يشتغل فقط إذا حساب مالك البوت عنده Telegram Premium.",
                 kb)
 
 
def admins_view(chat_id, mid):
    kb = InlineKeyboardMarkup()
    text = f"👑 الأدمنية\n\nالمالك: {ADMIN_ID}\n"
    if admins:
        text += "\nالأدمنية المضافون:\n" + "\n".join(f"• {a}" for a in admins)
        for a in list(admins):
            kb.add(B(f"🗑 حذف {a}", f"adm_admin_del_{a}", style=RED))
    else:
        text += "\nلا يوجد أدمنية إضافيون."
    kb.add(B("➕ إضافة أدمن", "adm_admin_add", style=GREEN))
    kb.add(B("🔙 رجوع", "admin_panel"))
    edit_or_send(chat_id, mid, text, kb)
 
 
def run_test_text(username: str) -> str:
    COOLDOWN["until"] = 0.0
    CONTROL_OK["until"] = 0.0
    status = check_instagram_status(username)
    return f"النتيجة: {status}\n\n{LAST['info']}"
 
 
def run_broadcast(targets, from_chat, msg_id, report_chat):
    ok = fail = 0
    for u in targets:
        try:
            bot.copy_message(u, from_chat, msg_id)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    try:
        bot.send_message(report_chat, f"📣 انتهت الإذاعة\n\n✅ وصلت: {ok}\n❌ فشلت: {fail}")
    except Exception:
        pass
 
 
install_icon_safety()
 
 
# ============================================================
#                          الأوامر
# ============================================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.chat.type != "private":
        return
    send_start(message.chat.id, message.from_user.id)
 
 
@bot.message_handler(commands=["id"])
def cmd_id(message):
    bot.reply_to(message, f"🆔 آيديك: {message.from_user.id}")
 
 
@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if message.chat.type != "private" or not is_admin(message.from_user.id):
        return
    m = bot.send_message(message.chat.id, "⏳")
    admin_panel_view(message.chat.id, m.message_id, message.from_user.id)
 
 
@bot.message_handler(commands=["test"])
def cmd_test(message):
    """للأدمن فقط: /test username"""
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        bot.reply_to(message, "الاستخدام: /test username")
        return
    bot.reply_to(message, run_test_text(parts[1].replace("@", "").lower()))
 
 
# ============================================================
#                      ضغطات الأزرار
# ============================================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    mid = call.message.message_id
    try:
        handle_callback(call, uid, chat_id, mid, call.data)
    except Exception as e:
        print("callback error:", repr(e))
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
 
 
def handle_callback(call, uid, chat_id, mid, data):
    def ans(text=None, alert=False):
        try:
            bot.answer_callback_query(call.id, text, show_alert=alert)
        except Exception:
            pass
 
    # ----- بوابات الدخول -----
    denied = access_denied(uid)
    if denied and not (data == "btn_mysub" and uid not in blocked):
        return ans(denied.split("\n")[0], True)
 
    if data == "check_sub":
        if not missing_channels(uid, use_cache=False):
            ans("✅ تم التحقق بنجاح!")
            send_start(chat_id, uid)
        else:
            ans("❌ لم تشترك بكل القنوات بعد!", True)
        return
 
    if data != "btn_mysub" and missing_channels(uid):
        return ans("⚠️ اشترك بقنوات البوت أولاً ثم اضغط /start", True)
 
    if data.startswith(("admin_", "adm_", "sub_")) and not is_admin(uid):
        return ans("هذا الزر للأدمن فقط!", True)
    if data.startswith("adm_admin") and not is_owner(uid):
        return ans("هذا الزر للمالك فقط!", True)
 
    if data.startswith(("admin_", "adm_", "sub_")):
        return admin_callback(call, uid, chat_id, mid, data, ans)
 
    # ----- أزرار المستخدم -----
    if data == "btn_back":
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        if SETTINGS.get("welcome") and getattr(call.message, "content_type", "text") == "text":
            try:
                bot.delete_message(chat_id, mid)
            except Exception:
                pass
            send_main_menu(chat_id, uid)
        else:
            edit_or_send(chat_id, mid, welcome_text(), main_keyboard(uid))
        ans()
 
    elif data == "btn_mysub":
        exp = subs.get(str(uid), 0)
        if exp > time.time():
            text = f"💳 اشتراكك فعّال\n⏳ المتبقي: {fmt_left(exp - time.time())}\n📅 ينتهي: {fmt_date(exp)}"
        else:
            text = f"❌ ليس لديك اشتراك فعّال.\nتواصل مع الأدمن لتفعيله.\n🆔 آيديك: {uid}"
        kb = back_kb() if has_sub(uid) else InlineKeyboardMarkup()
        edit_or_send(chat_id, mid, text, kb)
        ans()
 
    elif data == "btn_add":
        user_states[uid] = "waiting_username"
        edit_or_send(chat_id, mid,
                     "👤 أرسل الآن يوزر الحساب المراد مراقبته:\n(مثال: username أو @username)",
                     back_kb())
        ans()
 
    elif data == "btn_list":
        with lock:
            mine = {k: v for k, v in tracked.items() if v["chat_id"] == uid}
        kb = InlineKeyboardMarkup()
        if not mine:
            text = "❌ لا توجد حسابات قيد المراقبة حالياً."
        else:
            text = "📋 الحسابات قيد الرصد:\n\n"
            for key, info in mine.items():
                mins = int((time.time() - info["start_time"]) // 60)
                text += f"• @{info['username']}\n  النوع: {info['ban_type']}\n  المدة: {mins} دقيقة\n\n"
                kb.add(B(f"🗑 إيقاف مراقبة @{info['username']}", f"del_{key}", style=RED))
        kb.add(B("🔙 رجوع", "btn_back"))
        edit_or_send(chat_id, mid, text, kb)
        ans()
 
    elif data.startswith("del_"):
        key = data[4:]
        with lock:
            info = tracked.get(key)
            ok = bool(info and info["chat_id"] == uid)
            if ok:
                tracked.pop(key, None)
        if ok:
            save_data()
        ans("✅ تم الإيقاف" if ok else "غير موجود")
        edit_or_send(chat_id, mid, "✅ تم إيقاف المراقبة." if ok else "⚠️ الحساب غير موجود.", back_kb())
 
    elif data.startswith("type_"):
        try:
            ban_type = BAN_TYPES[int(data[5:])]
        except (ValueError, IndexError):
            return ans()
 
        username = temp_data.get(uid)
        if not isinstance(username, str):
            return ans("⚠️ انتهت الجلسة، أعد المحاولة.", True)
 
        unchecked = bool(temp_data.pop(f"{uid}:unchecked", False))
        key = f"{uid}:{username}"
        with lock:
            tracked[key] = {
                "username": username,
                "start_time": time.time(),
                "ban_type": ban_type,
                "chat_id": uid,
                "active_hits": 0,
                "seen_banned": not unchecked,
            }
        save_data()
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        edit_or_send(chat_id, mid,
                     f"✅ تمت إضافة الحساب للمراقبة!\n\n👤 @{username}\n📌 النوع: {ban_type}\n\n🔍 سأعلمك فور فكه.",
                     back_kb("btn_back", "🔙 القائمة الرئيسية"))
        ans("✅ تمت الإضافة!")
    else:
        ans()
 
 
def admin_callback(call, uid, chat_id, mid, data, ans):
    # ---------- اللوحة الرئيسية ----------
    if data == "admin_panel":
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        admin_panel_view(chat_id, mid, uid)
        return ans()
 
    if data == "adm_stats":
        edit_or_send(chat_id, mid, stats_text(), back_kb("admin_panel"))
        return ans()
 
    # ---------- المستخدمون ----------
    if data == "adm_users":
        user_states.pop(uid, None)
        users_menu_view(chat_id, mid)
        return ans()
 
    if data == "adm_ban":
        user_states[uid] = "ban_wait_id"
        edit_or_send(chat_id, mid, "🚫 أرسل آيدي المستخدم اللي تريد تحظره (أرقام فقط):", back_kb("adm_users"))
        return ans()
 
    if data == "adm_banlist":
        banlist_view(chat_id, mid)
        return ans()
 
    if data.startswith("adm_unban_"):
        try:
            blocked.discard(int(data[len("adm_unban_"):]))
            save_data()
        except ValueError:
            pass
        banlist_view(chat_id, mid)
        return ans("✅ تم فك الحظر")
 
    if data == "adm_uinfo":
        user_states[uid] = "uinfo_wait_id"
        edit_or_send(chat_id, mid, "🔎 أرسل آيدي المستخدم:", back_kb("adm_users"))
        return ans()
 
    # ---------- الاشتراكات ----------
    if data == "adm_subs":
        user_states.pop(uid, None)
        subs_menu_view(chat_id, mid)
        return ans()
 
    if data == "adm_sub_add":
        user_states[uid] = "sub_wait_id"
        temp_data.pop(uid, None)
        edit_or_send(chat_id, mid,
                     "🆔 أرسل آيدي المستخدم (أرقام فقط).\nالمستخدم يقدر يعرف آيديه بإرسال /id للبوت.",
                     back_kb("adm_subs"))
        return ans()
 
    if data.startswith("sub_unit_"):
        unit = data[len("sub_unit_"):]
        info = temp_data.get(uid) or {}
        if not isinstance(info, dict) or unit not in UNITS or "sub_uid" not in info or "sub_num" not in info:
            return ans("⚠️ انتهت الجلسة، أعد المحاولة.", True)
 
        label, secs = UNITS[unit]
        num, target = info["sub_num"], info["sub_uid"]
        if num * secs > MAX_SUB:
            return ans("⚠️ المدة طويلة جداً (الحد 10 سنين).", True)
        key = str(target)
        base = max(subs.get(key, 0), time.time())
        subs[key] = base + num * secs
        save_data()
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        edit_or_send(chat_id, mid,
                     f"✅ تم منح الاشتراك\n\n🆔 المستخدم: {target}\n⏱ المدة: {num} {label}\n📅 ينتهي: {fmt_date(subs[key])}",
                     back_kb("adm_subs", "🔙 قائمة الاشتراكات"))
        try:
            bot.send_message(target, f"✅ تم تفعيل اشتراكك لمدة {num} {label}.\n📅 ينتهي: {fmt_date(subs[key])}\n\nاضغط /start للبدء.")
        except Exception:
            pass
        return ans("✅ تم")
 
    if data == "adm_sub_list":
        now = time.time()
        kb = InlineKeyboardMarkup()
        lines = []
        for u, exp in sorted(subs.items(), key=lambda x: -x[1])[:30]:
            if exp > now:
                lines.append(f"• {u} — باقي {fmt_left(exp - now)}")
                kb.add(B(f"🗑 إلغاء اشتراك {u}", f"sub_del_{u}", style=RED))
        text = ("📋 المشتركون الفعّالون:\n\n" + "\n".join(lines)) if lines else "❌ لا يوجد مشتركون فعّالون."
        kb.add(B("🔙 رجوع", "adm_subs"))
        edit_or_send(chat_id, mid, text, kb)
        return ans()
 
    if data.startswith("sub_del_"):
        try:
            revoke_user(int(data[len("sub_del_"):]), "⛔ تم إلغاء اشتراكك من قبل الأدمن.")
        except ValueError:
            pass
        subs_menu_view(chat_id, mid)
        return ans("✅ تم الإلغاء")
 
    # ---------- المراقبة ----------
    if data == "adm_track":
        track_view(chat_id, mid)
        return ans()
 
    if data.startswith("adm_tdel_"):
        with lock:
            tracked.pop(data[len("adm_tdel_"):], None)
        save_data()
        track_view(chat_id, mid)
        return ans("✅ تم الإيقاف")
 
    # ---------- الاشتراك الإجباري ----------
    if data == "adm_force":
        user_states.pop(uid, None)
        force_view(chat_id, mid)
        return ans()
 
    if data == "adm_fadd":
        user_states[uid] = "force_wait_ch"
        edit_or_send(chat_id, mid,
                     "📢 أرسل يوزر القناة (مثال: @mychannel).\n\n"
                     "⚠️ لازم تضيف البوت **أدمن** بالقناة أولاً، والقناة عامة (لها يوزر).",
                     back_kb("adm_force"))
        return ans()
 
    if data.startswith("adm_fdel_"):
        ch = data[len("adm_fdel_"):]
        chs = SETTINGS.get("force_channels", [])
        if ch in chs:
            chs.remove(ch)
            sub_cache.clear()
            save_data()
        force_view(chat_id, mid)
        return ans("✅ تم الحذف")
 
    # ---------- الإذاعة ----------
    if data == "adm_bc":
        user_states[uid] = "bc_wait_msg"
        edit_or_send(chat_id, mid,
                     "📣 أرسل الآن الرسالة اللي تريد إذاعتها (نص / صورة / GIF / فيديو ...).",
                     back_kb("admin_panel"))
        return ans()
 
    if data in ("adm_bc_go_all", "adm_bc_go_subs"):
        info = temp_data.get(uid)
        if not isinstance(info, dict) or "bc" not in info:
            return ans("⚠️ انتهت الجلسة، أعد المحاولة.", True)
        from_chat, msg_id = info["bc"]
        if data == "adm_bc_go_subs":
            targets = [u for u in users if u not in blocked and has_sub(u)]
        else:
            targets = [u for u in users if u not in blocked]
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        edit_or_send(chat_id, mid, f"⏳ جاري الإرسال إلى {len(targets)} مستخدم...", back_kb("admin_panel"))
        threading.Thread(target=run_broadcast, args=(targets, from_chat, msg_id, chat_id), daemon=True).start()
        return ans()
 
    if data == "adm_bc_cancel":
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        admin_panel_view(chat_id, mid, uid)
        return ans("تم الإلغاء")
 
    # ---------- الترحيب والـ GIF ----------
    if data == "adm_welcome":
        user_states.pop(uid, None)
        welcome_view(chat_id, mid)
        return ans()
 
    if data == "adm_gif":
        user_states[uid] = "waiting_gif"
        edit_or_send(chat_id, mid,
                     "🎞 أرسل الآن الـ GIF (أو صورة / فيديو) اللي تريده يطلع مع القائمة الرئيسية.",
                     back_kb("adm_welcome"))
        return ans()
 
    if data == "adm_gif_del":
        SETTINGS["welcome"] = None
        seen.clear()
        save_data()
        welcome_view(chat_id, mid)
        return ans("✅ تم الحذف")
 
    if data == "adm_gifonce":
        SETTINGS["gif_once"] = not SETTINGS.get("gif_once")
        seen.clear()
        save_data()
        welcome_view(chat_id, mid)
        return ans("✅ تم")
 
    if data == "adm_wtext":
        user_states[uid] = "wtext_wait"
        edit_or_send(chat_id, mid,
                     "✏️ أرسل نص الترحيب الجديد (أقصى 1000 حرف).\nلإرجاع النص الافتراضي أرسل: -",
                     back_kb("adm_welcome"))
        return ans()
 
    if data == "adm_wpreview":
        send_main_menu(chat_id, uid)
        return ans()
 
    # ---------- الإعدادات ----------
    if data == "adm_settings":
        user_states.pop(uid, None)
        settings_view(chat_id, mid)
        return ans()
 
    if data == "adm_channel":
        user_states[uid] = "waiting_new_channel"
        edit_or_send(chat_id, mid, "📢 أرسل يوزر قناة النتائج الجديدة (مثال: @mychannel):", back_kb("adm_settings"))
        return ans()
 
    if data == "adm_interval":
        user_states[uid] = "interval_wait"
        edit_or_send(chat_id, mid,
                     "⏱ أرسل فترة الفحص بالثواني (من 30 إلى 3600).\nكل ما قلّت الفترة زاد احتمال حظر إنستغرام.",
                     back_kb("adm_settings"))
        return ans()
 
    if data == "adm_session":
        user_states[uid] = "session_wait"
        edit_or_send(chat_id, mid,
                     "🔑 أرسل الـ sessionid الجديد لحساب إنستغرام الثانوي.\n"
                     "(راح أحذف رسالتك بعد ما أحفظه.)\nلإرجاع القيمة الموجودة بالملف أرسل: -",
                     back_kb("adm_settings"))
        return ans()
 
    if data == "adm_test":
        user_states[uid] = "test_wait"
        edit_or_send(chat_id, mid, "🧪 أرسل يوزر الحساب اللي تريد تفحصه:", back_kb("adm_settings"))
        return ans()
 
    if data == "adm_confirm":
        SETTINGS["confirm_needed"] = 2 if int(SETTINGS.get("confirm_needed", 1)) == 1 else 1
        save_data()
        settings_view(chat_id, mid)
        return ans("✅ تم")
 
    if data == "adm_emoji":
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        emoji_view(chat_id, mid)
        return ans()
 
    if data.startswith("adm_eslot_"):
        try:
            slot, label = EMOJI_SLOTS[int(data[len("adm_eslot_"):])]
        except (ValueError, IndexError):
            return ans()
        user_states[uid] = "emoji_wait"
        temp_data[uid] = {"eslot": slot}
        kb = InlineKeyboardMarkup()
        if slot in (SETTINGS.get("btn_emojis") or {}):
            kb.add(B("🗑 إزالة إيموجي هذا الزر", f"adm_edel_{data[len('adm_eslot_'):]}", style=RED))
        kb.add(B("🔙 رجوع", "adm_emoji"))
        edit_or_send(chat_id, mid,
                     f"✨ الزر: {label}\n\nأرسل الآن إيموجي Premium واحد (المتحرك) ليظهر قبل نص هذا الزر.",
                     kb)
        return ans()
 
    if data.startswith("adm_edel_"):
        try:
            slot = EMOJI_SLOTS[int(data[len("adm_edel_"):])][0]
            (SETTINGS.get("btn_emojis") or {}).pop(slot, None)
            save_data()
        except (ValueError, IndexError):
            pass
        user_states.pop(uid, None)
        emoji_view(chat_id, mid)
        return ans("✅ تمت الإزالة")
 
    if data == "adm_eid":
        user_states[uid] = "eid_wait"
        edit_or_send(chat_id, mid, "🔎 أرسل أي إيموجي مميز (أو أكثر) وأعطيك الـ ID مالته.", back_kb("adm_emoji"))
        return ans()
 
    if data == "adm_eton":
        SETTINGS["icons_on"] = not SETTINGS.get("icons_on", True)
        save_data()
        emoji_view(chat_id, mid)
        return ans("✅ تم")
 
    if data == "adm_eclear":
        SETTINGS["btn_emojis"] = {}
        save_data()
        emoji_view(chat_id, mid)
        return ans("✅ تم الحذف")
 
    if data == "adm_toggle":
        SETTINGS["bot_on"] = not SETTINGS.get("bot_on", True)
        save_data()
        settings_view(chat_id, mid)
        return ans("✅ تم")
 
    # ---------- الأدمنية (للمالك فقط) ----------
    if data == "adm_admins":
        user_states.pop(uid, None)
        admins_view(chat_id, mid)
        return ans()
 
    if data == "adm_admin_add":
        user_states[uid] = "admin_wait_id"
        edit_or_send(chat_id, mid, "👑 أرسل آيدي الشخص اللي تريد تخليه أدمن (أرقام فقط):", back_kb("adm_admins"))
        return ans()
 
    if data.startswith("adm_admin_del_"):
        try:
            admins.discard(int(data[len("adm_admin_del_"):]))
            save_data()
        except ValueError:
            pass
        admins_view(chat_id, mid)
        return ans("✅ تم الحذف")
 
    ans()
 
 
# ============================================================
#                  استقبال الرسائل (نص / وسائط)
# ============================================================
ALL_TYPES = ["text", "animation", "photo", "video", "document", "audio", "voice", "sticker", "video_note"]
 
 
@bot.message_handler(content_types=ALL_TYPES)
def on_message(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    state = user_states.get(uid)
 
    if is_admin(uid) and state == "waiting_gif":
        return handle_gif_upload(message)
    if is_admin(uid) and state == "bc_wait_msg":
        return handle_bc_message(message)
 
    if message.content_type != "text":
        return
 
    text_in = (message.text or "").strip()
    denied = access_denied(uid)
    if denied:
        bot.send_message(message.chat.id, denied)
        return
 
    if is_admin(uid) and admin_text_state(message, uid, state, text_in):
        return
 
    if state == "waiting_username":
        handle_username(message, uid, text_in)
 
 
def handle_gif_upload(message):
    uid = message.from_user.id
    if message.animation:
        w = {"type": "animation", "file_id": message.animation.file_id}
    elif message.video:
        w = {"type": "video", "file_id": message.video.file_id}
    elif message.photo:
        w = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.document and (message.document.mime_type or "").startswith(("image/", "video/")):
        w = {"type": "document", "file_id": message.document.file_id}
    else:
        bot.send_message(message.chat.id, "⚠️ أرسل GIF أو صورة أو فيديو.")
        return
    SETTINGS["welcome"] = w
    seen.clear()
    save_data()
    user_states.pop(uid, None)
    bot.send_message(message.chat.id,
                     "✅ تم حفظ الـ GIF. راح يطلع مع القائمة الرئيسية برسالة وحدة.",
                     reply_markup=back_kb("adm_welcome", "🔙 الترحيب والـ GIF"))
 
 
def handle_bc_message(message):
    uid = message.from_user.id
    temp_data[uid] = {"bc": (message.chat.id, message.message_id)}
    user_states[uid] = "bc_confirm"
    kb = InlineKeyboardMarkup()
    kb.add(B(f"✅ إرسال للجميع ({len([u for u in users if u not in blocked])})", "adm_bc_go_all", style=GREEN))
    kb.add(B(f"💳 للمشتركين فقط ({len([u for u in users if u not in blocked and has_sub(u)])})",
             "adm_bc_go_subs", style=BLUE))
    kb.add(B("❌ إلغاء", "adm_bc_cancel", style=RED))
    bot.send_message(message.chat.id, "هذي الرسالة راح تنرسل. اختر الجمهور:", reply_markup=kb)
 
 
def admin_text_state(message, uid, state, text_in) -> bool:
    """يعالج حالات إدخال النص للأدمنية. يرجع True إذا عالجها."""
    chat = message.chat.id
 
    if state == "waiting_new_channel":
        ch = parse_channel(text_in)
        if not ch:
            bot.send_message(chat, "⚠️ يوزر قناة غير صالح، أرسله مرة ثانية.")
            return True
        SETTINGS["channel"] = ch
        user_states.pop(uid, None)
        save_data()
        bot.send_message(chat, f"✅ تم تغيير قناة النتائج إلى: @{ch}", reply_markup=back_kb("adm_settings"))
        return True
 
    if state == "force_wait_ch":
        ch = parse_channel(text_in)
        if not ch:
            bot.send_message(chat, "⚠️ يوزر قناة غير صالح (لازم قناة عامة لها يوزر). أرسله مرة ثانية.")
            return True
        if ch in SETTINGS.setdefault("force_channels", []):
            bot.send_message(chat, "⚠️ هذي القناة مضافة من قبل.")
            return True
        ok, err = bot_is_admin_in(ch)
        if not ok:
            bot.send_message(chat,
                             f"⚠️ ما قدرت أتأكد أن البوت أدمن بالقناة @{ch}.\n"
                             "أضف البوت أدمن بالقناة ثم أرسل اليوزر مرة ثانية.\n\n" + (err or ""))
            return True
        SETTINGS["force_channels"].append(ch)
        sub_cache.clear()
        user_states.pop(uid, None)
        save_data()
        bot.send_message(chat, f"✅ تمت إضافة @{ch} للاشتراك الإجباري.", reply_markup=back_kb("adm_force", "🔙 الاشتراك الإجباري"))
        return True
 
    if state == "sub_wait_id":
        if not text_in.isdigit():
            bot.send_message(chat, "⚠️ أرسل آيدي صحيح (أرقام فقط).")
            return True
        temp_data[uid] = {"sub_uid": int(text_in)}
        user_states[uid] = "sub_wait_num"
        bot.send_message(chat, "🔢 اكتب المدة كرقم (مثال: 3). بعدها تختار الوحدة:")
        return True
 
    if state == "sub_wait_num":
        if not text_in.isdigit() or not (1 <= int(text_in) <= 10000000):
            bot.send_message(chat, "⚠️ اكتب رقم من 1 فما فوق.")
            return True
        n = int(text_in)
        temp_data[uid]["sub_num"] = n
        user_states[uid] = "sub_wait_unit"
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(*[B(f"{n} {lab}", f"sub_unit_{k}", style=BLUE) for k, (lab, _) in UNITS.items()])
        kb.add(B("🔙 إلغاء", "adm_subs", style=RED))
        bot.send_message(chat, "⏱ اختر وحدة المدة:", reply_markup=kb)
        return True
 
    if state == "ban_wait_id":
        if not text_in.isdigit():
            bot.send_message(chat, "⚠️ أرسل آيدي صحيح (أرقام فقط).")
            return True
        target = int(text_in)
        if is_admin(target):
            bot.send_message(chat, "⚠️ ما تقدر تحظر أدمن.")
            return True
        blocked.add(target)
        stop_tracking(target)
        user_states.pop(uid, None)
        save_data()
        try:
            bot.send_message(target, "🚫 تم حظرك من استخدام البوت.")
        except Exception:
            pass
        bot.send_message(chat, f"✅ تم حظر {target}.", reply_markup=back_kb("adm_users", "🔙 المستخدمون"))
        return True
 
    if state == "uinfo_wait_id":
        if not text_in.isdigit():
            bot.send_message(chat, "⚠️ أرسل آيدي صحيح (أرقام فقط).")
            return True
        target = int(text_in)
        exp = subs.get(str(target), 0)
        n_track = len([1 for v in tracked.values() if v["chat_id"] == target])
        info = (f"🔎 معلومات المستخدم {target}\n\n"
                f"👤 بدأ البوت: {'نعم' if target in users else 'لا'}\n"
                f"👑 أدمن: {'نعم' if is_admin(target) else 'لا'}\n"
                f"🚫 محظور: {'نعم' if target in blocked else 'لا'}\n"
                f"💳 الاشتراك: " + (f"فعّال، باقي {fmt_left(exp - time.time())}" if exp > time.time() else "لا يوجد") + "\n"
                f"🔍 حسابات مراقبة: {n_track}")
        user_states.pop(uid, None)
        bot.send_message(chat, info, reply_markup=back_kb("adm_users", "🔙 المستخدمون"))
        return True
 
    if state == "admin_wait_id" and is_owner(uid):
        if not text_in.isdigit():
            bot.send_message(chat, "⚠️ أرسل آيدي صحيح (أرقام فقط).")
            return True
        target = int(text_in)
        if target == ADMIN_ID:
            bot.send_message(chat, "⚠️ هذا المالك أصلاً.")
            return True
        admins.add(target)
        blocked.discard(target)
        user_states.pop(uid, None)
        save_data()
        try:
            bot.send_message(target, "👑 تم تعيينك أدمن بالبوت. اكتب /admin لفتح لوحة التحكم.")
        except Exception:
            pass
        bot.send_message(chat, f"✅ تمت إضافة {target} كأدمن.", reply_markup=back_kb("adm_admins", "🔙 الأدمنية"))
        return True
 
    if state == "interval_wait":
        if not text_in.isdigit() or not (30 <= int(text_in) <= 3600):
            bot.send_message(chat, "⚠️ اكتب رقم من 30 إلى 3600.")
            return True
        SETTINGS["check_interval"] = int(text_in)
        user_states.pop(uid, None)
        save_data()
        bot.send_message(chat, f"✅ فترة الفحص صارت {text_in} ثانية.", reply_markup=back_kb("adm_settings", "🔙 الإعدادات"))
        return True
 
    if state == "session_wait":
        val = text_in.replace("sessionid=", "").strip().strip('"').strip("'")
        SETTINGS["ig_session"] = "" if val == "-" else val
        COOLDOWN["until"] = 0.0
        user_states.pop(uid, None)
        save_data()
        try:
            bot.delete_message(chat, message.message_id)
        except Exception:
            pass
        bot.send_message(chat, "✅ تم حفظ الـ sessionid" if val != "-" else "✅ رجع sessionid الملف.",
                         reply_markup=back_kb("adm_settings", "🔙 الإعدادات"))
        return True
 
    if state == "test_wait":
        user_states.pop(uid, None)
        username = text_in.replace("@", "").strip().lower()
        m = bot.send_message(chat, f"🔍 جاري فحص @{username}...")
        result = run_test_text(username)
        try:
            bot.delete_message(chat, m.message_id)
        except Exception:
            pass
        bot.send_message(chat, result, reply_markup=back_kb("adm_settings", "🔙 الإعدادات"))
        return True
 
    if state in ("emoji_wait", "eid_wait"):
        ids = [e.custom_emoji_id for e in (getattr(message, "entities", None) or [])
               if getattr(e, "type", "") == "custom_emoji" and getattr(e, "custom_emoji_id", None)]
        if not ids:
            bot.send_message(chat, "⚠️ ما لقيت إيموجي مميز برسالتك. أرسل إيموجي Premium (المتحرك)، مو إيموجي عادي.")
            return True
        if state == "eid_wait":
            user_states.pop(uid, None)
            bot.send_message(chat, "🔎 الـ IDs:\n\n" + "\n".join(ids), reply_markup=back_kb("adm_emoji", "🔙 الإيموجيات"))
            return True
        slot = (temp_data.get(uid) or {}).get("eslot") if isinstance(temp_data.get(uid), dict) else None
        if not slot:
            user_states.pop(uid, None)
            bot.send_message(chat, "⚠️ انتهت الجلسة، أعد المحاولة.", reply_markup=back_kb("adm_emoji", "🔙 الإيموجيات"))
            return True
        ok, err = test_icon(chat, ids[0])
        if not ok:
            bot.send_message(chat,
                             "❌ تلجرام رفض هذا الإيموجي:\n" + err +
                             "\n\nتأكد أن حسابك (مالك البوت) عنده Telegram Premium وأن الإيموجي مميز.")
            return True
        SETTINGS.setdefault("btn_emojis", {})[slot] = ids[0]
        SETTINGS["icons_on"] = True
        user_states.pop(uid, None)
        temp_data.pop(uid, None)
        save_data()
        bot.send_message(chat, "✅ تم! اضغط /start حتى تشوف الإيموجي على الزر.",
                         reply_markup=back_kb("adm_emoji", "🔙 الإيموجيات"))
        return True
 
    if state == "wtext_wait":
        if text_in == "-":
            SETTINGS["welcome_text"] = DEFAULT_WELCOME
        elif len(text_in) > 1000:
            bot.send_message(chat, "⚠️ النص طويل (الحد 1000 حرف).")
            return True
        else:
            SETTINGS["welcome_text"] = text_in
        user_states.pop(uid, None)
        save_data()
        bot.send_message(chat, "✅ تم حفظ نص الترحيب.", reply_markup=back_kb("adm_welcome", "🔙 الترحيب والـ GIF"))
        return True
 
    return False
 
 
def handle_username(message, uid, text_in):
    username = text_in.replace("@", "").strip().lower()
    if not username or " " in username or len(username) > 30:
        bot.send_message(message.chat.id, "⚠️ يوزر غير صالح، أرسله مرة ثانية.")
        return
 
    if f"{uid}:{username}" in tracked:
        bot.send_message(message.chat.id, "⚠️ هذا الحساب مراقب بالفعل!")
        return
 
    msg = bot.send_message(message.chat.id, f"🔍 جاري فحص @{username}...")
    status = check_instagram_status(username)
    try:
        bot.delete_message(message.chat.id, msg.message_id)
    except Exception:
        pass
 
    if status == "active":
        bot.send_message(message.chat.id,
                         f"❌ الحساب @{username} شغال وغير متبند! لا يمكن مراقبته.",
                         reply_markup=back_kb("btn_back", "🔙 القائمة الرئيسية"))
        user_states.pop(uid, None)
        return
 
    temp_data[uid] = username
    temp_data[f"{uid}:unchecked"] = False
    user_states[uid] = "waiting_ban_type"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(*[B(t, f"type_{i}", style=BLUE) for i, t in enumerate(BAN_TYPES)])
    kb.add(B("🔙 رجوع", "btn_back"))
    if status == "banned":
        text = f"✅ الحساب @{username} متبند فعلاً!\n\n📂 اختر نوع الباند:"
    else:
        text = (f"⚠️ ما قدرت أتأكد من حالة @{username} حالياً (الفحص غير مؤكد).\n\n"
                "إذا أنت متأكد أنه متبند اختر نوع الباند، وراح أراقبه وأبلغك أول ما أشوفه شغال فعلاً.")
    bot.send_message(message.chat.id, text, reply_markup=kb)
 
 
# ============================================================
#              المراقبة (خيط واحد لكل الحسابات)
# ============================================================
def notify_unban(key: str, info: dict):
    with lock:
        if tracked.pop(key, None) is None:
            return
    SETTINGS["unbans"] = SETTINGS.get("unbans", 0) + 1
    save_data()
 
    total = int(time.time() - info["start_time"])
    d, h, m, s = total // 86400, (total % 86400) // 3600, (total % 3600) // 60, total % 60
    unban_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
 
    text = (
        f"✧ Account Unbanned\n"
        f"Username : @{info['username']}\n"
        f"Time : {unban_time}\n"
        f"TAT : {d}d {h:02d}h {m:02d}m {s:02d}s\n"
        f"Type : •{info['ban_type']}\n"
        f"By : @{SETTINGS['channel']}"
    )
    try:
        bot.send_message(info["chat_id"], text)
    except Exception:
        pass
    if SETTINGS.get("channel"):
        try:
            bot.send_message(f"@{SETTINGS['channel']}", text)
        except Exception:
            pass
 
 
def notify_not_banned(key: str, info: dict):
    """حساب أُضيف بدون فحص وطلع شغال أصلاً: نوقف المراقبة"""
    with lock:
        if tracked.pop(key, None) is None:
            return
    save_data()
    try:
        bot.send_message(info["chat_id"],
                         f"ℹ️ الحساب @{info['username']} طلع شغال وغير متبند، فأوقفت مراقبته.")
    except Exception:
        pass
 
 
def monitor_loop():
    while True:
        wait = COOLDOWN["until"] - time.time()
        if wait > 0:
            time.sleep(min(wait, 30))
            continue
 
        with lock:
            items = list(tracked.items())
        for key, info in items:
            try:
                status = check_instagram_status(info["username"])
                if status == "active":
                    info["active_hits"] = info.get("active_hits", 0) + 1
                    if info["active_hits"] >= int(SETTINGS.get("confirm_needed", 1)):
                        if info.get("seen_banned", True):
                            notify_unban(key, info)
                        else:
                            notify_not_banned(key, info)
                elif status == "banned":
                    info["active_hits"] = 0
                    if not info.get("seen_banned", True):
                        info["seen_banned"] = True
                        save_data()
                # unknown: نتجاهله ولا نغير شي
            except Exception as e:
                print("monitor error:", repr(e))
            time.sleep(DELAY_BETWEEN)
        time.sleep(max(int(SETTINGS.get("check_interval", 90)), 30))
 
 
def subs_watcher():
    """كل 5 ثواني: اللي خلص اشتراكه ينطرد وتنحذف حساباته المراقبة"""
    while True:
        try:
            now = time.time()
            for u, exp in list(subs.items()):
                if exp <= now:
                    revoke_user(int(u), "⛔ انتهى اشتراكك وتم إيقاف حساباتك المراقبة.\nللتجديد تواصل مع الأدمن.")
                    try:
                        bot.send_message(ADMIN_ID, f"ℹ️ انتهى اشتراك المستخدم {u} وتم طرده.")
                    except Exception:
                        pass
        except Exception as e:
            print("subs_watcher error:", e)
        time.sleep(5)
 
 
def main():
    load_data()
    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=subs_watcher, daemon=True).start()
    print(f"Bot is running... (version: {VERSION})")
    bot.infinity_polling()
 
 
if __name__ == "__main__":
    main()
