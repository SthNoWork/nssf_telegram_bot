import asyncio
import json
import math
import os
import time
import urllib.request
from dotenv import load_dotenv
from google import genai
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# 1. Path Resolution & Environment Variables
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

# Load environment variables (telegram_bot/.env first, then root .env)
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(PARENT_DIR, ".env"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")
MAP_WEB_URL = os.getenv("MAP_WEB_URL", "https://sthnowork.github.io/nssf_telegram_bot/")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def get_openrouter_candidate_models() -> list[str]:
    """Returns prioritized candidate models for OpenRouter fallback."""
    models: list[str] = []
    if OPENROUTER_MODEL:
        for m in OPENROUTER_MODEL.split(","):
            m = m.strip()
            if m and m not in models:
                models.append(m)
    defaults = [
        "meta-llama/llama-3.3-70b-instruct",
        "deepseek/deepseek-chat",
        "mistralai/mistral-small-24b-instruct-2501",
        "qwen/qwen-2.5-72b-instruct",
        "google/gemini-2.0-flash-lite-001",
        "openrouter/auto",
    ]
    for d in defaults:
        if d not in models:
            models.append(d)
    return models


def call_openrouter_sync(system_prompt: str, user_text: str) -> str | None:
    """Calls OpenRouter chat completions with multi-model fallback."""
    if not OPENROUTER_API_KEY:
        return None

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/SthNoWork/nssf_telegram_bot",
        "X-Title": "NSSF Telegram Bot",
        "User-Agent": "NSSFBot/1.0",
    }
    candidate_models = get_openrouter_candidate_models()

    # 1. First attempt: OpenRouter native server-side fallback array (requires primary model + models list)
    payload = {
        "model": candidate_models[0],
        "models": candidate_models,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": 450,
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12.0) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            choices = res_data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "").strip()
                if content:
                    return content
    except Exception as e:
        print(f"OpenRouter multi-model payload attempt failed: {e}. Trying individual candidate models...")

    # 2. Secondary fallback: Iterate candidate models individually
    for model_name in candidate_models:
        try:
            single_payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "max_tokens": 450,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(single_payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                choices = res_data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "").strip()
                    if content:
                        print(f"OpenRouter succeeded with fallback model: {model_name}")
                        return content
        except Exception as e:
            print(f"OpenRouter model '{model_name}' attempt failed: {e}")
            continue

    return None


def resolve_file_path(filename, fallbacks=None):
    candidates = [filename] + (fallbacks or [])
    for name in candidates:
        for folder in [BASE_DIR, PARENT_DIR]:
            candidate_path = os.path.join(folder, name)
            if os.path.exists(candidate_path) and os.path.getsize(candidate_path) > 0:
                return candidate_path
    return os.path.join(BASE_DIR, filename)


NSSF_GEOJSON_FILE = resolve_file_path(
    os.path.join("data", "nssf.geojson"),
    [os.path.join("data", "nssf.json"), "nssf.geojson", "nssf.json", "facilities_baseline.json"],
)
TERMINATIONS_FILE = resolve_file_path(
    "verified_terminations.json", ["verified_terminations_2.json"]
)
GUIDELINES_FILE = resolve_file_path(
    os.path.join("data", "civic_guidelines.json"),
    ["civic_guidelines.json"],
)
SETTINGS_FILE = os.path.join(BASE_DIR, "data", "user_settings.json")


# Persistent User Settings Helpers
_CACHED_SETTINGS = None


def load_user_settings() -> dict:
    global _CACHED_SETTINGS
    if _CACHED_SETTINGS is not None:
        return _CACHED_SETTINGS
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                _CACHED_SETTINGS = json.load(f)
                return _CACHED_SETTINGS
        except Exception:
            pass
    _CACHED_SETTINGS = {}
    return _CACHED_SETTINGS


def save_user_setting(user_id: str | int, key: str, value):
    settings = load_user_settings()
    u_key = str(user_id)
    settings.setdefault(u_key, {})[key] = value
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"Error saving user settings: {e}")


# 2. Data Loaders & Cache
_CACHED_FACILITIES = None
_CACHED_GUIDELINES = None


