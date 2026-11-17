import json
import math
import os
from dotenv import load_dotenv
from google import genai
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
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
MAP_WEB_URL = os.getenv("MAP_WEB_URL", "http://localhost:8080/map")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


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
REPORTS_FILE = os.path.join(BASE_DIR, "community_reports.json")


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


# 3. Bilingual UI Strings (Strictly NSSF)
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
            "សូមជ្រើសរើសមុខងារខាងក្រោម៖"
        ),
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
        "btn_open_web_map": "🗺 បើកមើលផែនទីអន្តរកម្ម (Interactive Map)",
        "btn_open_gmaps": "🌐 បើកលើ Google Maps",
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
        "label_district": "ខណ្ឌ/ស្រុក៖",
        "label_phone": "ទូរស័ព្ទ៖",
        "open_maps": "បើកមើលទីតាំងលើ Google Maps",
        "btn_verify_ok": "✅ ទទួលប័ណ្ណធម្មតា",
        "btn_verify_bad": "❌ បដិសេធប័ណ្ណ",
        "verify_thanks": "🙏 អរគុណសម្រាប់ការរាយការណ៍!",
        "gemini_missing_key": "⚠️ សូមដាក់ `GEMINI_API_KEY` ក្នុង `.env` ដើម្បីប្រើប្រាស់មុខងារឆ្លើយតបឆ្លាតវៃ។",
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
            "Select an option below to begin:"
        ),
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
        "btn_open_web_map": "🗺 Open Interactive Web Map",
        "btn_open_gmaps": "🌐 Open Google Maps",
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
        "label_district": "District:",
        "label_phone": "Phone:",
        "open_maps": "Open in Google Maps",
        "btn_verify_ok": "✅ Card Accepted",
        "btn_verify_bad": "❌ Card Rejected",
        "verify_thanks": "🙏 Thank you for your community report!",
        "gemini_missing_key": "⚠️ Please add `GEMINI_API_KEY` to your `.env` to enable AI triage.",
    },
}


def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "km")


def t(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    lang = get_lang(context)
    return STRINGS.get(lang, STRINGS["km"]).get(key, STRINGS["km"].get(key, ""))


# 4. Keyboards
def get_main_menu(context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton(t(context, "btn_location"), request_location=True)],
        [
            KeyboardButton(t(context, "btn_issues")),
            KeyboardButton(t(context, "btn_rules")),
        ],
        [
            KeyboardButton(t(context, "btn_map")),
            KeyboardButton(t(context, "btn_lang")),
        ],
        [
            KeyboardButton(t(context, "btn_emergency")),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_issues_inline(context: ContextTypes.DEFAULT_TYPE):
    guidelines = load_guidelines()
    lang = get_lang(context)
    buttons = []
    btn_key = "btn_km" if lang == "km" else "btn_en"

    for issue_id, data in guidelines.items():
        label = data.get(btn_key, issue_id)
        buttons.append([InlineKeyboardButton(label, callback_data=f"issue:{issue_id}")])

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
    gmaps_url = "https://www.google.com/maps/search/NSSF+hospital+Cambodia"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(context, "btn_open_web_map"), url=MAP_WEB_URL)],
        [InlineKeyboardButton(t(context, "btn_open_gmaps"), url=gmaps_url)],
    ])
    await update.message.reply_text(
        t(context, "map_info"),
        parse_mode="Markdown",
        reply_markup=buttons,
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
    user_loc = update.message.location
    facilities = load_facilities()
    terminations = load_terminations()
    lang = get_lang(context)

    scored = []
    for fac in facilities:
        dist = haversine_km(user_loc.latitude, user_loc.longitude, fac["lat"], fac["lng"])
        term_notice = check_if_terminated(fac, terminations)
        scored.append((dist, fac, term_notice))

    scored.sort(key=lambda x: x[0])

    active_results = [item for item in scored if item[2] is None][:3]
    terminated_nearby = [item for item in scored if item[2] is not None and item[0] <= 15.0]

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
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t(context, "btn_verify_ok"), callback_data=f"verify_ok:{fac['id']}"),
                InlineKeyboardButton(t(context, "btn_verify_bad"), callback_data=f"verify_bad:{fac['id']}"),
            ]
        ])
        await update.message.reply_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=buttons,
            disable_web_page_preview=True,
        )


