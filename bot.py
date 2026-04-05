import tweepy, requests, time, json, os, datetime

# ================================================================
# CONFIG
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
        "name":            "normiesART",
        "slug":            "normies",
        "contract":        "0x9435208ca4a8dfba4bbffc52bd4d65fac3a87fd4",
        "twitter_account": "normiesART",
        "discord_webhook": "YOUR_DISCORD_WEBHOOK",
        "sale_min_eth":    0.5,
        "sweep_min_eth":   1.0,
        "sweep_count":     5,
        "sweep_window":    600,
        "burn_ap_min":     50,
        "canvas_ap_min":   100,
        "floor_alert_pct": 5,
        "volume_milestones": [10, 25, 50, 100, 250, 500],
    },
]

GRAIL_TYPES     = ["Cat", "Alien", "Agent"]
OPENSEA_HEADERS = {"accept": "application/json", "x-api-key": "0439IVxW5fla2biNld4HmYggLYuhKqM8dVcewCw1xGWmNZQY"}
NORMIES_API     = "https://api.normies.art"
DB_FILE         = "nft_bot_pro.json"
CHECK_INTERVAL  = 60
FLOOR_CHECK_EVERY = 5
DAILY_HOUR      = 20

# ================================================================
# TWEET TEMPLATES
# ================================================================

def tweet_single_sale(token_id, price_eth, price_usd):
    return (
        f"Oh look! 😯\n\n"
        f"Another big @normiesART single sale!\n\n"
        f"Normie #{token_id} — {price_eth:.4f} ETH ({price_usd})\n"
        f"{opensea_link(token_id)}\n\n"
        f"Normies."
    )

def tweet_sweep(count, total_eth, total_usd, buyer):
    ens = get_ens_name(buyer)
    return (
        f"Woah, watch out! 👀\n\n"
        f"Another big @normiesART sweep!\n\n"
        f"{ens} swept {count} Normies for {total_eth:.3f} ETH ({total_usd})\n\n"
        f"Normies."
    )

def tweet_grail(token_id, ntype, price_eth, price_usd):
    article = "an" if ntype in ["Alien", "Agent"] else "a"
    return (
        f"Can't believe it, that's {article} {ntype}! 🥶\n\n"
        f"That's an @normiesART grail for sure!\n\n"
        f"Normie #{token_id} — {price_eth:.4f} ETH ({price_usd})\n"
        f"{opensea_link(token_id)}\n\n"
        f"Normies."
    )

def tweet_burn(receiver_id, count, owner):
    ens    = get_ens_name(owner) if owner else "unknown"
    plural = "Normies" if count != 1 else "Normie"
    return (
        f"🔥 Another big burn!\n\n"
        f"{ens} burned {count} {plural} to edit Normie #{receiver_id}\n"
        f"{opensea_link(receiver_id)}\n\n"
        f"@normiesART #normies #NormiesCanvas"
    )

def tweet_canvas(token_id, ap, changes):
    return (
        f"🎨 Canvas change!\n\n"
        f"Normie #{token_id} was just edited — {changes} pixels changed.\n"
        f"Action Points: {ap}\n"
        f"{opensea_link(token_id)}\n\n"
        f"@normiesART #normies #NormiesCanvas"
    )

def tweet_floor(direction, pct, new_floor, new_usd):
    word = "up" if direction == "up" else "down"
    emoji = "📈" if direction == "up" else "📉"
    return (
        f"{emoji} Floor {word} {pct:.1f}%!\n\n"
        f"New floor: {new_floor:.4f} ETH ({new_usd})\n\n"
        f"@normiesART #normies #NFT"
    )

def tweet_milestone(milestone, volume):
    return (
        f"🎉 Milestone reached!\n\n"
        f"@normiesART just hit {milestone} ETH in total volume!\n"
        f"Current: {volume:.2f} ETH\n\n"
        f"#normies #NFT"
    )

def tweet_daily_ranking(lines):
    return f"🏆 Daily @normiesART Ranking\n\n{lines}\n\n#normies #NFT"

def tweet_weekly_ranking(lines):
    return f"🏆 Weekly @normiesART Ranking\n\n{lines}\n\n#normies #NFT"

