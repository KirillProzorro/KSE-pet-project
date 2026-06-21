from flask import Flask, render_template, jsonify
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
from collections import defaultdict
import time

app = Flask(__name__)

ALERT_URL = "https://kyiv.digital/storage/air-alert/stats.html"
DAYS_UK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

# Глобальний кеш
CACHE = {"data": None, "timestamp": 0}
CACHE_TTL = 900  # 15 хвилин

def fetch_and_parse_alerts():
    response = requests.get(ALERT_URL, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text_data = soup.get_text(separator="\n")

    event_pattern = re.compile(r"(\d{2}:\d{2})\s+(\d{2}\.\d{2}\.\d{2})\s+(🔴|🟢)")
    events = []
    
    for time_str, date_str, emoji in event_pattern.findall(text_data):
        dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%y %H:%M")
        kind = "start" if emoji == "🔴" else "end"
        events.append((dt, kind))

    events.sort(key=lambda x: x[0])

    alerts = []
    pending_starts = []
    
    for dt, kind in events:
        if kind == "start":
            pending_starts.append(dt)
        else:
            matched = None
            for i in range(len(pending_starts) - 1, -1, -1):
                if pending_starts[i] < dt:
                    matched = pending_starts.pop(i)
                    break
            if matched is not None:
                duration_min = int((dt - matched).total_seconds() / 60)
                if 0 < duration_min <= 720:
                    alerts.append({"start": matched, "end": dt, "duration_min": duration_min})

    return alerts

def compute_stats(alerts):
    if not alerts:
        return {}

    total = len(alerts)
    total_min = sum(a["duration_min"] for a in alerts)
    avg_min = total_min / total if total else 0

    by_hour = defaultdict(int)
    by_weekday = defaultdict(int)
    by_month = defaultdict(int)
    durations = []

    for a in alerts:
        by_hour[a["start"].hour] += 1
        by_weekday[a["start"].weekday()] += 1
        month_key = a["start"].strftime("%Y-%m")
        by_month[month_key] += 1
        durations.append(a["duration_min"])

    buckets = {"0–30": 0, "30–60": 0, "60–120": 0, "120–180": 0, "180+": 0}
    for d in durations:
        if d < 30: buckets["0–30"] += 1
        elif d < 60: buckets["30–60"] += 1
        elif d < 120: buckets["60–120"] += 1
        elif d < 180: buckets["120–180"] += 1
        else: buckets["180+"] += 1

    sorted_months = sorted(by_month.keys())

    # Виправлення UTC для Києва (+3 години)
    today = (datetime.utcnow() + timedelta(hours=3)).date()
    last30 = {}
    for i in range(29, -1, -1):
        day = today - timedelta(days=i)
        last30[day.strftime("%d.%m")] = 0
        
    for a in alerts:
        key = a["start"].date().strftime("%d.%m")
        if key in last30:
            last30[key] += 1

    longest = max(alerts, key=lambda x: x["duration_min"])
    most_recent = max(alerts, key=lambda x: x["start"])

    return {
        "total": total,
        "total_hours": round(total_min / 60, 1),
        "avg_min": round(avg_min, 1),
        "max_duration_min": longest["duration_min"],
        "longest_start": longest["start"].strftime("%d.%m.%Y %H:%M"),
        "most_recent_start": most_recent["start"].strftime("%d.%m.%Y %H:%M"),
        "most_recent_duration": most_recent["duration_min"],
        "by_hour": {str(h): by_hour[h] for h in range(24)},
        "by_weekday": {DAYS_UK[d]: by_weekday[d] for d in range(7)},
        "by_month": {m: by_month[m] for m in sorted_months},
        "duration_buckets": buckets,
        "last30": last30,
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def stats():
    global CACHE
    now = time.time()
    
    if CACHE["data"] and (now - CACHE["timestamp"] < CACHE_TTL):
        return jsonify({"ok": True, "cached": True, "data": CACHE["data"]})
        
    try:
        alerts = fetch_and_parse_alerts()
        data = compute_stats(alerts)
        
        CACHE["data"] = data
        CACHE["timestamp"] = now
        
        return jsonify({"ok": True, "cached": False, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
