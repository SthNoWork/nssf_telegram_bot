import http.server
import json
import math
import os
import socket
import socketserver
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
DATA_FILE = os.path.join(BASE_DIR, "data", "nssf.geojson")
if not os.path.exists(DATA_FILE):
    DATA_FILE = os.path.join(BASE_DIR, "data", "nssf.json")

TERMINATIONS_FILE = os.path.join(PARENT_DIR, "verified_terminations.json")
if not os.path.exists(TERMINATIONS_FILE):
    TERMINATIONS_FILE = os.path.join(BASE_DIR, "verified_terminations.json")

PORT = int(os.getenv("PORT", "8080"))


def load_terminations():
    if os.path.exists(TERMINATIONS_FILE):
        try:
            with open(TERMINATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading terminations: {e}")
    return []


def check_if_terminated(name_km, name_en, terminations):
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


def get_facilities_with_status():
    if not os.path.exists(DATA_FILE):
        return []

    terminations = load_terminations()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"Error reading {DATA_FILE}: {e}")
        return []

    features = []
    if isinstance(raw, dict) and raw.get("type") == "FeatureCollection":
        features = raw.get("features", [])
    elif isinstance(raw, list):
        for idx, item in enumerate(raw):
            features.append({
                "type": "Feature",
                "id": item.get("id", f"fac_{idx}"),
                "geometry": {
                    "type": "Point",
                    "coordinates": [item.get("lng", 0), item.get("lat", 0)],
                },
                "properties": item,
            })

    output_features = []
    for feat in features:
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

        name_en = props.get("name") or props.get("name_en", "NSSF Partner Facility")
        name_km = props.get("name_km", name_en)
        phone = (
            props.get("contact")
            or props.get("ambulance")
            or props.get("nssf_agency")
            or props.get("phone")
            or "N/A"
        )
        first_phone = phone.split("\n")[0].strip() if phone else "N/A"

        term_notice = check_if_terminated(name_km, name_en, terminations)

        output_features.append({
            "type": "Feature",
            "id": feat.get("id"),
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name_km": name_km,
                "name_en": name_en,
                "district": props.get("district", "N/A"),
                "province": props.get("province", "Cambodia"),
                "phone": first_phone,
                "lat": lat,
                "lng": lng,
                "is_terminated": term_notice is not None,
                "term_date": term_notice.get("date", "")[:10] if term_notice else None,
                "term_url": term_notice.get("url") if term_notice else None,
            },
        })

    return output_features


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="km">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NSSF Facilities & Termination Map 🇰🇭</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
  <link href="https://fonts.googleapis.com/css2?family=Content:wght@400;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Inter', 'Content', sans-serif; background: #0e1621; color: #fff; height: 100vh; display: flex; flex-direction: column; }
    header { background: #17212b; padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #232e3c; }
    header h1 { font-size: 1.1rem; color: #2ea6ff; display: flex; align-items: center; gap: 8px; }
    .controls { display: flex; gap: 12px; align-items: center; }
    .search-input { background: #0e1621; border: 1px solid #232e3c; color: #fff; padding: 8px 14px; border-radius: 8px; font-size: 0.9rem; outline: none; width: 220px; }
    .search-input:focus { border-color: #2ea6ff; }
    .badge { padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; }
    .badge-active { background: rgba(46, 204, 113, 0.2); color: #2ecc71; border: 1px solid #2ecc71; }
    .badge-term { background: rgba(231, 76, 60, 0.2); color: #e74c3c; border: 1px solid #e74c3c; }
    #map { flex: 1; width: 100%; height: 100%; }
    .custom-popup .leaflet-popup-content-wrapper { background: #17212b; color: #fff; border-radius: 10px; border: 1px solid #232e3c; }
    .custom-popup .leaflet-popup-tip { background: #17212b; }
    .popup-title { font-size: 1rem; font-weight: bold; margin-bottom: 6px; color: #2ea6ff; }
    .popup-meta { font-size: 0.85rem; color: #a4b0be; line-height: 1.5; }
    .term-box { background: rgba(231, 76, 60, 0.15); border: 1px solid #e74c3c; padding: 8px; border-radius: 6px; margin-top: 8px; font-size: 0.82rem; color: #ff6b6b; }
    .term-box a { color: #2ea6ff; text-decoration: underline; }
    .btn-nav { display: inline-block; margin-top: 10px; padding: 6px 12px; background: #2ea6ff; color: #fff; text-decoration: none; border-radius: 6px; font-size: 0.82rem; font-weight: 600; }
  </style>
</head>
<body>
  <header>
    <h1>🏥 NSSF Partner Facilities 🇰🇭</h1>
    <div class="controls">
      <input type="text" id="search" class="search-input" placeholder="🔍 Search clinic / district..." />
      <span class="badge badge-active" id="active-count">0 Active</span>
      <span class="badge badge-term" id="term-count">0 Terminated</span>
    </div>
  </header>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
  <script>
    const map = L.map('map').setView([11.5564, 104.9282], 8);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors'
    }).addTo(map);

    const greenIcon = new L.Icon({
      iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png',
      shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
      iconSize: [25, 41],
      iconAnchor: [12, 41],
      popupAnchor: [1, -34],
      shadowSize: [41, 41]
    });

    const redIcon = new L.Icon({
      iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
      shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
      iconSize: [25, 41],
      iconAnchor: [12, 41],
      popupAnchor: [1, -34],
      shadowSize: [41, 41]
    });

    const markersCluster = L.markerClusterGroup();
    let allMarkers = [];

    fetch('/api/facilities')
      .then(res => res.json())
      .then(features => {
        let activeTotal = 0;
        let termTotal = 0;

        features.forEach(f => {
          const p = f.properties;
          const isTerm = p.is_terminated;
          if (isTerm) termTotal++; else activeTotal++;

          const icon = isTerm ? redIcon : greenIcon;
          const marker = L.marker([p.lat, p.lng], { icon });

          let termHtml = '';
          if (isTerm) {
            termHtml = `<div class="term-box">
              ⚠️ <strong>កិច្ចព្រមព្រៀងត្រូវបានបញ្ចប់/ផ្អាក!</strong><br>
              ថ្ងៃទី៖ ${p.term_date || 'N/A'}<br>
              ${p.term_url ? `<a href="${p.term_url}" target="_blank">🔗 មើលសេចក្តីជូនដំណឹងផ្លូវការ</a>` : ''}
            </div>`;
          }

          const popupContent = `
            <div class="custom-popup">
              <div class="popup-title">${p.name_km}</div>
              <div class="popup-meta">
                <div><strong>EN:</strong> ${p.name_en}</div>
                <div><strong>📍 ទីតាំង៖</strong> ${p.district}, ${p.province}</div>
                <div><strong>📞 ទូរស័ព្ទ៖</strong> ${p.phone}</div>
                ${termHtml}
                <a class="btn-nav" href="https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lng}" target="_blank">🗺 បើកមើលផ្លូវ (Google Maps)</a>
              </div>
            </div>
          `;

          marker.bindPopup(popupContent);
          marker.featureData = p;
          markersCluster.addLayer(marker);
          allMarkers.push(marker);
        });

        map.addLayer(markersCluster);
        document.getElementById('active-count').innerText = `${activeTotal} Active`;
        document.getElementById('term-count').innerText = `${termTotal} Terminated`;
      });

    // Search filter
    document.getElementById('search').addEventListener('input', function(e) {
      const q = e.target.value.toLowerCase().trim();
      markersCluster.clearLayers();
      allMarkers.forEach(m => {
        const p = m.featureData;
        const textMatch = (
          p.name_km.toLowerCase().includes(q) ||
          p.name_en.toLowerCase().includes(q) ||
          p.district.toLowerCase().includes(q) ||
          p.province.toLowerCase().includes(q)
        );
        if (textMatch) {
          markersCluster.addLayer(m);
        }
      });
    });
  </script>
</body>
</html>
"""


class MapServerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ["/", "/map"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/facilities":
            facilities = get_facilities_with_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(facilities, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        # Concise logging
        print(f"[MapServer] {self.address_string()} - {format % args}")


def run_server():
    server = socketserver.TCPServer(("0.0.0.0", PORT), MapServerHandler)
    server.allow_reuse_address = True
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)

    print("=" * 60)
    print(f"🗺 NSSF Interactive Map Server running on Port {PORT}")
    print(f"📍 Local URL:     http://localhost:{PORT}")
    print(f"🌐 LAN URL:       http://{local_ip}:{PORT}")
    print(
        "🚀 Public Access: Run 'cloudflared tunnel --url http://localhost:%d' or ngrok"
        % PORT
    )
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down map server.")
        server.server_close()


if __name__ == "__main__":
    run_server()