def tweet_cotw(wallet, eth_spent):
    return (
        f"⭐ Collector of the Week!\n\n"
        f"🏆 {wallet}\n"
        f"Spent: {eth_spent:.3f} ETH this week\n\n"
        f"@normiesART #normies #CollectorOfTheWeek"
    )

# ================================================================
# ETH/USD PRICE
# ================================================================

_eth_cache = {"price": 0, "ts": 0}

def get_eth_usd():
    now = time.time()
    if now - _eth_cache["ts"] < 300:
        return _eth_cache["price"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
            timeout=8
        )
        p = r.json()["ethereum"]["usd"]
        _eth_cache.update({"price": p, "ts": now})
        return p
    except:
        return _eth_cache["price"] or 2000

def fmt_usd(eth):
    usd = eth * get_eth_usd()
    return f"${usd:,.0f}" if usd >= 1000 else f"${usd:.2f}"


# ================================================================
# ENS LOOKUP
# ================================================================

_ens_cache = {}

def get_ens_name(address):
    if address in _ens_cache:
        return _ens_cache[address]
    try:
        r = requests.get(f"https://api.ensideas.com/ens/resolve/{address}", timeout=5)
        name = r.json().get("name")
        result = name if name else address
        _ens_cache[address] = result
        return result
    except:
        _ens_cache[address] = address
        return address

def opensea_link(token_id):
    return f"https://opensea.io/item/ethereum/0x9435208ca4a8dfba4bbffc52bd4d65fac3a87fd4/{token_id}"

# ================================================================
# DATABASE
# ================================================================

def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({"collections": {}}, f)
    with open(DB_FILE, "r+") as f:
        db = json.load(f)
        for col in COLLECTIONS:
            s = col["slug"]
            if s not in db["collections"]:
                db["collections"][s] = {
                    "last_sale_tx":   None,
                    "last_burn_id":   None,
                    "last_canvas_tx": {},
                    "last_floor":     None,
                    "total_volume":   0.0,
                    "milestones_hit": [],
                    "history":        {},
                    "buyer_window":   {},
                }
        f.seek(0); json.dump(db, f, indent=2); f.truncate()

def load_db():
    with open(DB_FILE, "r") as f: return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=2)

# ================================================================
# POSTING
# ================================================================

_tc = {}
def get_tw(name):
    if name not in _tc:
        c = TWITTER_CONFIGS[name]
        _tc[name] = tweepy.Client(c["bearer_token"], c["api_key"], c["api_secret"], c["access_token"], c["access_token_secret"])
    return _tc[name]

def post_tw(acc, text):
    try:
        get_tw(acc).create_tweet(text=text)
        print(f"[TW] {text[:70]}...")
    except Exception as e:
        print(f"[TW ERR] {e}")

def post_dc(url, embed):
    if not url or "YOUR_DISCORD" in url: return
    try: requests.post(url, json={"embeds": [embed]}, timeout=10)
    except: pass

def post_all(col, tw, dc):
    post_tw(col["twitter_account"], tw)
    post_dc(col["discord_webhook"], dc)

# ================================================================
# NORMIES API
# ================================================================

def get_normie_type(token_id):
    try:
        r = requests.get(f"{NORMIES_API}/normie/{token_id}/traits", timeout=8)
        for a in r.json().get("attributes", []):
            if a.get("trait_type") == "Type":
                return a.get("value", "")
        return ""
    except:
        return ""

def get_normie_ap(token_id):
    try:
        r = requests.get(f"{NORMIES_API}/normie/{token_id}/canvas/info", timeout=8)
        return r.json().get("actionPoints", 0)
    except:
        return 0

def get_normie_img(token_id):
    return f"{NORMIES_API}/normie/{token_id}/image.png"

def get_burn_history(limit=10):
    try:
        r = requests.get(f"{NORMIES_API}/history/burns?limit={limit}", timeout=8)
        d = r.json()
        return d if isinstance(d, list) else []
    except:
        return []

def get_canvas_versions(token_id):
    try:
        r = requests.get(f"{NORMIES_API}/history/normie/{token_id}/versions", timeout=8)
        d = r.json()
        return d if isinstance(d, list) else []
    except:
        return []

# ================================================================
# OPENSEA
# ================================================================

