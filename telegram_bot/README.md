# NSSF Smart Navigator Bot & Web Map 🇰🇭

A token-optimized, bilingual (Khmer / English) Telegram civic assistant and interactive web map for National Social Security Fund (NSSF) members in Cambodia.

---

## 1. Hosting the Interactive Map on GitHub Pages

The repository includes a standalone [index.html](file:///c:/Main/Github/test/index.html) designed for **GitHub Pages**. It loads all nationwide facilities from `nssf.geojson` and highlights terminated clinics in red without requiring a local server.

### Steps to Host on GitHub Pages:

1. Initialize git and push your repository to GitHub:
   ```bash
   git init
   git add .
   git commit -m "feat: initial commit for NSSF bot and map"
   git branch -M main
   git remote add origin https://github.com/SthNoWork/nssf_telegram_bot.git
   git push -u origin main
   ```
2. In your GitHub repository:
   - Go to **Settings** -> **Pages**.
   - Under **Build and deployment**:
     - Source: `Deploy from a branch`
     - Branch: `main` / Folder: `/ (root)`
   - Click **Save**.
3. Your GitHub Pages URL:
   `https://sthnowork.github.io/nssf_telegram_bot/`
4. Set this URL in your `.env` (already the built-in default):
   ```env
   MAP_WEB_URL=https://sthnowork.github.io/nssf_telegram_bot/
   ```

---

## 2. Installation & Running the Telegram Bot

Install required dependencies:

```bash
py -m pip install python-telegram-bot google-genai python-dotenv
```

Configure `.env` in the project root:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
# Optional: primary model or comma-separated priority list (falls back across Gemini 2.0 Flash, Llama 3.3 70B, DeepSeek, Mistral, Qwen, and Auto)
OPENROUTER_MODEL=google/gemini-2.0-flash-001
GEMINI_API_KEY=your_gemini_api_key_here
MAP_WEB_URL=https://sthnowork.github.io/nssf_telegram_bot/
```

Start the bot:

```bash
py telegram_bot/bot.py
```

---

## 3. Advanced Features & Protections

* **In-App Telegram WebApp:** Tapping `🗺 View Map` opens the Leaflet map directly inside Telegram without opening an external browser.
* **Direct Local Clinic Search (Zero Tokens):** Typing any clinic or district name (e.g., *"Calmette"*, *"កាល់ម៉ែត"*, *"Kossamak"*, *"ពន្លឺពេជ្រ"*) matches local records and immediately flags terminated agreements without calling Gemini.
* **Location Memory Caching:** The user's last GPS pin is stored in `context.user_data["last_loc"]` for instant follow-up searches.
* **Rate-Limiting & Timeout Protection:** Limits rapid LLM queries to 10 per 2 minutes per user and enforces a 12s timeout guard.
* **Automated CI/CD Deployments:** Includes `.github/workflows/pages.yml` to automatically build and deploy `index.html` to GitHub Pages upon pushing to `main`.

---

## 4. Project Structure

```text
├── .github/
│   └── workflows/
│       └── pages.yml                     # Automated GitHub Pages CI/CD workflow
├── .gitignore                            # Strict exclusion of .env and temporary files
├── index.html                            # Standalone interactive map for GitHub Pages
├── verified_terminations.json            # 2024-2026 contract terminations
└── telegram_bot/
    ├── bot.py                            # Main Telegram bot application
    ├── map_server.py                     # Optional local Python HTTP map server
    ├── README.md                         # Project documentation
    ├── civic_guidelines_knowledge_base.md# Guidelines source notes
    └── data/
        ├── nssf.geojson                  # Nationwide NSSF partner clinics GeoJSON dataset
        └── civic_guidelines.json         # Structured NSSF coverage topics & keywords
```
