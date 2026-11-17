# Step-by-Step Instructions to Code the NSSF Civic Assistant Bot

## 1. Prerequisites & Setup

### Step 1.1: Create Your Telegram Bot Token

1. Open Telegram and message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot` and follow the prompts to name your bot (e.g., `NSSF Smart Navigator` / `@nssf_navigator_bot`).
3. Copy the **HTTP API Token** provided by BotFather.

### Step 1.2: Get Your Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/) and create an API key.

### Step 1.3: Install Required Python Packages

Open your VS Code terminal inside `C:\Main\Github\test\` and install the required libraries:

```bash
py -m pip install python-telegram-bot google-genai python-dotenv
```

### Step 1.4: Create a `.env` File

In your project folder (`C:\Main\Github\test\`), create a file named `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## 2. Project File Structure

Ensure your workspace has the following files in the same directory:

```text
C:\Main\Github\test\
├── .env                           # API keys (do not commit to Git)
├── bot.py                         # Main Telegram bot application
├── facilities_baseline.json       # Sample/ODC clinic coordinates & phone numbers
├── verified_terminations_2.json   # Your extracted 2024-2026 terminated clinics
└── community_reports.json         # Auto-created by bot for crowd-sourced feedback
```

---

## 3. Create Sample Baseline Clinic Data (`facilities_baseline.json`)

If you haven't formatted your ODC dataset into JSON yet, save this starter `facilities_baseline.json` in your folder so you can immediately test the **Nearest Clinic + Termination Warning** demo in Phnom Penh (specifically around Chbar Ampov, Daun Penh, and Toul Kork):

```json
[
  {
    "id": "fac_001",
    "name_km": "មន្ទីរពេទ្យកាល់ម៉ែត",
    "name_en": "Calmette Hospital",
    "type": "Public National Hospital",
    "district": "Daun Penh",
    "province": "Phnom Penh",
    "lat": 11.5816,
    "lng": 104.9179,
    "phone": "023 426 948",
    "chronic_care": true
  },
  {
    "id": "fac_002",
    "name_km": "មន្ទីរពេទ្យបង្អែកច្បារអំពៅ",
    "name_en": "Chbar Ampov Referral Hospital",
    "type": "Public Referral Hospital",
    "district": "Chbar Ampov",
    "province": "Phnom Penh",
    "lat": 11.5305,
    "lng": 104.9452,
    "phone": "023 720 451",
    "chronic_care": true
  },
  {
    "id": "fac_003",
    "name_km": "មន្ទីរពហុព្យាបាលច្បារអំពៅ",
    "name_en": "Chbar Ampov Polyclinic",
    "type": "Private Polyclinic",
    "district": "Chbar Ampov",
    "province": "Phnom Penh",
    "lat": 11.5318,
    "lng": 104.9425,
    "phone": "012 888 999",
    "chronic_care": false
  },
  {
    "id": "fac_004",
    "name_km": "មន្ទីរពេទ្យមិត្តភាពកម្ពុជា-ចិន ព្រះកុសុមៈ",
    "name_en": "Preah Kossamak Hospital",
    "type": "Public National Hospital",
    "district": "Toul Kork",
    "province": "Phnom Penh",
    "lat": 11.5641,
    "lng": 104.8894,
    "phone": "023 882 042",
    "chronic_care": true
  },
  {
    "id": "fac_005",
    "name_km": "មន្ទីរសម្រាកព្យាបាល ពន្លឺពេជ្រ",
    "name_en": "Ponleu Pich Clinic",
    "type": "Private Clinic",
    "district": "Sen Sok",
    "province": "Phnom Penh",
    "lat": 11.5698,
    "lng": 104.8752,
    "phone": "012 345 678",
    "chronic_care": false
  }
]
```

---

## 4. Complete Telegram Bot Code (`bot.py`)

Save the following code as `bot.py`. It implements all **4 core hackathon features**:

1. **📍 Nearest Active Clinic Finder** (with Haversine distance + live cross-check against `verified_terminations_2.json`).
2. **🩺 Symptom & Coverage AI Triage** (powered by Gemini 2.5 Flash with NSSF policy guardrails).
3. **📋 Eligibility & Rules Wizard** (inline buttons for Workers, Self-Employed, Maternity, Emergency).
4. **✅/❌ Community Status Verification** (inline buttons on clinic cards to crowd-source card acceptance).