def get_recent_sales(slug, limit=50):
    try:
        r = requests.get(
            f"https://api.opensea.io/api/v2/events/collection/{slug}",
            headers=OPENSEA_HEADERS,
            params={"event_type": "sale", "limit": limit},
            timeout=10
        )
        sales = []
        for e in r.json().get("asset_events", []):
            p   = e.get("payment", {})
            qty = int(p.get("quantity", "0"))
            dec = int(p.get("decimals", 18))
            nft = e.get("nft", {})
            sales.append({
                "tx":      e.get("transaction", ""),
                "buyer":   e.get("buyer", ""),
                "tokenId": nft.get("identifier", "?"),
                "price":   qty / (10 ** dec),
                "url":     nft.get("opensea_url", ""),
            })
        return sales
    except Exception as e:
        print(f"[OS ERR] {e}"); return []

def get_collection_stats(slug):
    try:
        r = requests.get(f"https://api.opensea.io/api/v2/collections/{slug}/stats", headers=OPENSEA_HEADERS, timeout=10)
        t = r.json().get("total", {})
        return t.get("floor_price"), t.get("volume")
    except:
        return None, None

# ================================================================
# SPENDING TRACKER (replaces points)
# ================================================================

def update_spending(db, slug, buyer, eth):
    today = str(datetime.date.today())
    h = db["collections"][slug]["history"]
    if today not in h: h[today] = {}
    if buyer not in h[today]: h[today][buyer] = {"eth": 0.0}
    h[today][buyer]["eth"] += eth

def top_spenders(db, slug, days=1, n=5):
    today = datetime.date.today(); c = {}
    for i in range(days):
        for b, s in db["collections"][slug]["history"].get(str(today - datetime.timedelta(days=i)), {}).items():
            c[b] = c.get(b, 0.0) + s.get("eth", 0.0)
    return sorted(c.items(), key=lambda x: x[1], reverse=True)[:n]

# ================================================================
# FEATURE 1: SALES
# ================================================================

