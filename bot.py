import tweepy
import requests
import time
import json
import os
import datetime

# ================================================================
# KONFIGURATION
# ================================================================

TWITTER_CONFIGS = {
    "normiesART": {
        "api_key":             "vU9YN0PH66FlZ7KaH67HRVJ8g",
        "api_secret":          "XmZk8Kt0KccumPfSzfOJKWU1ht2VvHdc3CHZFmVVq4WIfV2M7e",
        "access_token":        "2039233498058223617-rLy3FV7pKSE4T5FXdEu5I5FJMkidtc",
        "access_token_secret": "Rbfu43ufW1Lwz7CuHtTvX7J24CDw2R37EZ8i6TfZGWUs0",
        "bearer_token":        "AAAAAAAAAAAAAAAAAAAAAHPE8gEAAAAAoe16YJk%2FOlLYaWjuFGGsEoDVKzc%3DUsMN7pXsSPneUOx8HI9t6dFsIZupoxMHjhpKb0gGj2Q0VebE7k",
    },
}

COLLECTIONS = [
    {
        "name":              "normiesART",
        "slug":              "normies",
        "contract":          "0x9435208ca4a8dfba4bbffc52bd4d65fac3a87fd4",
        "twitter_account":   "normiesART",
        "discord_webhook":   "https://discord.com/api/webhooks/DEIN_WEBHOOK",
        "whale_threshold":   0.5,
        "sweep_threshold":   3,
        "floor_alert_pct":   5,
        "volume_milestones": [10, 25, 50, 100, 250, 500],
    },
]

WHALE_WALLETS = []

OPENSEA_API_KEY = "0439IVxW5fla2biNld4HmYggLYuhKqM8dVcewCw1xGWmNZQY"
OPENSEA_HEADERS = {
    "accept":    "application/json",
    "x-api-key": OPENSEA_API_KEY,
}

DB_FILE           = "nft_bot_pro.json"
CHECK_INTERVAL    = 60
FLOOR_CHECK_EVERY = 5
DAILY_HOUR        = 20


# ================================================================
# DATENBANK
# ================================================================

def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({"collections": {}, "whale_wallets": WHALE_WALLETS}, f, indent=2)
    with open(DB_FILE, "r+") as f:
        db = json.load(f)
        for col in COLLECTIONS:
            cid = col["slug"]
            if cid not in db["collections"]:
                db["collections"][cid] = {
                    "last_tx":               None,
                    "last_floor":            None,
                    "volume_milestones_hit": [],
                    "total_volume":          0.0,
                    "history":               {}
                }
        f.seek(0)
        json.dump(db, f, indent=2)
        f.truncate()

def load_db():
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


# ================================================================
# TWITTER
# ================================================================

_twitter_clients = {}

def get_twitter_client(account_name):
    if account_name not in _twitter_clients:
        cfg = TWITTER_CONFIGS[account_name]
        client = tweepy.Client(
            cfg["bearer_token"], cfg["api_key"], cfg["api_secret"],
            cfg["access_token"], cfg["access_token_secret"]
        )
        _twitter_clients[account_name] = client
    return _twitter_clients[account_name]


# ================================================================
# POSTING
# ================================================================

def post_to_twitter(account_name, text):
    try:
        get_twitter_client(account_name).create_tweet(text=text)
        print(f"[Twitter] OK: {text[:60]}...")
    except Exception as e:
        print(f"[Twitter] Fehler: {e}")

def post_to_discord(webhook_url, embed):
    if not webhook_url or "DEIN_WEBHOOK" in webhook_url:
        return
    try:
        r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 204):
            print(f"[Discord] OK: {embed.get('title', '')}")
    except Exception as e:
        print(f"[Discord] Fehler: {e}")

def post_everywhere(col, tw_text, disc_embed):
    post_to_twitter(col["twitter_account"], tw_text)
    post_to_discord(col["discord_webhook"], disc_embed)


