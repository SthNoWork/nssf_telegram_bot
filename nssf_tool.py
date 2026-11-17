import json
import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

CHANNEL = "nssfcambodia"
DEFAULT_INPUT = "result.json"
CLEANED_FILE = "cleaned_nssf.json"
FILTERED_FILE = "verified_terminations.json"
DOWNLOAD_DIR = "downloaded_posts"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}


def extract_text(raw_text) -> str:
    """Safely extracts plain text from Telegram Desktop's string or list-of-entities format."""
    if isinstance(raw_text, list):
        return "".join(
            part if isinstance(part, str) else part.get("text", "")
            for part in raw_text
        )
    return str(raw_text or "")


def extract_links_from_entities(msg: dict) -> list[str]:
    """Extracts any URLs embedded inside Telegram Desktop's text_entities."""
    links = []
    for ent in msg.get("text_entities", []):
        if isinstance(ent, dict):
            if ent.get("type") == "link" and ent.get("text"):
                links.append(ent["text"])
            elif ent.get("type") == "text_link" and ent.get("href"):
                links.append(ent["href"])
    return links


def option_1_clean_json(input_file: str):
    """Option 1: Strips 100% irrelevant noise while keeping all health/agreement posts."""
    if not os.path.exists(input_file):
        print(f"❌ File not found: {input_file}")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    keywords = (
        "កិច្ចព្រមព្រៀង",      # Agreement
        "មូលដ្ឋានសុខាភិបាល",   # Health facility
        "មន្ទីរសម្រាកព្យាបាល",  # Clinic / Polyclinic
        "មន្ទីរពហុព្យាបាល",    # Polyclinic
        "គ្លីនិក",             # Clinic
        "មន្ទីរពេទ្យ",         # Hospital
    )

    kept = []
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        text = extract_text(m.get("text", ""))
        if any(kw in text for kw in keywords):
            kept.append({
                "id": m.get("id"),
                "date": m.get("date"),
                "url": f"https://t.me/{CHANNEL}/{m.get('id')}",
                "has_photo": "photo" in m,
                "embedded_links": extract_links_from_entities(m),
                "text": text.strip(),
            })

    with open(CLEANED_FILE, "w", encoding="utf-8") as out:
        json.dump(
            {"channel": data.get("name", CHANNEL), "count": len(kept), "messages": kept},
            out,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n✅ Option 1 Complete: Kept {len(kept)} relevant posts out of {len(data.get('messages', []))} total.")
    print(f"   Saved to: {CLEANED_FILE}")


def option_2_filter_terminations(source_file: str):
    """Option 2: Filters strictly for clinic contract terminations/suspensions."""
    if not os.path.exists(source_file):
        print(f"❌ File not found: {source_file}")
        return

    with open(source_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    filtered = []

    for m in messages:
        # Works on both raw result_3.json and cleaned_nssf.json
        if "type" in m and m.get("type") != "message":
            continue

        text = extract_text(m.get("text", ""))
        is_terminated = any(
            kw in text
            for kw in ["បញ្ចប់កិច្ចព្រមព្រៀង", "ផ្អាកកិច្ចព្រមព្រៀង", "លុបកិច្ចព្រមព្រៀង"]
        )

        if is_terminated:
            msg_id = m.get("id")
            filtered.append({
                "id": msg_id,
                "date": m.get("date", ""),
                "status": "TERMINATED_OR_SUSPENDED",
                "url": f"https://t.me/{CHANNEL}/{msg_id}",
                "embedded_links": m.get("embedded_links") or extract_links_from_entities(m),
                "text": text.strip(),
            })

    filtered.sort(key=lambda x: x["date"], reverse=True)

    with open(FILTERED_FILE, "w", encoding="utf-8") as out:
        json.dump(filtered, out, ensure_ascii=False, indent=2)

    print(f"\n✅ Option 2 Complete: Extracted {len(filtered)} verified termination notices.")
    print(f"   Saved to: {FILTERED_FILE}")


def download_file(url: str, save_path: str) -> bool:
    """Downloads an image or PDF file cleanly."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, stream=True)
        if r.status_code == 200:
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception as e:
        print(f"   ⚠️ Download error for {url}: {e}")
    return False


def option_3_download_full_posts(filtered_file: str = FILTERED_FILE):
    """Option 3: Fetches each filtered post's web preview to download photos, PDFs, and full links."""
    if not os.path.exists(filtered_file):
        print(f"❌ File not found: {filtered_file}. Run Option 2 first.")
        return

    with open(filtered_file, "r", encoding="utf-8") as f:
        items = json.load(f)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    enriched_records = []

    print(f"\n⬇️ Downloading full content (photos, links, PDFs) for {len(items)} posts...")

    for item in items:
        msg_id = item["id"]
        # Use Telegram's public single-post embed/web preview URL
        preview_url = f"https://t.me/{CHANNEL}/{msg_id}?embed=1&mode=tme"
        post_folder = os.path.join(DOWNLOAD_DIR, f"post_{msg_id}")
        os.makedirs(post_folder, exist_ok=True)

        print(f"\n[Post {msg_id}] Fetching {preview_url}")
        resp = requests.get(preview_url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. Extract all external links in the post
        links = set(item.get("embedded_links", []))
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "t.me/" not in href and "telegram.org" not in href:
                links.add(href)

        # 2. Extract and download attached poster photos (background-image URLs in Telegram embed)
        downloaded_photos = []
        photo_wraps = soup.find_all("a", class_="tgme_widget_message_photo_wrap")
        for idx, wrap in enumerate(photo_wraps, start=1):
            style = wrap.get("style", "")
            match = re.search(r"background-image:url\('([^']+)'\)", style)
            if match:
                img_url = match.group(1)
                img_path = os.path.join(post_folder, f"photo_{idx}.jpg")
                if download_file(img_url, img_path):
                    downloaded_photos.append(img_path)
                    print(f"   🖼️ Saved photo -> {img_path}")

        # 3. Download any direct PDF links found
        downloaded_pdfs = []
        for idx, link in enumerate(links, start=1):
            if ".pdf" in link.lower():
                pdf_name = os.path.basename(urllib.parse.urlparse(link).path) or f"doc_{idx}.pdf"
                pdf_path = os.path.join(post_folder, pdf_name)
                if download_file(link, pdf_path):
                    downloaded_pdfs.append(pdf_path)
                    print(f"   📄 Saved PDF -> {pdf_path}")

        # 4. Save full metadata & text inside the post's folder
        record = {
            **item,
            "all_links": sorted(links),
            "downloaded_photos": downloaded_photos,
            "downloaded_pdfs": downloaded_pdfs,
        }
        with open(os.path.join(post_folder, "details.json"), "w", encoding="utf-8") as df:
            json.dump(record, df, ensure_ascii=False, indent=2)

        enriched_records.append(record)
        time.sleep(1.0)

    summary_path = os.path.join(DOWNLOAD_DIR, "all_downloaded_summary.json")
    with open(summary_path, "w", encoding="utf-8") as sf:
        json.dump(enriched_records, sf, ensure_ascii=False, indent=2)

    print(f"\n✅ Option 3 Complete! All photos, links, and details saved inside '{DOWNLOAD_DIR}/'.")


def main():
    while True:
        print("\n==========================================")
        print("   NSSF Telegram Export Processor")
        print("==========================================")
        print("1. Clean raw JSON (strip noise -> cleaned_nssf.json)")
        print("2. Filter cleaned/raw JSON (extract terminations -> verified_terminations.json)")
        print("3. Download full message assets (photos, PDFs, links -> downloaded_posts/)")
        print("4. Exit")
        choice = input("\nSelect an option (1-4): ").strip()

        if choice == "1":
            file_in = input(f"Input JSON filename [{DEFAULT_INPUT}]: ").strip() or DEFAULT_INPUT
            option_1_clean_json(file_in)
        elif choice == "2":
            default_src = CLEANED_FILE if os.path.exists(CLEANED_FILE) else DEFAULT_INPUT
            file_in = input(f"Input JSON filename [{default_src}]: ").strip() or default_src
            option_2_filter_terminations(file_in)
        elif choice == "3":
            file_in = input(f"Filtered JSON filename [{FILTERED_FILE}]: ").strip() or FILTERED_FILE
            option_3_download_full_posts(file_in)
        elif choice == "4":
            print("Exiting.")
            break
        else:
            print("Invalid choice. Please enter 1, 2, 3, or 4.")


if __name__ == "__main__":
    main()