def process_sales(col, db):
    slug = col["slug"]
    cd   = db["collections"][slug]
    sales = get_recent_sales(slug)
    if not sales: return

    new_sales = []
    for s in sales:
        if s["tx"] == cd["last_sale_tx"]: break
        new_sales.append(s)
    if not new_sales: return

    now_ts = time.time()
    window = col["sweep_window"]
    bw     = cd.get("buyer_window", {})
    bw     = {b: [t for t in times if now_ts - t < window] for b, times in bw.items()}

    sweep_posted = set()

    for s in reversed(new_sales):
        tx       = s["tx"]
        buyer    = s["buyer"]
        price    = s["price"]
        token_id = s["tokenId"]
        url      = s["url"] or f"https://opensea.io/assets/ethereum/{col['contract']}/{token_id}"
        short    = f"{buyer[:6]}...{buyer[-4:]}"
        usd      = fmt_usd(price)

        if buyer not in bw: bw[buyer] = []
        bw[buyer].append(now_ts)

        # buyer total ETH in window
        buyer_total_eth = sum(
            s2["price"] for s2 in new_sales if s2["buyer"] == buyer
        )

        # Sweep check
        if len(bw[buyer]) >= col["sweep_count"] and buyer_total_eth >= col["sweep_min_eth"] and buyer not in sweep_posted:
            cnt       = len(bw[buyer])
            total_usd = fmt_usd(buyer_total_eth)
            tw = tweet_sweep(cnt, buyer_total_eth, total_usd, buyer)
            dc = {
                "title":  f"👀 Sweep — {cnt} Normies!",
                "color":  0xFF6600,
                "fields": [
                    {"name": "Buyer",  "value": f"`{short}`",              "inline": True},
                    {"name": "Count",  "value": str(cnt),                   "inline": True},
                    {"name": "Total",  "value": f"{buyer_total_eth:.3f} ETH ({total_usd})", "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)
            sweep_posted.add(buyer)
            bw[buyer] = []

        elif buyer not in sweep_posted and price >= col["sale_min_eth"]:
            # Check grail type
            ntype = get_normie_type(token_id)
            is_grail = ntype in GRAIL_TYPES

            ens = get_ens_name(buyer)
            if is_grail:
                tw = tweet_grail(token_id, ntype, price, usd)
                color = 0x9B59B6
            else:
                tw = tweet_single_sale(token_id, price, usd)
                color = 0x00CC66

            dc = {
                "title":  f"{'🥶 GRAIL' if is_grail else '😯 Sale'} — #{token_id}",
                "color":  color,
                "fields": [
                    {"name": "Buyer",  "value": f"`{ens}`",       "inline": True},
                    {"name": "Price",  "value": f"{price:.4f} ETH ({usd})", "inline": True},
                    {"name": "Type",   "value": ntype or "Human","inline": True},
                    {"name": "Link",   "value": f"[OpenSea]({url})", "inline": False},
                ],
                "image":     {"url": get_normie_img(token_id)},
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)

        cd["total_volume"] = cd.get("total_volume", 0.0) + price
        update_spending(db, slug, buyer, price)
        cd["last_sale_tx"] = tx

    cd["buyer_window"] = bw
    print(f"[{col['name']}] {len(new_sales)} new sales.")

# ================================================================
# FEATURE 2: BURNS
# ================================================================

def check_burns(col, db):
    slug  = col["slug"]
    cd    = db["collections"][slug]
    burns = get_burn_history(10)
    if not burns: return

    last_id   = cd.get("last_burn_id")
    new_burns = []
    for b in burns:
        if str(b.get("commitId")) == str(last_id): break
        new_burns.append(b)

    for burn in reversed(new_burns):
        receiver  = burn.get("receiverTokenId", "?")
        count     = burn.get("tokenCount", 0)
        owner     = burn.get("owner", "")
        short     = f"{owner[:6]}...{owner[-4:]}"
        ap        = get_normie_ap(receiver)

        if ap >= col["burn_ap_min"]:
            tw = tweet_burn(receiver, count, owner)
            dc = {
                "title":  f"🔥 Burn — #{receiver} got {ap} AP!",
                "color":  0xFF3300,
                "fields": [
                    {"name": "Owner",   "value": f"`{short}`", "inline": True},
                    {"name": "Burned",  "value": str(count),   "inline": True},
                    {"name": "AP",      "value": str(ap),       "inline": True},
                ],
                "image":     {"url": get_normie_img(receiver)},
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)

        cd["last_burn_id"] = str(burn.get("commitId"))

# ================================================================
# FEATURE 3: CANVAS CHANGES
# ================================================================

def check_canvas_changes(col, db):
    slug     = col["slug"]
    cd       = db["collections"][slug]
    last_cvs = cd.get("last_canvas_tx", {})

    for token_id in list(last_cvs.keys()):
        versions = get_canvas_versions(token_id)
        if not versions: continue
        latest_tx = versions[-1].get("txHash", "")
        if latest_tx and latest_tx != last_cvs.get(str(token_id)):
            ap = get_normie_ap(token_id)
            if ap >= col["canvas_ap_min"]:
                changes = versions[-1].get("changeCount", 0)
                tw = tweet_canvas(token_id, ap, changes)
                dc = {
                    "title":  f"🎨 Canvas Change — #{token_id}",
                    "color":  0x0099FF,
                    "fields": [
                        {"name": "Pixels",  "value": str(changes), "inline": True},
                        {"name": "AP",      "value": str(ap),       "inline": True},
                    ],
                    "image":     {"url": get_normie_img(token_id)},
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                post_all(col, tw, dc)
            last_cvs[str(token_id)] = latest_tx

    cd["last_canvas_tx"] = last_cvs

# ================================================================
# FEATURE 4: FLOOR ALERTS
# ================================================================

def check_floor(col, db):
    slug = col["slug"]; cd = db["collections"][slug]
    floor, vol = get_collection_stats(slug)
    if floor is None: return
    if vol: cd["total_volume"] = float(vol)
    last = cd.get("last_floor")
    if last and last > 0:
        pct = ((floor - last) / last) * 100
        if abs(pct) >= col.get("floor_alert_pct", 5):
            direction = "up" if pct > 0 else "down"
            tw = tweet_floor(direction, abs(pct), floor, fmt_usd(floor))
            dc = {
                "title":  f"{'📈' if pct > 0 else '📉'} Floor {direction} {abs(pct):.1f}%!",
                "color":  0x00FF00 if pct > 0 else 0xFF0000,
                "fields": [
                    {"name": "New",    "value": f"{floor:.4f} ETH ({fmt_usd(floor)})", "inline": True},
                    {"name": "Old",    "value": f"{last:.4f} ETH",                    "inline": True},
                    {"name": "Change", "value": f"{pct:+.1f}%",                        "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)
    cd["last_floor"] = floor

# ================================================================
# FEATURE 5: VOLUME MILESTONES
# ================================================================

def check_milestones(col, db):
    slug = col["slug"]; cd = db["collections"][slug]
    vol  = cd.get("total_volume", 0.0)
    hits = cd.get("milestones_hit", [])
    for m in sorted(col.get("volume_milestones", [])):
        if vol >= m and m not in hits:
            tw = tweet_milestone(m, vol)
            dc = {
                "title":  f"🎉 {m} ETH Milestone!",
                "color":  0xFFD700,
                "fields": [{"name": "Volume", "value": f"{vol:.2f} ETH ({fmt_usd(vol)})", "inline": True}],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)
            hits.append(m)
    cd["milestones_hit"] = hits

# ================================================================
# FEATURE 6: RANKINGS
# ================================================================

def post_rankings(col, db, now):
    slug = col["slug"]
    em   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    td = top_spenders(db, slug, 1)
    if td:
        lines = "\n".join(f"{em[i]} {b[:6]}...{b[-4:]} — {e:.3f} ETH ({fmt_usd(e)})" for i, (b, e) in enumerate(td))
        post_all(col,
            tweet_daily_ranking(lines),
            {"title": "🏆 Daily Ranking", "color": 0x5865F2,
             "fields": [{"name": f"{em[i]} #{i+1}", "value": f"`{b[:6]}...{b[-4:]}` — **{e:.3f} ETH**", "inline": False} for i, (b, e) in enumerate(td)],
             "timestamp": datetime.datetime.utcnow().isoformat()}
        )

    if now.weekday() == 6:
        tw = top_spenders(db, slug, 7)
        if tw:
            lines = "\n".join(f"{em[i]} {b[:6]}...{b[-4:]} — {e:.3f} ETH ({fmt_usd(e)})" for i, (b, e) in enumerate(tw))
            post_all(col,
                tweet_weekly_ranking(lines),
                {"title": "🏆 Weekly Ranking", "color": 0xFFD700,
                 "fields": [{"name": f"{em[i]} #{i+1}", "value": f"`{b[:6]}...{b[-4:]}` — **{e:.3f} ETH**", "inline": False} for i, (b, e) in enumerate(tw)],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )
            winner, eth_spent = tw[0]
            post_all(col,
                tweet_cotw(f"{winner[:6]}...{winner[-4:]}", eth_spent),
                {"title": "⭐ Collector of the Week!", "color": 0xFFD700,
                 "fields": [{"name": "Wallet", "value": f"`{winner}`", "inline": False}, {"name": "Spent", "value": f"{eth_spent:.3f} ETH ({fmt_usd(eth_spent)})", "inline": True}],
                 "timestamp": datetime.datetime.utcnow().isoformat()}
            )

# ================================================================
# MAIN
# ================================================================

def main():
    print("="*50 + "\n  Normies Bot — Live\n" + "="*50)
    init_db()
    last_rank = None
    fc        = 0

    while True:
        now = datetime.datetime.utcnow()
        db  = load_db()

        for col in COLLECTIONS:
            try: process_sales(col, db)
            except Exception as e: print(f"[Sales ERR] {e}")

            try: check_burns(col, db)
            except Exception as e: print(f"[Burns ERR] {e}")

            try: check_canvas_changes(col, db)
            except Exception as e: print(f"[Canvas ERR] {e}")

            try: check_milestones(col, db)
            except Exception as e: print(f"[Milestone ERR] {e}")

        fc += 1
        if fc >= FLOOR_CHECK_EVERY:
            for col in COLLECTIONS:
                try: check_floor(col, db)
                except Exception as e: print(f"[Floor ERR] {e}")
            fc = 0

        save_db(db)

        if now.hour == DAILY_HOUR and now.minute == 0 and last_rank != now.date():
            db = load_db()
            for col in COLLECTIONS:
                try: post_rankings(col, db, now)
                except Exception as e: print(f"[Ranking ERR] {e}")
            last_rank = now.date()

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