# ================================================================
# OPENSEA API v2
# ================================================================

def get_recent_sales(slug, limit=50):
    url = f"https://api.opensea.io/api/v2/events/collection/{slug}"
    try:
        r    = requests.get(url, headers=OPENSEA_HEADERS, params={"event_type": "sale", "limit": limit}, timeout=10)
        data = r.json()
    except Exception as e:
        print(f"[OpenSea] Sales Fehler: {e}")
        return []

    sales = []
    for event in data.get("asset_events", []):
        try:
            payment  = event.get("payment", {})
            quantity = int(payment.get("quantity", "0"))
            decimals = int(payment.get("decimals", 18))
            price    = quantity / (10 ** decimals)
            nft      = event.get("nft", {})
            sales.append({
                "tx":       event.get("transaction", ""),
                "buyer":    event.get("buyer", ""),
                "tokenId":  nft.get("identifier", "?"),
                "priceEth": price,
                "url":      nft.get("opensea_url", ""),
            })
        except Exception:
            continue
    return sales

def get_collection_stats(slug):
    url = f"https://api.opensea.io/api/v2/collections/{slug}/stats"
    try:
        r    = requests.get(url, headers=OPENSEA_HEADERS, timeout=10)
        data = r.json().get("total", {})
        return data.get("floor_price"), data.get("volume")
    except Exception as e:
        print(f"[OpenSea] Stats Fehler: {e}")
        return None, None


# ================================================================
# PUNKTESYSTEM
# ================================================================

def update_points(db, slug, buyer, points):
    today = str(datetime.date.today())
    hist  = db["collections"][slug]["history"]
    if today not in hist: hist[today] = {}
    if buyer not in hist[today]: hist[today][buyer] = {"points": 0}
    hist[today][buyer]["points"] += points

def get_top_buyers(db, slug, days=1, top_n=5):
    today    = datetime.date.today()
    combined = {}
    for i in range(days):
        d_str = str(today - datetime.timedelta(days=i))
        for buyer, stats in db["collections"][slug]["history"].get(d_str, {}).items():
            combined[buyer] = combined.get(buyer, 0) + stats["points"]
    return sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_n]


# ================================================================
# FEATURE 1: SALES + SWEEP + WHALE
# ================================================================

