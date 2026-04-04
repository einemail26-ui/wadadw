import tweepy
import requests
import time
import json
import os
import datetime

# ================================================================
# KONFIGURATION
# ================================================================

# --- Twitter Accounts ---
TWITTER_CONFIGS = {
    "normiesART": {
        "api_key":             "vU9YN0PH66FlZ7KaH67HRVJ8g",
        "api_secret":          "XmZk8Kt0KccumPfSzfOJKWU1ht2VvHdc3CHZFmVVq4WIfV2M7e",
        "access_token":        "1758736566879424512-evPFfcvVmfUwQ9rXrxBsI6SrU6CZxJ",
        "access_token_secret": "oTSveTMCvcXi3URDttHuGDKJkrsSxP5JuI9a5rQtxyFcF",
        "bearer_token":        "AAAAAAAAAAAAAAAAAAAAAHPE8gEAAAAAoe16YJk%2FOlLYaWjuFGGsEoDVKzc%3DUsMN7pXsSPneUOx8HI9t6dFsIZupoxMHjhpKb0gGj2Q0VebE7k",
    },
}

# --- Collections ---
COLLECTIONS = [
    {
        "name":              "normiesART",
        "contract":          "0x9eb642398402130310214a1240214c3300000000",
        "twitter_account":   "normiesART",
        "discord_webhook":   "https://discord.com/api/webhooks/DEIN_WEBHOOK",
        "whale_threshold":   0.5,
        "sweep_threshold":   3,
        "floor_alert_pct":   5,
        "volume_milestones": [10, 25, 50, 100, 250, 500],
    },
]

# --- Whale Wallets ---
WHALE_WALLETS = [
    # "0xABC...",
]

# --- Alchemy ---
ALCHEMY_API_KEY = "Aa2hs4IatofJbeB0Nijcw"
ALCHEMY_BASE    = f"https://eth-mainnet.g.alchemy.com/nft/v3/{ALCHEMY_API_KEY}"

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
            cid = col["contract"].lower()
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
            cfg["bearer_token"],
            cfg["api_key"],
            cfg["api_secret"],
            cfg["access_token"],
            cfg["access_token_secret"]
        )
        _twitter_clients[account_name] = client
    return _twitter_clients[account_name]


# ================================================================
# POSTING: TWITTER + DISCORD
# ================================================================

def post_to_twitter(account_name, text):
    try:
        client = get_twitter_client(account_name)
        client.create_tweet(text=text)
        print(f"[Twitter] ✓ {text[:60]}...")
    except Exception as e:
        print(f"[Twitter] ✗ {e}")


def post_to_discord(webhook_url, embed):
    if not webhook_url or "DEIN_WEBHOOK" in webhook_url:
        return
    try:
        r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 204):
            print(f"[Discord] ✓ {embed.get('title', '')}")
        else:
            print(f"[Discord] ✗ HTTP {r.status_code}")
    except Exception as e:
        print(f"[Discord] ✗ {e}")


def post_everywhere(col, twitter_text, discord_embed):
    post_to_twitter(col["twitter_account"], twitter_text)
    post_to_discord(col["discord_webhook"], discord_embed)


# ================================================================
# ALCHEMY API
# ================================================================

def _alchemy_get(endpoint, params=None):
    try:
        url = f"{ALCHEMY_BASE}/{endpoint}"
        r = requests.get(url, params=params, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[Alchemy] Fehler: {e}")
        return {}


def get_recent_sales(contract, limit=20):
    """
    Holt die letzten NFT Sales via Alchemy getNFTSales.
    Gibt eine normalisierte Liste zurück:
    [{ txHash, from, tokenId, priceEth }, ...]
    """
    data = _alchemy_get("getNFTSales", {
        "contractAddress": contract,
        "order":           "desc",
        "limit":           limit,
    })

    sales = []
    for s in data.get("nftSales", []):
        try:
            # Preis aus sellerFee + royaltyFee + protocolFee (wei → ETH)
            price_wei = int(s.get("sellerFee", {}).get("amount", "0"))
            price_eth = price_wei / 1e18

            sales.append({
                "txHash":   s.get("transactionHash", ""),
                "from":     s.get("buyerAddress", ""),
                "tokenId":  s.get("tokenId", "?"),
                "priceEth": price_eth,
            })
        except Exception:
            continue

    return sales


def get_floor_price(contract):
    """Holt Floor Price via Alchemy getFloorPrice."""
    data = _alchemy_get("getFloorPrice", {"contractAddress": contract})
    # Alchemy gibt OpenSea + LooksRare zurück — wir nehmen OpenSea
    try:
        return data["openSea"]["floorPrice"]
    except Exception:
        return None


def get_token_rarity(contract, token_id):
    """Holt Rarity Rank via Alchemy getNFTMetadata."""
    data = _alchemy_get("getNFTMetadata", {
        "contractAddress": contract,
        "tokenId":         token_id,
    })
    try:
        rank = data["raw"]["metadata"].get("rarityRank") or data.get("rarityRank")
        return rank
    except Exception:
        return None


# ================================================================
# PUNKTESYSTEM
# ================================================================

def update_points(db, contract, buyer, points):
    cid   = contract.lower()
    today = str(datetime.date.today())
    hist  = db["collections"][cid]["history"]

    if today not in hist:
        hist[today] = {}
    if buyer not in hist[today]:
        hist[today][buyer] = {"points": 0}
    hist[today][buyer]["points"] += points


def get_top_buyers(db, contract, days=1, top_n=5):
    cid      = contract.lower()
    today    = datetime.date.today()
    combined = {}

    for i in range(days):
        d_str    = str(today - datetime.timedelta(days=i))
        day_data = db["collections"][cid]["history"].get(d_str, {})
        for buyer, stats in day_data.items():
            combined[buyer] = combined.get(buyer, 0) + stats["points"]

    return sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_n]