def load_facilities() -> list:
    """Loads and caches facilities from GeoJSON (data/nssf.geojson) or baseline JSON."""
    global _CACHED_FACILITIES
    if _CACHED_FACILITIES is not None:
        return _CACHED_FACILITIES

    if not os.path.exists(NSSF_GEOJSON_FILE):
        return []

    try:
        with open(NSSF_GEOJSON_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        print(f"Error loading {NSSF_GEOJSON_FILE}: {e}")
        return []

    if isinstance(raw_data, dict) and raw_data.get("type") == "FeatureCollection":
        facilities = []
        for feat in raw_data.get("features", []):
            props = feat.get("properties", {})
            lat = props.get("lat")
            lng = props.get("long") or props.get("lng")
            if lat is None or lng is None:
                continue
            try:
                lat = float(lat)
                lng = float(lng)
            except (ValueError, TypeError):
                continue

            name = props.get("name", "NSSF Partner Facility")
            raw_contact = props.get("contact") or props.get("ambulance") or props.get("nssf_agency") or "N/A"
            phone = raw_contact.split("\n")[0].strip() if raw_contact else "N/A"
            district = (props.get("district") or "").strip()
            province = (props.get("province") or "").strip()

            facilities.append({
                "id": str(feat.get("id") or props.get("map_id") or len(facilities)),
                "name_km": props.get("name_km", name),
                "name_en": name,
                "district": district or "N/A",
                "province": province or "Cambodia",
                "lat": lat,
                "lng": lng,
                "phone": phone,
            })
        _CACHED_FACILITIES = facilities
        return facilities

    if isinstance(raw_data, list):
        _CACHED_FACILITIES = raw_data
        return raw_data

    return []


def load_guidelines() -> dict:
    global _CACHED_GUIDELINES
    if _CACHED_GUIDELINES is not None:
        return _CACHED_GUIDELINES

    if os.path.exists(GUIDELINES_FILE):
        try:
            with open(GUIDELINES_FILE, "r", encoding="utf-8") as f:
                _CACHED_GUIDELINES = json.load(f).get("issues", {})
                return _CACHED_GUIDELINES
        except Exception as e:
            print(f"Error loading guidelines: {e}")
    _CACHED_GUIDELINES = {}
    return _CACHED_GUIDELINES


def load_terminations() -> list:
    if os.path.exists(TERMINATIONS_FILE):
        try:
            with open(TERMINATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading terminations: {e}")
    return []


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def check_if_terminated(facility: dict, terminations: list) -> dict | None:
    name_km = facility.get("name_km", "")
    name_en = facility.get("name_en", "")

    core_km = (
        name_km.replace("មន្ទីរពហុព្យាបាល", "")
        .replace("មន្ទីរសម្រាកព្យាបាល", "")
        .replace("មន្ទីរពេទ្យ", "")
        .strip()
    )
    core_en = (
        name_en.replace("Hospital", "")
        .replace("Clinic", "")
        .replace("Polyclinic", "")
        .replace("Referral", "")
        .strip()
    )

    for item in terminations:
        item_text = item.get("text", "")
        if core_km and len(core_km) > 2 and core_km in item_text:
            return item
        if core_en and len(core_en) > 3 and core_en.lower() in item_text.lower():
            return item
    return None


def search_clinic_by_name(query: str, facilities: list, terminations: list) -> list:
    """Searches clinics directly by name or district locally with zero LLM tokens."""
    q = query.lower().strip()
    # Strip generic terms for tighter matching
    for generic in ["មន្ទីរពេទ្យ", "មន្ទីរសម្រាកព្យាបាល", "មន្ទីរពហុព្យាបាល", "hospital", "clinic", "polyclinic"]:
        q_clean = q.replace(generic, "").strip()
        if len(q_clean) >= 3:
            q = q_clean
            break

    if len(q) < 3:
        return []

    matches = []
    for fac in facilities:
        name_km = fac.get("name_km", "").lower()
        name_en = fac.get("name_en", "").lower()
        district = fac.get("district", "").lower()
        if q in name_km or q in name_en or q in district:
            term_notice = check_if_terminated(fac, terminations)
            matches.append((fac, term_notice))
            if len(matches) >= 3:
                break
    return matches


# 3. Bilingual UI Strings
STRINGS = {
    "km": {
        "welcome": (
            "សួស្តី! ស្វាគមន៍មកកាន់ **NSSF Smart Navigator** 🇰🇭\n"
            "ជំនួយការឆ្លាតវៃសម្រាប់សមាជិក ប.ស.ស.៖\n\n"
            "• 📍 **ស្វែងរកមន្ទីរពេទ្យជិតបំផុត** និងការព្រមានគ្លីនិកបញ្ចប់កិច្ចព្រមព្រៀង\n"
            "• 🩺 **ពិនិត្យប្រភេទបញ្ហា & សេវា** (ជំងឺរ៉ាំរ៉ៃ, ធ្មេញ, មាតុភាព, ការសម្រាកពេទ្យ)\n"
            "• 📋 **លក្ខខណ្ឌ & អត្ថប្រយោជន៍** សម្រាប់កម្មករ និងស្វ័យនិយោជន៍\n"
            "• 🗺 **មើលផែនទីរួម** មូលដ្ឋានសុខាភិបាលទូទាំងប្រទេស\n"
            "• 🚨 **ករណីបន្ទាន់** ព័ត៌មានរហ័ស\n\n"
            "💡 **ចំណាំ៖** អ្នកអាចចុចប៊ូតុងខាងក្រោម ឬ **គ្រាន់តែវាយសំណួររបស់អ្នកផ្ទាល់** (ឧ. ឈ្មោះគ្លីនិក ឬរោគសញ្ញា) ដោយមិនចាំបាច់ចុចប៊ូតុងក៏បាន!"
        ),
        "btn_back": "🔙 ថយក្រោយ (Back)",
        "btn_location": "🏥 ស្វែងរកមន្ទីរពេទ្យជិតបំផុត (Share Location)",
        "btn_issues": "🩺 ប្រភេទបញ្ហា & សេវា",
        "btn_rules": "📋 លក្ខខណ្ឌ & អត្ថប្រយោជន៍",
        "btn_map": "🗺 មើលផែនទីរួម (View Map)",
        "btn_lang": "🌐 ភាសា / Language",
        "btn_emergency": "🚨 ករណីបន្ទាន់ (In Case of Emergency)",
        "choose_lang": "🌐 **សូមជ្រើសរើសភាសារបស់អ្នក / Please select your language:**",
        "lang_set": "✅ ភាសាត្រូវបានប្តូរទៅជា **ភាសាខ្មែរ**។",
        "emergency_info": (
            "• **ទូរស័ព្ទបន្ទាន់ (24h)៖** `1286` ឬ `1297`\n"
            "• **ទៅពេទ្យជិតបំផុតភ្លាមៗ** (ទោះមិនមែនដៃគូ ប.ស.ស. ក៏អាចស្នើសុំទូទាត់សងវិញបាន)។\n"
            "• **សំខាន់៖** ហាមទុកប័ណ្ណ ប.ស.ស. នៅមន្ទីរពេទ្យពេលចេញពីពេទ្យដាច់ខាត!"
        ),
        "map_info": "🗺 **ផែនទីមូលដ្ឋានសុខាភិបាលដៃគូ ប.ស.ស. ទូទាំងប្រទេស៖**\nចុចប៊ូតុងខាងក្រោមដើម្បីបើកមើលទីតាំងមន្ទីរពេទ្យសកម្ម (ពណ៌បៃតង) និងគ្លីនិកដែលត្រូវបានបញ្ចប់កិច្ចព្រមព្រៀង (ពណ៌ក្រហម)៖",
        "btn_open_web_app": "📱 បើកក្នុង Telegram (Mobile)",
        "btn_open_browser": "💻 បើកលើ Browser (Laptop / PC)",
        "btn_open_area_map": "🗺 មើលគ្លីនិកជុំវិញទីតាំងអ្នក",
        "issues_menu_title": "🩺 **សូមជ្រើសរើសប្រភេទបញ្ហាសុខភាព ឬសេវា ប.ស.ស. ដែលអ្នកចង់ដឹង៖**",
        "rules_menu_title": "📋 **សូមជ្រើសរើសប្រភេទសមាជិក ដើម្បីពិនិត្យលក្ខខណ្ឌ និងឯកសារតម្រូវ៖**",
        "btn_rule_worker": "👷 កម្មករនិយោជិត (Formal Worker)",
        "btn_rule_self": "🛵 បុគ្គលស្វ័យនិយោជន៍ & អ្នកក្នុងបន្ទុក (15,600៛)",
        "btn_rule_maternity": "🤰 អត្ថប្រយោជន៍មាតុភាព (សម្រាលកូន)",
        "rule_worker_text": (
            "👷 **លក្ខខណ្ឌសម្រាប់កម្មករនិយោជិត (Formal Workers)៖**\n"
            "• **សិទ្ធិព្យាបាលឥតគិតថ្លៃ៖** និយោជកបានបង់ភាគទាន **២ខែជាប់គ្នា** ឬ **៦ខែក្នុង ១២ខែចុងក្រោយ**។\n"
            "• **ពេលឈប់ធ្វើការ៖** នៅតែអាចប្រើប្រាស់ប័ណ្ណ ប.ស.ស. បាន **២ខែបន្ទាប់**។\n"
            "• **ប្រាក់បំណាច់ ៧០% ពេលឈឺ៖** សម្រាកព្យាបាលនៅពេទ្យដៃគូចាប់ពី **៨ថ្ងៃឡើងទៅ**។"
        ),
        "rule_self_text": (
            "🛵 **បុគ្គលស្វ័យនិយោជន៍ & អ្នកក្នុងបន្ទុក (Self-Employed & Dependents)៖**\n"
            "• **ភាគទាន៖** ចំណាយត្រឹមតែ **១៥,៦០០ រៀល/ខែ**។\n"
            "• **សិទ្ធិប្រើប្រាស់សេវា៖** ត្រូវបង់ភាគទានយ៉ាងតិច **២ខែជាប់គ្នា**។\n"
            "• **ឯកសារទៅពេទ្យ៖** ប័ណ្ណសមាជិក ប.ស.ស. + អត្តសញ្ញាណប័ណ្ណសញ្ជាតិខ្មែរច្បាប់ដើម។"
        ),
        "rule_maternity_text": (
            "🤰 **អត្ថប្រយោជន៍មាតុភាព (Maternity Benefits)៖**\n"
            "• **លក្ខខណ្ឌទទួលប្រាក់បំណាច់៖** បានបង់ភាគទានយ៉ាងតិច **៩ខែ ក្នុងអំឡុង ១២ខែចុងក្រោយ** គិតត្រឹមខែសម្រាល។\n"
            "• **កម្មការិនីវិស័យឯកជន៖** ទទួលបានប្រាក់បំណាច់ប្រចាំថ្ងៃ **៧០% រយៈពេល ៩០ថ្ងៃ** + ប្រាក់ឧបត្ថម្ភរាជរដ្ឋាភិបាល។\n"
            "• **ស្ត្រីស្វ័យនិយោជន៍៖** ទទួលបានប្រាក់បំណាច់មាតុភាព **១,២៦០,០០០ រៀល**។"
        ),
        "warn_terminated_title": "⚠️ **ការជូនដំណឹងបន្ទាន់ (គ្លីនិកបញ្ចប់កិច្ចព្រមព្រៀងជិតអ្នក)៖**",
        "terminated_suffix": "ត្រូវបានបញ្ចប់/ផ្អាកកិច្ចព្រមព្រៀងកាលពីថ្ងៃទី",
        "view_official_notice": "មើលសេចក្តីជូនដំណឹងផ្លូវការ",
        "active_clinics_title": "✅ **មូលដ្ឋានសុខាភិបាលដៃគូ ប.ស.ស. ដែលនៅជិតអ្នកបំផុត៖**",
        "clinic_search_result_title": "🏥 **លទ្ធផលស្វែងរកមូលដ្ឋានសុខាភិបាល៖**",
        "label_district": "ខណ្ឌ/ស្រុក៖",
        "label_phone": "ទូរស័ព្ទ៖",
        "open_maps": "បើកមើលទីតាំងលើ Google Maps",
        "gemini_missing_key": "⚠️ សូមដាក់ `GEMINI_API_KEY` ក្នុង `.env` ដើម្បីប្រើប្រាស់មុខងារឆ្លើយតបឆ្លាតវៃ។",
        "status_loc": "🔍 កំពុងស្វែងរក និងត្រួតពិនិត្យគ្លីនិកដៃគូជិតបំផុត...",
        "status_ai": "⏳ កំពុងដំណើរការស្វែងរកចម្លើយ...",
        "rate_limited": "⚠️ អ្នកបានសួរសំណួរញឹកញាប់ពេក។ សូមរង់ចាំ ១ នាទី រួចសួរម្តងទៀត។",
    },
    "en": {
        "welcome": (
            "Hello! Welcome to **NSSF Smart Navigator** 🇰🇭\n"
            "Smart civic assistant for NSSF members in Cambodia:\n\n"
            "• 📍 **Nearest Clinics & Termination Alerts**: Live proximity search with contract status checks\n"
            "• 🩺 **Issue Types & Services**: Fast answers for Chronic Care, Dental Exclusions, and Maternity\n"
            "• 📋 **Eligibility & Checklists**: Rules for formal workers and self-employed members\n"
            "• 🗺 **Nationwide Map**: View active and terminated clinics on interactive web map\n"
            "• 🚨 **In Case of Emergency**: Fast access hotlines & instructions\n\n"
            "💡 **Tip:** You can tap the buttons below or **just type your question directly** (e.g. clinic name or question) without having to click anything!"
        ),
        "btn_back": "🔙 Back",
        "btn_location": "🏥 Find Nearest Clinic (Share Location)",
        "btn_issues": "🩺 Issue Types & Services",
        "btn_rules": "📋 Eligibility & Rules",
        "btn_map": "🗺 View Nationwide Map",
        "btn_lang": "🌐 Language / ភាសា",
        "btn_emergency": "🚨 In Case of Emergency",
        "choose_lang": "🌐 **Please select your language / សូមជ្រើសរើសភាសារបស់អ្នក:**",
        "lang_set": "✅ Language has been set to **English**.",
        "emergency_info": (
            "• **Hotline (24/7):** `1286` or `1297`\n"
            "• **Head to the nearest hospital immediately** (even non-partner; submit receipts for reimbursement later).\n"
            "• **Important:** Never leave your NSSF card behind at the hospital upon discharge!"
        ),
        "map_info": "🗺 **Nationwide NSSF Healthcare Facilities Map:**\nClick below to open the interactive map showing active clinics (green) vs terminated clinics (red):",
        "btn_open_web_app": "📱 Open in Telegram (Mobile)",
        "btn_open_browser": "💻 Open in Browser (Laptop / PC)",
        "btn_open_area_map": "🗺 View Clinics in Your Area",
        "issues_menu_title": "🩺 **Select a topic to view official NSSF coverage rules:**",
        "rules_menu_title": "📋 **Select your membership type to view eligibility criteria:**",
        "btn_rule_worker": "👷 Formal Workers",
        "btn_rule_self": "🛵 Self-Employed & Dependents (15,600៛)",
        "btn_rule_maternity": "🤰 Maternity Benefits",
        "rule_worker_text": (
            "👷 **Eligibility for Formal Workers:**\n"
            "• **Free Healthcare:** Employer must have contributed **2 consecutive months** or **6 months within the last 12 months**.\n"
            "• **After Resignation:** Coverage remains valid for **2 additional months**.\n"
            "• **70% Sick Leave Allowance:** Applies when admitted at a partner facility for **8 days or more**."
        ),
        "rule_self_text": (
            "🛵 **Self-Employed & Dependents:**\n"
            "• **Monthly Contribution:** Only **15,600 KHR/month**.\n"
            "• **Care Access:** Must contribute at least **2 consecutive months**.\n"
            "• **Required Documents:** Original NSSF card + original Cambodian National ID card."
        ),
        "rule_maternity_text": (
            "🤰 **Maternity Benefits:**\n"
            "• **Eligibility:** Contributed at least **9 months within the last 12 months** prior to delivery.\n"
            "• **Private Sector Workers:** 70% of daily wage for **90 days** + Royal Government cash allowance.\n"
            "• **Self-Employed Women:** Direct cash maternity allowance of **1,260,000 KHR**."
        ),
        "warn_terminated_title": "⚠️ **URGENT NOTICE (Nearby Terminated/Suspended Clinics):**",
        "terminated_suffix": "Contract terminated or suspended on",
        "view_official_notice": "View Official Notice",
        "active_clinics_title": "✅ **Nearest Active NSSF Partner Healthcare Facilities:**",
        "clinic_search_result_title": "🏥 **Healthcare Facility Search Results:**",
        "label_district": "District:",
        "label_phone": "Phone:",
        "open_maps": "Open in Google Maps",
        "gemini_missing_key": "⚠️ Please add `GEMINI_API_KEY` to your `.env` to enable AI triage.",
        "status_loc": "🔍 Searching and verifying nearest partner clinics...",
        "status_ai": "⏳ Consulting NSSF guidelines & generating response...",
        "rate_limited": "⚠️ You are sending requests too quickly. Please wait 1 minute before asking again.",
    },
}


def get_lang(context: ContextTypes.DEFAULT_TYPE, user_id: str | int = None) -> str:
    # 1. In-memory check
    if "lang" in context.user_data:
        return context.user_data["lang"]
    # 2. Disk persistence check
    if user_id:
        settings = load_user_settings()
        persisted = settings.get(str(user_id), {}).get("lang")
        if persisted:
            context.user_data["lang"] = persisted
            return persisted
    return "km"


def t(context: ContextTypes.DEFAULT_TYPE, key: str, user_id: str | int = None) -> str:
    lang = get_lang(context, user_id)
    return STRINGS.get(lang, STRINGS["km"]).get(key, STRINGS["km"].get(key, ""))


# 4. Keyboards
def get_main_menu(context: ContextTypes.DEFAULT_TYPE, user_id: str | int = None):
    map_btn = KeyboardButton(t(context, "btn_map", user_id), web_app=WebAppInfo(url=MAP_WEB_URL))

    keyboard = [
        [KeyboardButton(t(context, "btn_location", user_id), request_location=True)],
        [
            KeyboardButton(t(context, "btn_issues", user_id)),
            KeyboardButton(t(context, "btn_rules", user_id)),
        ],
        [
            map_btn,
            KeyboardButton(t(context, "btn_lang", user_id)),
        ],
        [
            KeyboardButton(t(context, "btn_emergency", user_id)),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


def get_issues_inline(context: ContextTypes.DEFAULT_TYPE):
    guidelines = load_guidelines()
    lang = get_lang(context)
    buttons = []
    btn_key = "btn_km" if lang == "km" else "btn_en"

    for issue_id, data in guidelines.items():
        label = data.get(btn_key, issue_id)
        buttons.append([InlineKeyboardButton(label, callback_data=f"issue:{issue_id}")])

    buttons.append([InlineKeyboardButton(t(context, "btn_back"), callback_data="menu:back")])
    return InlineKeyboardMarkup(buttons)


def get_language_inline():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇰🇭 ភាសាខ្មែរ (Khmer)", callback_data="lang:km"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ]
    ])


# 5. Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = t(context, "welcome")
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_main_menu(context),
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        t(context, "choose_lang"),
        parse_mode="Markdown",
        reply_markup=get_language_inline(),
    )


async def map_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None

    # Dual options: Mobile Telegram WebApp + Laptop/Desktop Browser
    buttons = [
        [InlineKeyboardButton(t(context, "btn_open_web_app", user_id), web_app=WebAppInfo(url=MAP_WEB_URL))],
        [InlineKeyboardButton(t(context, "btn_open_browser", user_id), url=MAP_WEB_URL)],
        [InlineKeyboardButton(t(context, "btn_back", user_id), callback_data="menu:back")],
    ]
    await update.message.reply_text(
        t(context, "map_info", user_id),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def emergency_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        t(context, "emergency_info"),
        parse_mode="Markdown",
    )


async def issues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        t(context, "issues_menu_title"),
        parse_mode="Markdown",
        reply_markup=get_issues_inline(context),
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.FIND_LOCATION)
    except Exception:
        pass

    status_msg = None
    try:
        status_msg = await update.message.reply_text(t(context, "status_loc"))
    except Exception:
        pass

    user_loc = update.message.location
    user_id = update.effective_user.id if update.effective_user else None
    # Location memory caching (in-memory + persistent disk)
    context.user_data["last_loc"] = (user_loc.latitude, user_loc.longitude)
    if user_id:
        save_user_setting(user_id, "last_loc", [user_loc.latitude, user_loc.longitude])

    facilities = load_facilities()
    terminations = load_terminations()
    lang = get_lang(context, user_id)

    scored = []
    for fac in facilities:
        dist = haversine_km(user_loc.latitude, user_loc.longitude, fac["lat"], fac["lng"])
        term_notice = check_if_terminated(fac, terminations)
        scored.append((dist, fac, term_notice))

    scored.sort(key=lambda x: x[0])

    active_results = [item for item in scored if item[2] is None][:3]
    terminated_nearby = [item for item in scored if item[2] is not None and item[0] <= 15.0]

    # Clean up status message
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    # 1. Warn about nearby terminated clinics
    if terminated_nearby:
        warn_lines = [t(context, "warn_terminated_title")]
        for dist, fac, notice in terminated_nearby:
            name_display = fac["name_km"] if lang == "km" else f"{fac['name_en']} ({fac['name_km']})"
            notice_date = notice.get("date", "")[:10]
            notice_url = notice.get("url", "")
            suffix = t(context, "terminated_suffix")
            link_label = t(context, "view_official_notice")
            warn_lines.append(
                f"❌ **{name_display}** — `{dist:.1f} km`\n"
                f"   ↳ {suffix} `{notice_date}` ([{link_label}]({notice_url}))"
            )
        await update.message.reply_text(
            "\n".join(warn_lines),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    # 2. Top nearest active clinics
    await update.message.reply_text(
        t(context, "active_clinics_title"),
        parse_mode="Markdown",
    )

    for dist, fac, _ in active_results:
        maps_url = f"https://www.google.com/maps?q={fac['lat']},{fac['lng']}"
        name_display = fac["name_km"] if lang == "km" else fac["name_en"]
        card_text = (
            f"🏥 **{name_display}**\n"
            f"📍 {t(context, 'label_district')} {fac['district']}, {fac['province']} (`{dist:.1f} km`)\n"
            f"📞 {t(context, 'label_phone')} `{fac['phone']}`\n"
            f"🗺 [{t(context, 'open_maps')}]({maps_url})"
        )
        await update.message.reply_text(
            card_text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    # Area overview link on map
    area_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(context, "btn_open_web_app", user_id), web_app=WebAppInfo(url=MAP_WEB_URL))],
        [InlineKeyboardButton(t(context, "btn_open_browser", user_id), url=MAP_WEB_URL)],
    ])
    await update.message.reply_text(
        t(context, "btn_open_area_map", user_id),
        reply_markup=area_buttons,
    )
    # Ensure bottom menu is always available
    await update.message.reply_text(
        "👇",
        reply_markup=get_main_menu(context, user_id),
    )