def process_sales(col, db):
    slug     = col["slug"]
    col_data = db["collections"][slug]
    sales    = get_recent_sales(slug)
    if not sales: return

    new_sales = []
    for sale in sales:
        if sale["tx"] == col_data["last_tx"]: break
        new_sales.append(sale)
    if not new_sales: return

    tx_count  = {}
    for s in new_sales:
        tx_count[s["tx"]] = tx_count.get(s["tx"], 0) + 1
    sweep_txs     = {tx for tx, cnt in tx_count.items() if cnt >= col["sweep_threshold"]}
    posted_sweeps = set()
    all_by_tx     = {}
    for s in sales:
        all_by_tx.setdefault(s["tx"], []).append(s)

    for sale in new_sales:
        tx       = sale["tx"]
        buyer    = sale["buyer"]
        price    = sale["priceEth"]
        token_id = sale["tokenId"]
        url      = sale["url"] or f"https://opensea.io/assets/ethereum/{col['contract']}/{token_id}"
        pts      = 5 if price >= col["whale_threshold"] else 1
        short    = f"{buyer[:6]}...{buyer[-4:]}"

        if tx in sweep_txs and tx not in posted_sweeps:
            group     = all_by_tx.get(tx, [])
            total_eth = sum(s["priceEth"] for s in group)
            count     = len(group)
            post_everywhere(col,
                f"🧹 SWEEP! {col['name']}\n{short} swept {count} NFTs for {total_eth:.3f} ETH!\nhttps://etherscan.io/tx/{tx}\n#{col['name']} #NFT #Sweep",
                {"title": f"🧹 SWEEP — {count} NFTs!", "color": 0xFF6600,
                 "fields": [{"name":"Buyer","value":f"`{short}`","inline":True},{"name":"Count","value":str(count),"inline":True},{"name":"Total","value":f"{total_eth:.3f} ETH","inline":True},{"name":"Tx","value":f"[Etherscan](https://etherscan.io/tx/{tx})","inline":False}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )
            posted_sweeps.add(tx)

        elif tx not in sweep_txs and price >= col["whale_threshold"]:
            is_whale = price >= col["whale_threshold"]
            header   = "🐋 WHALE SALE!" if is_whale else "🎉 New Sale!"
            post_everywhere(col,
                f"{header} {col['name']} #{token_id}\nBuyer: {short}\nPrice: {price:.4f} ETH\n{url}\n#{col['name']} #NFT",
                {"title": f"{header} #{token_id}", "color": 0xFF8800 if is_whale else 0x00CC66,
                 "fields": [{"name":"Buyer","value":f"`{short}`","inline":True},{"name":"Price","value":f"{price:.4f} ETH","inline":True},{"name":"Points","value":f"+{pts}","inline":True},{"name":"Link","value":f"[OpenSea]({url})","inline":False}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )

        if buyer.lower() in [w.lower() for w in db.get("whale_wallets", [])]:
            post_everywhere(col,
                f"🐳 KNOWN WHALE! {col['name']}\n{short} bought #{token_id} for {price:.4f} ETH\n#{col['name']} #WhaleAlert",
                {"title": "🐳 Known Whale!", "color": 0x0099FF,
                 "fields": [{"name":"Wallet","value":f"`{buyer}`","inline":False},{"name":"Token","value":f"#{token_id}","inline":True},{"name":"Price","value":f"{price:.4f} ETH","inline":True}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )

        col_data["total_volume"] = col_data.get("total_volume", 0.0) + price
        update_points(db, slug, buyer, pts)
        col_data["last_tx"] = tx

    print(f"[{col['name']}] {len(new_sales)} new sales.")


# ================================================================
# FEATURE 2: FLOOR ALERTS
# ================================================================

def check_floor_price(col, db):
    slug     = col["slug"]
    col_data = db["collections"][slug]
    floor, volume = get_collection_stats(slug)
    if floor is None: return
    if volume: col_data["total_volume"] = float(volume)

    last_floor = col_data.get("last_floor")
    if last_floor and last_floor > 0:
        change_pct = ((floor - last_floor) / last_floor) * 100
        if abs(change_pct) >= col.get("floor_alert_pct", 5):
            direction = "📈" if change_pct > 0 else "📉"
            word      = "UP" if change_pct > 0 else "DOWN"
            post_everywhere(col,
                f"{direction} FLOOR {word} {abs(change_pct):.1f}%! {col['name']}\nNew: {floor:.4f} ETH | Old: {last_floor:.4f} ETH\n#{col['name']} #FloorAlert",
                {"title": f"{direction} Floor {word} {abs(change_pct):.1f}%!", "color": 0x00FF00 if change_pct > 0 else 0xFF0000,
                 "fields": [{"name":"New Floor","value":f"{floor:.4f} ETH","inline":True},{"name":"Old Floor","value":f"{last_floor:.4f} ETH","inline":True},{"name":"Change","value":f"{change_pct:+.1f}%","inline":True}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )
    col_data["last_floor"] = floor


# ================================================================
# FEATURE 3: VOLUME MILESTONES
# ================================================================

def check_volume_milestones(col, db):
    slug     = col["slug"]
    col_data = db["collections"][slug]
    volume   = col_data.get("total_volume", 0.0)
    hits     = col_data.get("volume_milestones_hit", [])
    for milestone in sorted(col.get("volume_milestones", [])):
        if volume >= milestone and milestone not in hits:
            post_everywhere(col,
                f"🎉 MILESTONE! {col['name']}\n{milestone} ETH Volume reached! 🚀\nCurrent: {volume:.2f} ETH\n#{col['name']} #NFT",
                {"title": f"🎉 {milestone} ETH Milestone!", "color": 0xFFD700,
                 "fields": [{"name":"Milestone","value":f"{milestone} ETH","inline":True},{"name":"Volume","value":f"{volume:.2f} ETH","inline":True}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )
            hits.append(milestone)
    col_data["volume_milestones_hit"] = hits


# ================================================================
# FEATURE 4: RANKINGS
# ================================================================

def post_rankings(col, db, now):
    slug   = col["slug"]
    emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    top_daily = get_top_buyers(db, slug, days=1)
    if top_daily:
        lines = "\n".join(f"{emojis[i]} {b[:6]}...{b[-4:]} — {p} pts" for i, (b, p) in enumerate(top_daily))
        post_everywhere(col,
            f"🏆 DAILY {col['name'].upper()} RANKING 🏆\n\n{lines}\n\n#{col['name']} #NFT",
            {"title": f"🏆 Daily Ranking — {col['name']}", "color": 0x5865F2,
             "fields": [{"name": f"{emojis[i]} #{i+1}", "value": f"`{b[:6]}...{b[-4:]}` — **{p} pts**", "inline": False} for i, (b, p) in enumerate(top_daily)],
             "timestamp": datetime.datetime.utcnow().isoformat()}
        )

    if now.weekday() == 6:
        top_weekly = get_top_buyers(db, slug, days=7)
        if top_weekly:
            lines = "\n".join(f"{emojis[i]} {b[:6]}...{b[-4:]} — {p} pts" for i, (b, p) in enumerate(top_weekly))
            post_everywhere(col,
                f"🏆 WEEKLY {col['name'].upper()} RANKING 🏆\n\n{lines}\n\n#{col['name']} #NFT",
                {"title": f"🏆 Weekly Ranking — {col['name']}", "color": 0xFFD700,
                 "fields": [{"name": f"{emojis[i]} #{i+1}", "value": f"`{b[:6]}...{b[-4:]}` — **{p} pts**", "inline": False} for i, (b, p) in enumerate(top_weekly)],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )
            winner, pts = top_weekly[0]
            post_everywhere(col,
                f"⭐ COLLECTOR OF THE WEEK — {col['name']}\n🏆 {winner[:6]}...{winner[-4:]}\nPoints: {pts}\n#{col['name']} #CollectorOfTheWeek",
                {"title": "⭐ Collector of the Week!", "color": 0xFFD700,
                 "fields": [{"name":"Wallet","value":f"`{winner}`","inline":False},{"name":"Points","value":str(pts),"inline":True}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )


# ================================================================
# MAIN
# ================================================================

def main():
    print("=" * 50)
    print("  NFT Pro Bot — OpenSea API v2")
    print(f"  Collections: {[c['name'] for c in COLLECTIONS]}")
    print("=" * 50)

    init_db()
    last_rank_date = None
    floor_counter  = 0

    while True:
        now = datetime.datetime.utcnow()
        db  = load_db()

        for col in COLLECTIONS:
            try: process_sales(col, db)
            except Exception as e: print(f"[{col['name']}] Sales error: {e}")

            try: check_volume_milestones(col, db)
            except Exception as e: print(f"[{col['name']}] Milestone error: {e}")

        floor_counter += 1
        if floor_counter >= FLOOR_CHECK_EVERY:
            for col in COLLECTIONS:
                try: check_floor_price(col, db)
                except Exception as e: print(f"[{col['name']}] Floor error: {e}")
            floor_counter = 0

        save_db(db)

        if now.hour == DAILY_HOUR and now.minute == 0 and last_rank_date != now.date():
            db = load_db()
            for col in COLLECTIONS:
                try: post_rankings(col, db, now)
                except Exception as e: print(f"[{col['name']}] Ranking error: {e}")
            last_rank_date = now.date()

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