# ================================================================
# FEATURE 1: SALES + RARITY + SWEEP + WHALE WALLET
# ================================================================

def process_sales(col, db):
    contract = col["contract"]
    cid      = contract.lower()
    col_data = db["collections"][cid]

    sales = get_recent_sales(contract, limit=20)
    if not sales:
        return

    # Neue Sales bis zum letzten bekannten Tx
    new_sales = []
    for sale in sales:
        if sale["txHash"] == col_data["last_tx"]:
            break
        new_sales.append(sale)

    if not new_sales:
        return

    # Sweep Detection: Tx mit >= sweep_threshold Sales
    tx_count = {}
    for s in new_sales:
        tx_count[s["txHash"]] = tx_count.get(s["txHash"], 0) + 1

    sweep_txs     = {tx for tx, cnt in tx_count.items() if cnt >= col["sweep_threshold"]}
    posted_sweeps = set()

    # Alle Sales nach TxHash gruppieren (für Sweep-Zusammenfassung)
    all_by_tx = {}
    for s in sales:
        tx = s["txHash"]
        if tx not in all_by_tx:
            all_by_tx[tx] = []
        all_by_tx[tx].append(s)

    for sale in new_sales:
        tx       = sale["txHash"]
        buyer    = sale["from"]
        price    = sale["priceEth"]
        token_id = sale["tokenId"]
        pts      = 5 if price >= col["whale_threshold"] else 1

        # --- Sweep ---
        if tx in sweep_txs and tx not in posted_sweeps:
            group     = all_by_tx.get(tx, [])
            total_eth = sum(s["priceEth"] for s in group)
            count     = len(group)
            short     = f"{buyer[:6]}...{buyer[-4:]}"

            tw = (
                f"🧹 SWEEP! {col['name']}\n\n"
                f"{short} kaufte {count} NFTs für {total_eth:.3f} ETH!\n"
                f"https://etherscan.io/tx/{tx}\n\n"
                f"#{col['name']} #NFT #Sweep"
            )
            disc = {
                "title":  f"🧹 SWEEP — {count} NFTs!",
                "color":  0xFF6600,
                "fields": [
                    {"name": "Buyer",  "value": f"`{short}`",           "inline": True},
                    {"name": "Count",  "value": str(count),              "inline": True},
                    {"name": "Total",  "value": f"{total_eth:.3f} ETH", "inline": True},
                    {"name": "Tx",     "value": f"[Etherscan](https://etherscan.io/tx/{tx})", "inline": False},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw, disc)
            posted_sweeps.add(tx)

        elif tx not in sweep_txs:
            # --- Normaler Sale + Rarity ---
            rarity_rank = get_token_rarity(contract, token_id)
            is_whale    = price >= col["whale_threshold"]
            header      = "🐋 WHALE SALE!" if is_whale else "🎉 New Sale!"
            short       = f"{buyer[:6]}...{buyer[-4:]}"
            rarity_line = f"Rarity Rank: #{rarity_rank}\n" if rarity_rank else ""

            tw = (
                f"{header} {col['name']} #{token_id}\n\n"
                f"Buyer: {short}\n"
                f"Price: {price:.4f} ETH\n"
                f"{rarity_line}"
                f"\nhttps://etherscan.io/tx/{tx}\n\n"
                f"#{col['name']} #NFT"
            )
            disc_fields = [
                {"name": "Buyer",  "value": f"`{short}`",      "inline": True},
                {"name": "Price",  "value": f"{price:.4f} ETH","inline": True},
                {"name": "Points", "value": f"+{pts}",          "inline": True},
            ]
            if rarity_rank:
                disc_fields.append({"name": "Rarity Rank", "value": f"#{rarity_rank}", "inline": True})
            disc_fields.append({"name": "Tx", "value": f"[Etherscan](https://etherscan.io/tx/{tx})", "inline": False})

            disc = {
                "title":     f"{header} #{token_id}",
                "color":     0xFF8800 if is_whale else 0x00CC66,
                "fields":    disc_fields,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw, disc)

        # --- Whale Wallet Alert ---
        if buyer.lower() in [w.lower() for w in db.get("whale_wallets", [])]:
            short = f"{buyer[:6]}...{buyer[-4:]}"
            tw_w  = (
                f"🐳 KNOWN WHALE! {col['name']}\n\n"
                f"{short} kaufte #{token_id}\n"
                f"Price: {price:.4f} ETH\n\n"
                f"#{col['name']} #WhaleAlert #NFT"
            )
            disc_w = {
                "title":  "🐳 Known Whale Spotted!",
                "color":  0x0099FF,
                "fields": [
                    {"name": "Wallet", "value": f"`{buyer}`",      "inline": False},
                    {"name": "Token",  "value": f"#{token_id}",    "inline": True},
                    {"name": "Price",  "value": f"{price:.4f} ETH","inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw_w, disc_w)

        # Volume tracken
        col_data["total_volume"] = col_data.get("total_volume", 0) + price
        update_points(db, contract, buyer, pts)
        col_data["last_tx"] = tx

    print(f"[{col['name']}] {len(new_sales)} neue Sales verarbeitet.")


# ================================================================
# FEATURE 2: FLOOR PRICE ALERTS
# ================================================================

def check_floor_price(col, db):
    contract      = col["contract"]
    cid           = contract.lower()
    col_data      = db["collections"][cid]
    threshold_pct = col.get("floor_alert_pct", 5)

    floor = get_floor_price(contract)
    if floor is None:
        return

    last_floor = col_data.get("last_floor")

    if last_floor and last_floor > 0:
        change_pct = ((floor - last_floor) / last_floor) * 100

        if abs(change_pct) >= threshold_pct:
            direction = "📈" if change_pct > 0 else "📉"
            word      = "UP" if change_pct > 0 else "DOWN"

            tw = (
                f"{direction} FLOOR {word} {abs(change_pct):.1f}%! {col['name']}\n\n"
                f"Neu:  {floor:.4f} ETH\n"
                f"Alt:  {last_floor:.4f} ETH\n\n"
                f"#{col['name']} #NFT #FloorAlert"
            )
            disc = {
                "title":  f"{direction} Floor {word} {abs(change_pct):.1f}%!",
                "color":  0x00FF00 if change_pct > 0 else 0xFF0000,
                "fields": [
                    {"name": "Neuer Floor", "value": f"{floor:.4f} ETH",      "inline": True},
                    {"name": "Alter Floor", "value": f"{last_floor:.4f} ETH", "inline": True},
                    {"name": "Änderung",    "value": f"{change_pct:+.1f}%",   "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw, disc)
            print(f"[{col['name']}] Floor Alert: {change_pct:+.1f}%")

    col_data["last_floor"] = floor


# ================================================================
# FEATURE 3: VOLUME MILESTONES
# ================================================================

def check_volume_milestones(col, db):
    contract   = col["contract"]
    cid        = contract.lower()
    col_data   = db["collections"][cid]
    milestones = col.get("volume_milestones", [])
    volume     = col_data.get("total_volume", 0)
    hits       = col_data.get("volume_milestones_hit", [])

    for milestone in sorted(milestones):
        if volume >= milestone and milestone not in hits:
            tw = (
                f"🎉 MILESTONE! {col['name']}\n\n"
                f"Total Volume hat {milestone} ETH überschritten! 🚀\n"
                f"Aktuell: {volume:.2f} ETH\n\n"
                f"#{col['name']} #NFT #Milestone"
            )
            disc = {
                "title":       f"🎉 {milestone} ETH Milestone!",
                "description": f"**{col['name']}** hat {milestone} ETH Gesamtvolumen erreicht!",
                "color":       0xFFD700,
                "fields": [
                    {"name": "Milestone", "value": f"{milestone} ETH",  "inline": True},
                    {"name": "Volume",    "value": f"{volume:.2f} ETH", "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw, disc)
            hits.append(milestone)
            print(f"[{col['name']}] Milestone: {milestone} ETH")

    col_data["volume_milestones_hit"] = hits


# ================================================================
# FEATURE 4: RANKINGS + COLLECTOR OF THE WEEK
# ================================================================

def post_rankings(col, db, now):
    contract = col["contract"]
    emojis   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    # Daily
    top_daily = get_top_buyers(db, contract, days=1)
    if top_daily:
        lines    = "\n".join(f"{emojis[i]} {b[:6]}...{b[-4:]} — {p} Pkt" for i, (b, p) in enumerate(top_daily))
        tw_daily = f"🏆 DAILY {col['name'].upper()} RANKING 🏆\n\n{lines}\n\n#{col['name']} #NFT"
        disc_daily = {
            "title":  f"🏆 Daily Ranking — {col['name']}",
            "color":  0x5865F2,
            "fields": [
                {"name": f"{emojis[i]} Platz {i+1}", "value": f"`{b[:6]}...{b[-4:]}` — **{p} Pkt**", "inline": False}
                for i, (b, p) in enumerate(top_daily)
            ],
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        post_everywhere(col, tw_daily, disc_daily)
        print(f"[{col['name']}] Daily Ranking gepostet.")

    # Weekly + Collector of the Week (Sonntags)
    if now.weekday() == 6:
        top_weekly = get_top_buyers(db, contract, days=7)
        if top_weekly:
            lines      = "\n".join(f"{emojis[i]} {b[:6]}...{b[-4:]} — {p} Pkt" for i, (b, p) in enumerate(top_weekly))
            tw_weekly  = f"🏆 WEEKLY {col['name'].upper()} RANKING 🏆\n\n{lines}\n\n#{col['name']} #NFT"
            disc_weekly = {
                "title":  f"🏆 Weekly Ranking — {col['name']}",
                "color":  0xFFD700,
                "fields": [
                    {"name": f"{emojis[i]} Platz {i+1}", "value": f"`{b[:6]}...{b[-4:]}` — **{p} Pkt**", "inline": False}
                    for i, (b, p) in enumerate(top_weekly)
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw_weekly, disc_weekly)

            # Collector of the Week
            winner, pts = top_weekly[0]
            tw_cotw = (
                f"⭐ COLLECTOR OF THE WEEK — {col['name']}\n\n"
                f"🏆 {winner[:6]}...{winner[-4:]}\n"
                f"Punkte diese Woche: {pts}\n\n"
                f"Herzlichen Glückwunsch! 🎉\n\n"
                f"#{col['name']} #CollectorOfTheWeek #NFT"
            )
            disc_cotw = {
                "title":       "⭐ Collector of the Week!",
                "description": f"Bester Collector für **{col['name']}** diese Woche",
                "color":       0xFFD700,
                "fields": [
                    {"name": "Wallet", "value": f"`{winner}`", "inline": False},
                    {"name": "Punkte", "value": str(pts),       "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_everywhere(col, tw_cotw, disc_cotw)
            print(f"[{col['name']}] Weekly + Collector of the Week gepostet.")


# ================================================================
# MAIN LOOP
# ================================================================

def main():
    print("=" * 50)
    print("  NFT Pro Bot — Alchemy Edition")
    print(f"  Collections: {[c['name'] for c in COLLECTIONS]}")
    print("=" * 50)

    init_db()

    last_rank_date = None
    floor_counter  = 0

    while True:
        now = datetime.datetime.utcnow()
        db  = load_db()

        for col in COLLECTIONS:
            try:
                process_sales(col, db)
            except Exception as e:
                print(f"[{col['name']}] Sales-Fehler: {e}")

            try:
                check_volume_milestones(col, db)
            except Exception as e:
                print(f"[{col['name']}] Milestone-Fehler: {e}")

        # Floor alle FLOOR_CHECK_EVERY Minuten
        floor_counter += 1
        if floor_counter >= FLOOR_CHECK_EVERY:
            for col in COLLECTIONS:
                try:
                    check_floor_price(col, db)
                except Exception as e:
                    print(f"[{col['name']}] Floor-Fehler: {e}")
            floor_counter = 0

        save_db(db)

        # Tägliches Ranking
        if now.hour == DAILY_HOUR and now.minute == 0 and last_rank_date != now.date():
            db = load_db()
            for col in COLLECTIONS:
                try:
                    post_rankings(col, db, now)
                except Exception as e:
                    print(f"[{col['name']}] Ranking-Fehler: {e}")
            last_rank_date = now.date()

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