async def handle_rules_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(context, "btn_rule_worker"), callback_data="rule:worker")],
        [InlineKeyboardButton(t(context, "btn_rule_self"), callback_data="rule:self")],
        [InlineKeyboardButton(t(context, "btn_rule_maternity"), callback_data="rule:maternity")],
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
    lang = get_lang(context)

    # Language selection
    if data.startswith("lang:"):
        selected_lang = data.split(":")[1]
        context.user_data["lang"] = selected_lang
        ack_text = t(context, "lang_set")
        await query.message.reply_text(
            ack_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu(context),
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
            await query.message.reply_text(response_text, parse_mode="Markdown")
        return

    # Eligibility rules callbacks (Zero LLM Tokens)
    if data == "rule:worker":
        await query.edit_message_text(t(context, "rule_worker_text"), parse_mode="Markdown")
    elif data == "rule:self":
        await query.edit_message_text(t(context, "rule_self_text"), parse_mode="Markdown")
    elif data == "rule:maternity":
        await query.edit_message_text(t(context, "rule_maternity_text"), parse_mode="Markdown")

    # Community verification callbacks
    elif data.startswith("verify_"):
        action, fac_id = data.split(":")
        reports = {}
        if os.path.exists(REPORTS_FILE):
            try:
                with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                    reports = json.load(f)
            except Exception:
                reports = {}

        reports.setdefault(fac_id, {"ok": 0, "bad": 0})
        if action == "verify_ok":
            reports[fac_id]["ok"] += 1
        else:
            reports[fac_id]["bad"] += 1

        with open(REPORTS_FILE, "w", encoding="utf-8") as f:
            json.dump(reports, f, indent=2)

        thanks = t(context, "verify_thanks")
        await query.message.reply_text(
            f"{thanks} (✅ {reports[fac_id]['ok']} | ❌ {reports[fac_id]['bad']})"
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text messages using local keyword interception (Token Saving) before LLM fallback."""
    user_text = update.message.text.strip()
    user_lower = user_text.lower()
    lang = get_lang(context)

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

    # 6. TOKEN SAVING STRATEGY: Local Keyword Interception from civic_guidelines.json
    guidelines = load_guidelines()
    text_key = "text_km" if lang == "km" else "text_en"
    for _, issue_info in guidelines.items():
        keywords = issue_info.get("keywords", [])
        if any(kw.lower() in user_lower for kw in keywords):
            await update.message.reply_text(issue_info[text_key], parse_mode="Markdown")
            return

    # 7. Fallback to Gemini with compact prompt & output limits
    if not client:
        await update.message.reply_text(t(context, "gemini_missing_key"))
        return

    terminations = load_terminations()
    recent_terms = terminations[:5]
    term_summary = "\n".join(
        f"- {t_item.get('date', '')[:10]}: {t_item.get('text', '').splitlines()[0]}"
        for t_item in recent_terms
    )

    lang_instruction = "Answer strictly in Khmer." if lang == "km" else "Answer strictly in English."

    compact_system_prompt = f"""You are the NSSF Cambodia Smart Assistant. {lang_instruction}
Keep answers concise (max 3-4 bullet points).
Strict NSSF Rules:
1. Chronic diseases (diabetes, hypertension, cancer, kidney, hepatitis) covered ONLY at PUBLIC hospitals.
2. Routine dental cleaning/fillings, cosmetic surgery, and non-emergency checkups are EXCLUDED.
3. Emergency care is covered at the nearest hospital (call 1286/1297 for claims).
4. Never leave NSSF card with clinic staff upon discharge.
Recent clinic terminations:
{term_summary}
If user asks about a terminated clinic, immediately warn them."""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"{compact_system_prompt}\n\nUser Question: {user_text}",
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")


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

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("map", map_command))
    app.add_handler(CommandHandler("issues", issues_command))
    app.add_handler(CommandHandler("emergency", emergency_command))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print("🤖 NSSF Smart Navigator Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