```python
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

# 1. Load Environment Variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

FACILITIES_FILE = "facilities_baseline.json"
TERMINATIONS_FILE = "verified_terminations_2.json"
REPORTS_FILE = "community_reports.json"


# 2. Data Loading & Matching Helpers
def load_json(filepath, default):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def haversine_km(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two lat/lng coordinates."""
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


def check_if_terminated(facility_name_km: str, terminations: list) -> dict | None:
    """Cross-references a clinic's Khmer name against verified_terminations_2.json."""
    # Strip generic prefixes to match core clinic name accurately
    core_name = (
        facility_name_km.replace("មន្ទីរពហុព្យាបាល", "")
        .replace("មន្ទីរសម្រាកព្យាបាល", "")
        .replace("មន្ទីរពេទ្យ", "")
        .strip()
    )
    if not core_name:
        return None

    for item in terminations:
        if core_name in item.get("text", ""):
            return item
    return None


# 3. Persistent Main Menu Keyboard
def get_main_menu():
    keyboard = [
        [KeyboardButton("🏥 ស្វែងរកមន្ទីរពេទ្យជិតបំផុត (Share Location)", request_location=True)],
        [KeyboardButton("🩺 ពិនិត្យរោគសញ្ញា & សេវាធានារ៉ាប់រង"), KeyboardButton("📋 លក្ខខណ្ឌ & អត្ថប្រយោជន៍ ប.ស.ស.")],
        [KeyboardButton("🚨 សង្គ្រោះបន្ទាន់ / លេខ 1286")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# 4. Command & Button Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "សួស្តី! ស្វាគមន៍មកកាន់ **NSSF Smart Navigator** 🇰🇭\n"
        "ជំនួយការឆ្លាតវៃសម្រាប់សមាជិក ប.ស.ស.៖\n\n"
        "• 📍 **ស្វែងរកមន្ទីរពេទ្យជិតបំផុត** និងពិនិត្យមើលគ្លីនិកដែលត្រូវបានបញ្ចប់កិច្ចព្រមព្រៀង\n"
        "• 🩺 **ពិនិត្យរោគសញ្ញា** និងសេវាដែល ប.ស.ស. ធានារ៉ាប់រង\n"
        "• 📋 **ពិនិត្យលក្ខខណ្ឌ** ប្រាក់បំណាច់មាតុភាព និងបុគ្គលស្វ័យនិយោជន៍\n\n"
        "សូមជ្រើសរើសមុខងារខាងក្រោម ឬវាយសំណួររបស់អ្នកផ្ទាល់៖"
    )
    await update.message.reply_text(
        welcome_text, parse_mode="Markdown", reply_markup=get_main_menu()
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Feature 1: Nearest Active Clinic Finder + Real-Time Termination Alert."""
    user_loc = update.message.location
    facilities = load_json(FACILITIES_FILE, [])
    terminations = load_json(TERMINATIONS_FILE, [])

    scored = []
    for fac in facilities:
        dist = haversine_km(user_loc.latitude, user_loc.longitude, fac["lat"], fac["lng"])
        term_notice = check_if_terminated(fac["name_km"], terminations)
        scored.append((dist, fac, term_notice))

    scored.sort(key=lambda x: x[0])

    active_results = [item for item in scored if item[2] is None][:3]
    terminated_nearby = [item for item in scored if item[2] is not None and item[0] <= 10.0]

    # 1. Warn about nearby terminated clinics first!
    if terminated_nearby:
        warn_lines = ["⚠️ **ការជូនដំណឹងបន្ទាន់ (គ្លីនិកបញ្ចប់កិច្ចព្រមព្រៀងជិតអ្នក)៖**"]
        for dist, fac, notice in terminated_nearby:
            warn_lines.append(
                f"❌ **{fac['name_km']} ({fac['name_en']})** — ចម្ងាយ `{dist:.1f} km`\n"
                f"   ↳ ត្រូវបានបញ្ចប់/ផ្អាកកិច្ចព្រមព្រៀងកាលពីថ្ងៃទី `{notice['date'][:10]}` ([មើលសេចក្តីជូនដំណឹងផ្លូវការ]({notice['url']}))"
            )
        await update.message.reply_text(
            "\n".join(warn_lines), parse_mode="Markdown", disable_web_page_preview=True
        )

    # 2. Send top 3 nearest active clinics with community verification buttons
    await update.message.reply_text("✅ **មូលដ្ឋានសុខាភិបាលដៃគូ ប.ស.ស. ដែលនៅជិតអ្នកបំផុត៖**", parse_mode="Markdown")

    for dist, fac, _ in active_results:
        maps_url = f"https://www.google.com/maps?q={fac['lat']},{fac['lng']}"
        card_text = (
            f"🏥 **{fac['name_km']} ({fac['name_en']})**\n"
            f"🏷 ប្រភេទ៖ {fac['type']}\n"
            f"📍 ខណ្ឌ/ស្រុក៖ {fac['district']} ({dist:.1f} km)\n"
            f"📞 ទូរស័ព្ទ៖ `{fac['phone']}`\n"
            f"🗺 [បើកមើលទីតាំងលើ Google Maps]({maps_url})"
        )
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ ទទួលប័ណ្ណធម្មតា", callback_data=f"verify_ok:{fac['id']}"),
                InlineKeyboardButton("❌ បដិសេធប័ណ្ណ", callback_data=f"verify_bad:{fac['id']}"),
            ]
        ])
        await update.message.reply_text(
            card_text, parse_mode="Markdown", reply_markup=buttons, disable_web_page_preview=True
        )


async def handle_rules_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Feature 3: Instant Eligibility & Document Checklist Wizard."""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("👷 កម្មករនិយោជិត (Formal Worker)", callback_data="rule:worker")],
        [InlineKeyboardButton("🛵 បុគ្គលស្វ័យនិយោជន៍ & អ្នកក្នុងបន្ទុក (15,600៛)", callback_data="rule:self")],
        [InlineKeyboardButton("🤰 អត្ថប្រយោជន៍មាតុភាព (សម្រាលកូន)", callback_data="rule:maternity")],
    ])
    await update.message.reply_text(
        "📋 **សូមជ្រើសរើសប្រភេទសមាជិក ដើម្បីពិនិត្យលក្ខខណ្ឌ និងឯកសារតម្រូវ៖**",
        parse_mode="Markdown",
        reply_markup=buttons,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "rule:worker":
        text = (
            "👷 **លក្ខខណ្ឌសម្រាប់កម្មករនិយោជិត (Formal Workers)៖**\n"
            "• **សិទ្ធិព្យាបាលឥតគិតថ្លៃ៖** និយោជកបានបង់ភាគទាន **២ខែជាប់គ្នា** ឬ **៦ខែក្នុង ១២ខែចុងក្រោយ**។\n"
            "• **ពេលឈប់ធ្វើការ៖** នៅតែអាចប្រើប្រាស់ប័ណ្ណ ប.ស.ស. បាន **២ខែបន្ទាប់**។\n"
            "• **ប្រាក់បំណាច់ ៧០% ពេលឈឺ៖** សម្រាកព្យាបាលនៅពេទ្យដៃគូចាប់ពី **៨ថ្ងៃឡើងទៅ**។"
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "rule:self":
        text = (
            "🛵 **បុគ្គលស្វ័យនិយោជន៍ & អ្នកក្នុងបន្ទុក (Self-Employed & Dependents)៖**\n"
            "• **ភាគទាន៖** ចំណាយត្រឹមតែ **១៥,៦០០ រៀល/ខែ**។\n"
            "• **សិទ្ធិប្រើប្រាស់សេវា៖** ត្រូវបង់ភាគទានយ៉ាងតិច **២ខែជាប់គ្នា**។\n"
            "• **ឯកសារទៅពេទ្យ៖** ប័ណ្ណសមាជិក ប.ស.ស. + អត្តសញ្ញាណប័ណ្ណសញ្ជាតិខ្មែរច្បាប់ដើម។"
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "rule:maternity":
        text = (
            "🤰 **អត្ថប្រយោជន៍មាតុភាព (Maternity Benefits)៖**\n"
            "• **លក្ខខណ្ឌទទួលប្រាក់បំណាច់៖** បានបង់ភាគទានយ៉ាងតិច **៩ខែ ក្នុងអំឡុង ១២ខែចុងក្រោយ** គិតត្រឹមខែសម្រាល។\n"
            "• **កម្មការិនីវិស័យឯកជន៖** ទទួលបានប្រាក់បំណាច់ប្រចាំថ្ងៃ **៧០% រយៈពេល ៩០ថ្ងៃ** + ប្រាក់ឧបត្ថម្ភរាជរដ្ឋាភិបាល។\n"
            "• **ស្ត្រីស្វ័យនិយោជន៍៖** ទទួលបានប្រាក់បំណាច់មាតុភាព **១,២៦០,០០០ រៀល**។"
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data.startswith("verify_"):
        action, fac_id = data.split(":")
        reports = load_json(REPORTS_FILE, {})
        reports.setdefault(fac_id, {"ok": 0, "bad": 0})
        if action == "verify_ok":
            reports[fac_id]["ok"] += 1
        else:
            reports[fac_id]["bad"] += 1
        with open(REPORTS_FILE, "w", encoding="utf-8") as f:
            json.dump(reports, f, indent=2)
        await query.message.reply_text(
            f"🙏 អរគុណសម្រាប់ការរាយការណ៍! (ទទួលប័ណ្ណ: {reports[fac_id]['ok']} | បញ្ហា: {reports[fac_id]['bad']})"
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Feature 2: AI Triage & Terminated Clinic Lookup via Gemini."""
    user_text = update.message.text.strip()

    if "ពិនិត្យលក្ខខណ្ឌ" in user_text or "លក្ខខណ្ឌ &" in user_text:
        await handle_rules_menu(update, context)
        return

    if "សង្គ្រោះបន្ទាន់" in user_text or "1286" in user_text:
        await update.message.reply_text(
            "🚨 **ករណីសង្គ្រោះបន្ទាន់ និងបណ្តឹងតវ៉ា៖**\n"
            "• ទូរស័ព្ទបន្ទាន់ ប.ស.ស. (២៤ម៉ោង)៖ `1286` ឬ `1297`\n"
            "• ករណីសង្គ្រោះបន្ទាន់អាចទៅមន្ទីរពេទ្យជិតបំផុត (ទោះមិនមែនដៃគូ) រួចស្នើសុំទូទាត់មក ប.ស.ស. វិញតាមក្រោយ។\n"
            "• **សំខាន់៖** ហាមទុកប័ណ្ណ ប.ស.ស. នៅមន្ទីរពេទ្យពេលចេញពីពេទ្យដាច់ខាត!",
            parse_mode="Markdown",
        )
        return

    if "ពិនិត្យរោគសញ្ញា" in user_text:
        await update.message.reply_text(
            "🩺 សូមវាយបញ្ចូលរោគសញ្ញា ឬឈ្មោះគ្លីនិកដែលអ្នកចង់ពិនិត្យ (ឧ. 'ឈឺធ្មេញ', 'ជំងឺទឹកនោមផ្អែម', ឬ 'មន្ទីរពហុព្យាបាលច្បារអំពៅ')៖"
        )
        return

    # AI Triage + Grounded Context
    terminations = load_json(TERMINATIONS_FILE, [])
    term_summary = "\n".join(
        f"- Date {t['date'][:10]}: {t['text'].splitlines()[0]} (URL: {t['url']})"
        for t in terminations
    )

    system_prompt = f"""You are the NSSF Cambodia Smart Civic Assistant. Answer in the same language the user writes in (Khmer or English). Keep answers concise, warm, and bulleted.

Official NSSF Rules to strictly enforce:
1. Chronic diseases (diabetes, hypertension, heart failure, hepatitis B/C, kidney disease, cancer) are ONLY covered at PUBLIC hospitals (មន្ទីរពេទ្យរដ្ឋ) using essential medicines, NOT private clinics.
2. Routine dental cleaning/fillings, cosmetic surgery, artificial limbs/implants, fertility treatment, and general checkups without symptoms are EXCLUDED from NSSF coverage.
3. Emergency care is covered at the nearest hospital even if non-partner (reimbursed later via 1286).
4. Never leave your NSSF card with hospital staff upon discharge.

Official Terminated/Suspended Clinics List (2024-2026):
{term_summary}

If the user asks about a clinic on the terminated list, warn them immediately with the date and link."""

    if not client:
        await update.message.reply_text("⚠️ សូមដាក់ `GEMINI_API_KEY` ក្នុង `.env` ដើម្បីប្រើប្រាស់មុខងារឆ្លើយតបឆ្លាតវៃ។")
        return

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{system_prompt}\n\nUser Question: {user_text}",
    )
    await update.message.reply_text(response.text)


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Error: Set TELEGRAM_BOT_TOKEN in your .env file first!")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print("🤖 NSSF Smart Navigator Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
```

---

## 5. How to Test Your Bot for the Live Demo

1. Run `py bot.py` in your VS Code terminal.
2. Open your bot in Telegram and send `/start`.
3. **Demo Step 1 (Location + Termination Warning):** Share a location pin in **Chbar Ampov** (or Sen Sok). Watch the bot immediately warn you with a red alert that **មន្ទីរពហុព្យាបាលច្បារអំពៅ (Chbar Ampov Polyclinic)** had its contract terminated on `2026-09-17` with a direct link to the Telegram notice, and then recommend **Chbar Ampov Referral Hospital** instead!
4. **Demo Step 2 (Symptom Coverage Triage):** Type _"I have diabetes, can I go to a private clinic?"_ or _"ខ្ញុំឈឺធ្មេញ ចង់ទៅប៉ះធ្មេញប្រើប័ណ្ណ ប.ស.ស. បានទេ?"_ and watch Gemini enforce official NSSF public-hospital rules and dental exclusions.
5. **Demo Step 3 (Eligibility Checklists):** Tap **`📋 លក្ខខណ្ឌ & អត្ថប្រយោជន៍ ប.ស.ស.`** to show instant, zero-hallucination rules for workers, self-employed members (15,600៛/month), and maternity benefits.
