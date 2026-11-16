#!/usr/bin/env python3
"""Postet fällige Einträge aus schedule.json auf Instagram (Graph API).
Modi: python post.py verify  -> prüft nur Token und Konto
      python post.py run     -> postet fällige Beiträge
Umgebung: IG_TOKEN (Secret), IG_USER_ID, GRAPH_VERSION (optional)
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["IG_TOKEN"].strip()
IG_ID = os.environ.get("IG_USER_ID", "17841438511432630").strip()
VER = os.environ.get("GRAPH_VERSION", "v25.0")
BASE = f"https://graph.facebook.com/{VER}"
RAW = "https://raw.githubusercontent.com/MiaK2001/mia-content/main/media/"
TZ = ZoneInfo("Europe/Berlin")


def call(method, path, params=None):
    params = dict(params or {})
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{BASE}/{path}"
    if method == "GET":
        url += "?" + data.decode()
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        body = body.replace(TOKEN, "***")
        raise SystemExit(f"API-Fehler {e.code} bei {path}: {body}")


def wait_ready(container_id, tries=40):
    for _ in range(tries):
        st = call("GET", container_id, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Container {container_id} fehlgeschlagen: {st}")
        time.sleep(6)
    raise SystemExit(f"Container {container_id} nicht rechtzeitig fertig")


def publish(item):
    files = [RAW + f for f in item["files"]]
    if item["type"] == "image":
        c = call("POST", f"{IG_ID}/media", {"image_url": files[0], "caption": item["caption"]})["id"]
    elif item["type"] == "reel":
        c = call("POST", f"{IG_ID}/media", {
            "media_type": "REELS", "video_url": files[0],
            "caption": item["caption"], "share_to_feed": "true"})["id"]
    elif item["type"] == "carousel":
        kids = []
        for u in files:
            kid = call("POST", f"{IG_ID}/media", {"image_url": u, "is_carousel_item": "true"})["id"]
            wait_ready(kid)
            kids.append(kid)
        c = call("POST", f"{IG_ID}/media", {
            "media_type": "CAROUSEL", "children": ",".join(kids),
            "caption": item["caption"]})["id"]
    else:
        raise SystemExit("Unbekannter Typ: " + item["type"])
    wait_ready(c)
    return call("POST", f"{IG_ID}/media_publish", {"creation_id": c})["id"]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "verify":
        info = call("GET", IG_ID, {"fields": "username,media_count,followers_count"})
        print("Konto OK:", info)
        return
    schedule = json.load(open("schedule.json", encoding="utf-8"))
    posted = json.load(open("posted.json", encoding="utf-8"))
    done = {p["id"] for p in posted}
    now = datetime.now(TZ)
    for item in schedule:
        if item["id"] in done:
            continue
        due = datetime.fromisoformat(f'{item["date"]}T{item["time"]}:00').replace(tzinfo=TZ)
        if now < due:
            continue
        print("Poste", item["id"])
        media_id = publish(item)
        posted.append({"id": item["id"], "media_id": media_id, "at": now.isoformat()})
        json.dump(posted, open("posted.json", "w", encoding="utf-8"), indent=2)
        print("Gepostet:", item["id"], media_id)
        break  # höchstens ein Beitrag pro Lauf


if __name__ == "__main__":
    main()