async def handle_rules_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(context, "btn_rule_worker"), callback_data="rule:worker")],
        [InlineKeyboardButton(t(context, "btn_rule_self"), callback_data="rule:self")],
        [InlineKeyboardButton(t(context, "btn_rule_maternity"), callback_data="rule:maternity")],
        [InlineKeyboardButton(t(context, "btn_back"), callback_data="menu:back")],
    ])
    await update.message.reply_text(
        t(context, "rules_menu_title"),
        parse_mode="Markdown",
        reply_markup=buttons,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id if query.from_user else None
    lang = get_lang(context, user_id)

    # Back to main menu
    if data == "menu:back":
        await query.message.reply_text(
            t(context, "welcome", user_id),
            parse_mode="Markdown",
            reply_markup=get_main_menu(context, user_id),
        )
        return

    # Language selection (saved permanently to disk)
    if data.startswith("lang:"):
        selected_lang = data.split(":")[1]
        context.user_data["lang"] = selected_lang
        if user_id:
            save_user_setting(user_id, "lang", selected_lang)
        ack_text = t(context, "lang_set", user_id)
        await query.message.reply_text(
            ack_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu(context, user_id),
        )
        return

    # Issues knowledge base (Zero LLM Tokens)
    if data.startswith("issue:"):
        issue_id = data.split(":")[1]
        guidelines = load_guidelines()
        issue_data = guidelines.get(issue_id, {})
        text_key = "text_km" if lang == "km" else "text_en"
        response_text = issue_data.get(text_key, "")
        if response_text:
            back_btn = InlineKeyboardMarkup([[InlineKeyboardButton(t(context, "btn_back"), callback_data="menu:back")]])
            await query.message.reply_text(response_text, parse_mode="Markdown", reply_markup=back_btn)
        return

    # Eligibility rules callbacks (Zero LLM Tokens)
    back_btn = InlineKeyboardMarkup([[InlineKeyboardButton(t(context, "btn_back"), callback_data="menu:back")]])
    if data == "rule:worker":
        await query.edit_message_text(t(context, "rule_worker_text"), parse_mode="Markdown", reply_markup=back_btn)
    elif data == "rule:self":
        await query.edit_message_text(t(context, "rule_self_text"), parse_mode="Markdown", reply_markup=back_btn)
    elif data == "rule:maternity":
        await query.edit_message_text(t(context, "rule_maternity_text"), parse_mode="Markdown", reply_markup=back_btn)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text messages using local keyword interception (Token Saving) before LLM fallback."""
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass

    user_text = update.message.text.strip()
    user_lower = user_text.lower()
    user_id = update.effective_user.id if update.effective_user else None
    lang = get_lang(context, user_id)

    # 0. Back button trigger
    if "ថយក្រោយ" in user_text or "back" in user_lower:
        await start_command(update, context)
        return

    # 1. Language switch trigger
    if "ភាសា" in user_text or "language" in user_lower:
        await language_command(update, context)
        return

    # 2. Map trigger
    if "ផែនទី" in user_text or "map" in user_lower:
        await map_command(update, context)
        return

    # 3. Emergency trigger
    if (
        "ករណីបន្ទាន់" in user_text
        or "សង្គ្រោះបន្ទាន់" in user_text
        or "emergency" in user_lower
        or "1286" in user_text
        or "1297" in user_text
    ):
        await emergency_command(update, context)
        return

    # 4. Eligibility rules menu trigger
    if (
        "ពិនិត្យលក្ខខណ្ឌ" in user_text
        or "លក្ខខណ្ឌ &" in user_text
        or "eligibility" in user_lower
        or "rules" in user_lower
    ):
        await handle_rules_menu(update, context)
        return

    # 5. Issue menu trigger
    if (
        "ប្រភេទបញ្ហា" in user_text
        or "បញ្ហា &" in user_text
        or "issue" in user_lower
        or "services" in user_lower
    ):
        await issues_command(update, context)
        return

    # 6. Direct Local Clinic Name Search (Zero LLM Tokens)
    facilities = load_facilities()
    terminations = load_terminations()
    clinic_matches = search_clinic_by_name(user_text, facilities, terminations)
    if clinic_matches:
        await update.message.reply_text(t(context, "clinic_search_result_title"), parse_mode="Markdown")
        for fac, term_notice in clinic_matches:
            name_display = fac["name_km"] if lang == "km" else fac["name_en"]
            maps_url = f"https://www.google.com/maps?q={fac['lat']},{fac['lng']}"
            term_warning = ""
            if term_notice:
                notice_date = term_notice.get("date", "")[:10]
                notice_url = term_notice.get("url", "")
                suffix = t(context, "terminated_suffix")
                link_label = t(context, "view_official_notice")
                term_warning = f"\n⚠️ **{suffix} `{notice_date}`** ([{link_label}]({notice_url}))"

            card_text = (
                f"🏥 **{name_display}**{term_warning}\n"
                f"📍 {t(context, 'label_district')} {fac['district']}, {fac['province']}\n"
                f"📞 {t(context, 'label_phone')} `{fac['phone']}`\n"
                f"🗺 [{t(context, 'open_maps')}]({maps_url})"
            )
            await update.message.reply_text(card_text, parse_mode="Markdown", disable_web_page_preview=True)
        return

    # 7. TOKEN SAVING STRATEGY: Local Keyword Interception from civic_guidelines.json
    guidelines = load_guidelines()
    text_key = "text_km" if lang == "km" else "text_en"
    for _, issue_info in guidelines.items():
        keywords = issue_info.get("keywords", [])
        if any(kw.lower() in user_lower for kw in keywords):
            await update.message.reply_text(issue_info[text_key], parse_mode="Markdown")
            return

    # 8. Rate Limiting Protection (Max 10 AI queries per 120s per user)
    now = time.time()
    query_history = context.user_data.setdefault("query_times", [])
    # Keep only timestamps within last 120s
    query_history = [ts for ts in query_history if now - ts < 120]
    context.user_data["query_times"] = query_history
    if len(query_history) >= 10:
        await update.message.reply_text(t(context, "rate_limited"))
        return
    query_history.append(now)

    # 9. AI Resolution (OpenRouter priority if configured, fallback to Gemini)
    if not OPENROUTER_API_KEY and not client:
        await update.message.reply_text(t(context, "gemini_missing_key"))
        return

    ai_status = None
    try:
        ai_status = await update.message.reply_text(t(context, "status_ai"))
    except Exception:
        pass

    recent_terms = terminations[:5]
    term_summary = "\n".join(
        f"- {t_item.get('date', '')[:10]}: {t_item.get('text', '').splitlines()[0]}"
        for t_item in recent_terms
    )

    lang_instruction = "Answer strictly in Khmer." if lang == "km" else "Answer strictly in English."

    compact_system_prompt = f"""You are the NSSF Cambodia Smart Assistant. {lang_instruction}
Keep answers concise (max 3-4 bullet points).

DOMAIN BOUNDARY & REFUSAL RULES:
- ONLY answer questions concerning NSSF (National Social Security Fund Cambodia) benefits, coverage rules, clinic verification, maternity allowances, and medical emergency procedures (hotlines 1286 / 1297).
- STRICT REFUSAL: If the user asks about coding, homework, math, translations, creative writing, or non-NSSF topics—even if combined with a medical claim or emergency—DO NOT solve or answer the non-NSSF portion. Politely refuse and state that you only handle NSSF inquiries and medical emergency hotlines.

Strict NSSF Rules:
1. Chronic diseases (diabetes, hypertension, cancer, kidney, hepatitis) covered ONLY at PUBLIC hospitals.
2. Routine dental cleaning/fillings, cosmetic surgery, and non-emergency checkups are EXCLUDED.
3. Emergency care is covered at the nearest hospital (call 1286/1297 for claims).
4. Never leave NSSF card with clinic staff upon discharge.
Recent clinic terminations:
{term_summary}
If user asks about a terminated clinic, immediately warn them."""

    response_text = None

    # Priority 1: OpenRouter (fast, reliable, no 503 errors)
    if OPENROUTER_API_KEY:
        loop = asyncio.get_event_loop()
        try:
            response_text = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: call_openrouter_sync(compact_system_prompt, user_text),
                ),
                timeout=12.0,
            )
        except Exception as e:
            print(f"OpenRouter attempt failed: {e}")

    # Priority 2: Direct Gemini with fallback models if OpenRouter did not yield a response
    if not response_text and client:
        candidate_models = []
        for m in [GEMINI_MODEL, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            if m and m not in candidate_models:
                candidate_models.append(m)

        for model_name in candidate_models:
            for attempt in range(2):
                try:
                    loop = asyncio.get_event_loop()
                    resp = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: client.models.generate_content(
                                model=model_name,
                                contents=f"{compact_system_prompt}\n\nUser Question: {user_text}",
                            ),
                        ),
                        timeout=12.0,
                    )
                    if resp and resp.text:
                        response_text = resp.text
                        break
                except Exception as e:
                    err_str = str(e)
                    if any(k in err_str for k in ["503", "429", "UNAVAILABLE", "timeout"]):
                        await asyncio.sleep(1.5)
                        continue
                    break
            if response_text:
                break

    if ai_status:
        try:
            await ai_status.delete()
        except Exception:
            pass

    if response_text:
        await update.message.reply_text(response_text, reply_markup=get_main_menu(context))
    else:
        busy_msg = (
            "⚠️ ប្រព័ន្ធឆ្លើយសំណួរស្វ័យប្រវត្តិកំពុងមានការប្រើប្រាស់ច្រើនបណ្តោះអាសន្ន។ សូមព្យាយាមសួរម្តងទៀតបន្តិចក្រោយ ឬជ្រើសរើសមុខងារពីប៊ូតុងម៉ឺនុយខាងក្រោម។"
            if lang == "km"
            else "⚠️ The AI assistant is currently experiencing high traffic. Please try again in a few moments or select an option from the menu buttons below."
        )
        await update.message.reply_text(busy_msg, reply_markup=get_main_menu(context))


async def post_init(application: Application):
    from telegram import BotCommand
    commands = [
        BotCommand("start", "🏠 ម៉ឺនុយដើម / Main Menu"),
        BotCommand("map", "🗺 មើលផែនទី / Interactive Map"),
        BotCommand("issues", "🩺 ប្រភេទបញ្ហា & សេវា / Issues"),
        BotCommand("rules", "📋 លក្ខខណ្ឌ & អត្ថប្រយោជន៍ / Rules"),
        BotCommand("emergency", "🚨 ករណីបន្ទាន់ / Emergency"),
        BotCommand("language", "🌐 ភាសា / Language"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        print("✅ Registered persistent bot commands with Telegram.")
    except Exception as e:
        print(f"Warning: Could not set bot commands: {e}")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handles transient network errors cleanly without flooding the console."""
    err = context.error
    if not err:
        return
    err_str = str(err)
    if "getaddrinfo failed" in err_str or "ConnectError" in err_str or "NetworkError" in err_str:
        print("⚠️ Transient network/DNS interruption (Telegram API unreachable). Reconnecting...")
        return
    if "Conflict" in err_str or "terminated by other getUpdates" in err_str:
        print("⚠️ Multiple bot instances detected with the same token.")
        return
    print(f"⚠️ Telegram error: {err}")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Error: Set TELEGRAM_BOT_TOKEN in your .env file first!")
        return

    facilities = load_facilities()
    terminations = load_terminations()
    guidelines = load_guidelines()
    print(f"📦 Loaded {len(facilities)} facilities from {NSSF_GEOJSON_FILE}")
    print(f"⚠️ Loaded {len(terminations)} termination notices from {TERMINATIONS_FILE}")
    print(f"📋 Loaded {len(guidelines)} NSSF guideline topics from {GUIDELINES_FILE}")
    print(f"🗺 Map Web URL: {MAP_WEB_URL}")
    models_str = ", ".join(get_openrouter_candidate_models())
    print(f"⚡ OpenRouter Provider: {'Enabled (Cascade: ' + models_str + ')' if OPENROUTER_API_KEY else 'Disabled (Using Gemini direct)'}")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(global_error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("map", map_command))
    app.add_handler(CommandHandler("issues", issues_command))
    app.add_handler(CommandHandler("rules", handle_rules_menu))
    app.add_handler(CommandHandler("emergency", emergency_command))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print("🤖 NSSF Smart Navigator Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
