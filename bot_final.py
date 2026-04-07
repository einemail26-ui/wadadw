import tweepy, requests, time, json, os, datetime, random, logging

# ================================================================
# LOGGING
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot_audit.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("normiesbot")

# ================================================================
# CONFIG
# ================================================================

# Keys aus Umgebungsvariablen lesen (sicherer als hardcoded)
# Fallback auf hardcoded Werte falls keine Env-Vars gesetzt
def _env(key, fallback=""):
    return os.environ.get(key, fallback)

TWITTER_CONFIGS = {
    "normiesART": {
        "api_key":             _env("TW_API_KEY",             "6Bkw6xsEa2bKta1LVcpCmsMkt"),
        "api_secret":          _env("TW_API_SECRET",          "fCNacwYIAuQcEa7FXcgwH0Ik0IXkczHOMkArtDfHC9VCYULqnP"),
        "access_token":        _env("TW_ACCESS_TOKEN",        "1760084280066334720-c1U4NZFC2vZTJ32qaRIoawtnIVRKYy"),
        "access_token_secret": _env("TW_ACCESS_TOKEN_SECRET", "urzFNHZ32zyjcRiwZEEBlS0XgBfO2mZ9mN8VTB4Adtaxb"),
        "bearer_token":        _env("TW_BEARER_TOKEN",        "AAAAAAAAAAAAAAAAAAAAACdf8wEAAAAAb39Ul3%2B%2FPr9xJbs26d7%2F1qShU4w%3DSVeGIpdEgSPEryzmPXlJXrLH4Jfuf7A9ZGp1qbvVfp0dszc4WT"),
    },
}

COLLECTIONS = [
    {
        "name":            "normiesART",
        "slug":            "normies",
        "contract":        "0x9Eb6E2025B64f340691e424b7fe7022fFDE12438",
        "twitter_account": "normiesART",
        "discord_webhook": "YOUR_DISCORD_WEBHOOK",
        "sale_min_eth":    0.5,
        "sweep_min_eth":   1.0,
        "sweep_count":     5,
        "sweep_window":    600,
        "burn_ap_min":     50,
        "canvas_ap_min":   100,
        "floor_alert_pct": 5,
    },
]

GRAIL_TYPES       = ["Cat", "Alien", "Agent"]
OPENSEA_HEADERS   = {"accept": "application/json", "x-api-key": _env("OPENSEA_API_KEY", "0439IVxW5fla2biNld4HmYggLYuhKqM8dVcewCw1xGWmNZQY")}
NORMIES_API       = "https://api.normies.art"
DB_FILE           = "nft_bot_pro.json"
CHECK_INTERVAL    = 60
FLOOR_CHECK_EVERY = 5

# ================================================================
# SAFETY CONFIG
# ================================================================

# Killswitch: DM "STOP" from any of these accounts to pause the bot
KILLSWITCH_ACCOUNTS = [
    "xbtphil",
    "serc1n",
    "kali111supersta",
]

# Rate limiting: max tweets per window
MAX_TWEETS_PER_HOUR  = 8
MAX_TWEETS_PER_DAY   = 40
MIN_SECONDS_BETWEEN  = 90   # min pause between any two tweets

# Queue: max pending tweets
MAX_QUEUE_SIZE = 20

# Error backoff
MAX_CONSECUTIVE_ERRORS = 5

# ================================================================
# RATE LIMITER
# ================================================================

class RateLimiter:
    def __init__(self):
        self.hourly_times = []  # timestamps in last hour
        self.daily_times  = []  # timestamps in last 24h (separate — fixes bug)
        self.paused       = False
        self.pause_until  = 0
        self.consecutive_errors = 0

    def _clean(self):
        now = time.time()
        self.hourly_times = [t for t in self.hourly_times if now - t < 3600]
        self.daily_times  = [t for t in self.daily_times  if now - t < 86400]

    def can_tweet(self):
        if self.paused:
            if time.time() < self.pause_until:
                return False
            self.paused = False
            log.info("[RateLimit] Pause lifted, resuming.")

        self._clean()

        if len(self.hourly_times) >= MAX_TWEETS_PER_HOUR:
            log.warning(f"[RateLimit] Hourly limit ({MAX_TWEETS_PER_HOUR}/h). Queued.")
            return False

        if len(self.daily_times) >= MAX_TWEETS_PER_DAY:
            log.warning(f"[RateLimit] Daily limit ({MAX_TWEETS_PER_DAY}/day). Queued.")
            return False

        all_times = sorted(self.hourly_times + self.daily_times)
        if all_times and (time.time() - all_times[-1]) < MIN_SECONDS_BETWEEN:
            wait = MIN_SECONDS_BETWEEN - (time.time() - all_times[-1])
            log.info(f"[RateLimit] Min gap not reached, waiting {wait:.0f}s")
            return False

        return True

    def record_tweet(self):
        now = time.time()
        self.hourly_times.append(now)
        self.daily_times.append(now)
        self.consecutive_errors = 0
        log.info(f"[RateLimit] Tweeted. Hour: {len(self.hourly_times)}/{MAX_TWEETS_PER_HOUR}, Day: {len(self.daily_times)}/{MAX_TWEETS_PER_DAY}")

    def record_error(self):
        self.consecutive_errors += 1
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            self.paused = True
            self.pause_until = time.time() + 900
            log.error(f"[RateLimit] {MAX_CONSECUTIVE_ERRORS} consecutive errors. Pausing 15 min.")

    def emergency_pause(self, seconds=3600):
        self.paused = True
        self.pause_until = time.time() + seconds
        log.warning(f"[RateLimit] Emergency pause for {seconds}s.")

_rate_limiter = RateLimiter()

# ================================================================
# TWEET QUEUE
# ================================================================

_tweet_queue = []

def queue_tweet(acc, text, priority=False):
    """Add tweet to queue. High-priority (grail/sweep) goes to front."""
    if len(_tweet_queue) >= MAX_QUEUE_SIZE:
        log.warning("[Queue] Full, dropping oldest tweet.")
        _tweet_queue.pop(0)
    entry = {"acc": acc, "text": text, "ts": time.time()}
    if priority:
        _tweet_queue.insert(0, entry)
    else:
        _tweet_queue.append(entry)

_next_tweet_at = 0  # timestamp when next tweet is allowed

def flush_queue():
    """Send one tweet from queue if rate limiter allows and jitter delay has passed."""
    global _next_tweet_at
    if not _tweet_queue:
        return
    if not _rate_limiter.can_tweet():
        return
    if time.time() < _next_tweet_at:
        return  # jitter delay not yet passed — non-blocking
    entry = _tweet_queue.pop(0)
    # Set next allowed tweet time with human-like jitter
    _next_tweet_at = time.time() + random.randint(30, 45)
    _post_tw_now(entry["acc"], entry["text"])

# ================================================================
# TWEET TEMPLATES — randomized variants
# ================================================================

# ================================================================
# FLOOR MULTIPLIER HELPER
# ================================================================

_floor_cache = {"floor": 0.0, "ts": 0}

def get_cached_floor(slug):
    now = time.time()
    if now - _floor_cache["ts"] < 300 and _floor_cache["floor"] > 0:
        return _floor_cache["floor"]
    try:
        r = requests.get(
            f"https://api.opensea.io/api/v2/collections/{slug}/stats",
            headers=OPENSEA_HEADERS, timeout=8
        )
        f = r.json().get("total", {}).get("floor_price", 0)
        if f:
            _floor_cache.update({"floor": float(f), "ts": now})
            return float(f)
    except:
        pass
    return _floor_cache["floor"] or 0.0

def sale_tier(price_eth, floor):
    """Returns sale tier based on floor multiple."""
    if floor <= 0:
        return "normal"
    multiple = price_eth / floor
    if multiple >= 8:
        return "mega"
    elif multiple >= 4:
        return "high"
    elif multiple >= 1.5:
        return "solid"
    return "normal"

# ================================================================
# TWEET TEMPLATES
# ================================================================

def tweet_single_sale(token_id, price_eth, price_usd, floor=0):
    tier = sale_tier(price_eth, floor)
    link = opensea_link(token_id)
    if tier == "mega":
        templates = [
            f"Incredible! Normie #{token_id} smashed it and sold for {price_eth:.4f} ETH ({price_usd}) — 8x+ floor! Absolute grail move 🤯\n{link}\n\nNormies.",
        ]
    elif tier == "high":
        templates = [
            f"Whoa! Normie #{token_id} just sold for {price_eth:.4f} ETH ({price_usd}) – way above floor. Legend status unlocked. 👑\n{link}\n\nNormies.",
        ]
    elif tier == "solid":
        templates = [
            f"Boom! Normie #{token_id} flipped for {price_eth:.4f} ETH ({price_usd}). Someone's collecting that monochrome heat 🔥\n{link}\n\nNormies.",
        ]
    else:
        templates = [
            f"Oh look! Normie #{token_id} was just sold for {price_eth:.4f} ETH ({price_usd})! Pixel power on the move. ⚡\n{link}\n\nNormies.",
        ]
    return random.choice(templates)

def tweet_sweep(count, total_eth, total_usd, buyer):
    ens = get_ens_name(buyer)
    templates = [
        f"Woah, watch out! 👀\n\nAnother big @normiesART sweep!\n\n{ens} swept {count} Normies for {total_eth:.3f} ETH ({total_usd})\n\nNormies.",
        f"Someone's hungry! 🚨\n\n{ens} just swept {count} Normies for {total_eth:.3f} ETH ({total_usd})\n\nNormies.",
    ]
    return random.choice(templates)

def tweet_grail(token_id, ntype, price_eth, price_usd):
    article = "an" if ntype in ["Alien", "Agent"] else "a"
    link    = opensea_link(token_id)
    return (
        f"Can't believe it, that's {article} {ntype}! 🥶\n\n"
        f"That's an @normiesART grail for sure!\n\n"
        f"Normie #{token_id} — {price_eth:.4f} ETH ({price_usd})\n"
        f"{link}\n\nNormies."
    )

def tweet_burn(receiver_id, count, owner, ap=0):
    ens    = get_ens_name(owner) if owner else "unknown"
    plural = "Normies" if count != 1 else "Normie"
    link   = opensea_link(receiver_id)
    if count >= 20:
        templates = [
            f"Legendary move: {ens} burned {count} {plural} for Normie #{receiver_id}. On-chain history being written right now. 📜\n{link}\n\n@normiesART #normies #NormiesCanvas",
            f"Massive burn! {ens} just sacrificed {count} {plural} for Normie #{receiver_id}. That's how you build a masterpiece. 🎨\n{link}\n\n@normiesART #normies #NormiesCanvas",
        ]
    else:
        templates = [
            f"Burn alert! {ens} torched {count} {plural} for Normie #{receiver_id}. Pixel evolution in progress. 🔥\n{link}\n\n@normiesART #normies #NormiesCanvas",
            f"{ens} just burned {count} {plural} for Normie #{receiver_id}. Sacrifice for the canvas! 🔥\n{link}\n\n@normiesART #normies #NormiesCanvas",
        ]
    return random.choice(templates)

def tweet_canvas(token_id, ap, changes):
    link = opensea_link(token_id)
    templates = [
        f"🎨 Canvas change!\n\nNormie #{token_id} was just edited — {changes} pixels changed.\n{link}\n\n@normiesART #normies #NormiesCanvas",
        f"🎨 Artist at work! Normie #{token_id} just got {changes} pixels updated.\n{link}\n\n@normiesART #normies #NormiesCanvas",
    ]
    return random.choice(templates)

def tweet_floor(direction, pct, new_floor, new_usd):
    if direction == "up":
        return (
            f"Floor price just jumped to {new_floor:.4f} ETH ({new_usd})! "
            f"Normies are heating up – the pixel revolution continues 📈\n\n"
            f"@normiesART #normies #NFT"
        )
    else:
        return (
            f"Floor alert! Normies floor now at {new_floor:.4f} ETH ({new_usd}). "
            f"Whether you're buying or burning… the canvas is calling 📉\n\n"
            f"@normiesART #normies #NFT"
        )


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
# ENS + OPENSEA
# ================================================================

_ens_cache = {}  # {address: (name, timestamp)}
ENS_TTL   = 3600  # 1 hour

def get_ens_name(address):
    if not address: return "unknown"
    cached = _ens_cache.get(address)
    if cached and time.time() - cached[1] < ENS_TTL:
        return cached[0]
    try:
        r = requests.get(f"https://api.ensideas.com/ens/resolve/{address}", timeout=5)
        name   = r.json().get("name")
        result = name if name else address
        _ens_cache[address] = (result, time.time())
        return result
    except:
        _ens_cache[address] = (address, time.time())
        return address

def opensea_link(token_id):
    return f"https://opensea.io/collection/normies"

# ================================================================
# DATABASE
# ================================================================

def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({"collections": {}, "bot_paused": False}, f)
    with open(DB_FILE, "r+") as f:
        db = json.load(f)
        if "bot_paused" not in db:
            db["bot_paused"] = False
        for col in COLLECTIONS:
            s = col["slug"]
            if s not in db["collections"]:
                db["collections"][s] = {
                    "last_sale_tx":   None,
                    "last_burn_id":   None,
                    "last_canvas_tx": {},
                    "last_floor":     None,
                    "total_volume":   0.0,
                    "history":        {},
                    "buyer_window":   {},
                    "posted_txs":     [],   # dedupe: last 200 posted tx hashes
                }
        f.seek(0); json.dump(db, f, indent=2); f.truncate()

def load_db():
    with open(DB_FILE, "r") as f: return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=2)

# ================================================================
# TWITTER CLIENT
# ================================================================

_tc = {}
def get_tw(name):
    if name not in _tc:
        c = TWITTER_CONFIGS[name]
        _tc[name] = tweepy.Client(
            c["bearer_token"], c["api_key"], c["api_secret"],
            c["access_token"], c["access_token_secret"]
        )
    return _tc[name]

# ================================================================
# KILLSWITCH — check DMs for STOP/START command
# ================================================================

_last_dm_check = 0

def check_killswitch(acc_name):
    """
    Polls recent mentions for STOP or START commands from trusted accounts.
    Works on Free X API tier (no DM endpoint needed).
    Send: @normiesartbot STOP  or  @normiesartbot START
    """
    global _last_dm_check
    now = time.time()
    if now - _last_dm_check < 120:  # check every 2 min
        return
    _last_dm_check = now

    try:
        client = get_tw(acc_name)
        # Get recent mentions (v2 endpoint, works on Free tier)
        me = client.get_me()
        if not me or not me.data:
            return
        bot_id = me.data.id

        mentions = client.get_users_mentions(
            id=bot_id,
            max_results=5,
            tweet_fields=["author_id", "text"]
        )
        if not mentions or not mentions.data:
            return

        db = load_db()
        changed = False

        for mention in mentions.data:
            author_id = str(mention.author_id)
            text      = mention.text.strip().upper()

            # Resolve author username
            try:
                user     = client.get_user(id=author_id)
                username = user.data.username.lower() if user and user.data else ""
            except:
                username = ""

            if username not in [k.lower() for k in KILLSWITCH_ACCOUNTS]:
                continue

            if "STOP" in text and not db.get("bot_paused"):
                db["bot_paused"] = True
                _rate_limiter.emergency_pause(86400)
                changed = True
                log.warning(f"[KILLSWITCH] STOP from @{username}. Bot paused 24h.")

            elif "START" in text and db.get("bot_paused"):
                db["bot_paused"] = False
                _rate_limiter.paused = False
                changed = True
                log.info(f"[KILLSWITCH] START from @{username}. Bot resumed.")

        if changed:
            save_db(db)

    except Exception as e:
        log.warning(f"[Killswitch] Mention check error: {e}")

# ================================================================
# POSTING
# ================================================================

def _post_tw_now(acc, text):
    """Actually post to Twitter — called only from flush_queue."""
    try:
        get_tw(acc).create_tweet(text=text)
        _rate_limiter.record_tweet()
        log.info(f"[TW ✓] {text[:80]}...")
    except tweepy.errors.TooManyRequests:
        _rate_limiter.emergency_pause(900)
        log.error("[TW] Rate limited by X. Pausing 15 min.")
    except tweepy.errors.Forbidden as e:
        log.error(f"[TW] Forbidden: {e}")
        _rate_limiter.record_error()
    except tweepy.errors.Unauthorized:
        log.critical("[TW] Unauthorized — check API keys!")
        _rate_limiter.emergency_pause(3600)
    except Exception as e:
        log.error(f"[TW ERR] {e}")
        _rate_limiter.record_error()

def post_dc(url, embed):
    if not url or "YOUR_DISCORD" in url: return
    try:
        requests.post(url, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        log.warning(f"[Discord ERR] {e}")

def post_all(col, tw_text, dc_embed, priority=False):
    """Queue tweet + immediately send to Discord."""
    db = load_db()
    if db.get("bot_paused"):
        log.info("[PAUSED] Bot is paused, skipping post.")
        return
    queue_tweet(col["twitter_account"], tw_text, priority=priority)
    post_dc(col["discord_webhook"], dc_embed)

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
    except: return ""

def get_normie_ap(token_id):
    try:
        r = requests.get(f"{NORMIES_API}/normie/{token_id}/canvas/info", timeout=8)
        return r.json().get("actionPoints", 0)
    except: return 0

def get_normie_img(token_id):
    return f"{NORMIES_API}/normie/{token_id}/image.png"

def get_burn_history(limit=10):
    try:
        r = requests.get(f"{NORMIES_API}/history/burns?limit={limit}", timeout=8)
        d = r.json()
        return d if isinstance(d, list) else []
    except: return []

def get_canvas_versions(token_id):
    try:
        r = requests.get(f"{NORMIES_API}/history/normie/{token_id}/versions", timeout=8)
        d = r.json()
        return d if isinstance(d, list) else []
    except: return []

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
        log.warning(f"[OpenSea ERR] {e}"); return []

def get_collection_stats(slug):
    try:
        r = requests.get(
            f"https://api.opensea.io/api/v2/collections/{slug}/stats",
            headers=OPENSEA_HEADERS, timeout=10
        )
        t = r.json().get("total", {})
        return t.get("floor_price"), t.get("volume")
    except: return None, None

# ================================================================
# SPENDING TRACKER
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
    bw     = {b: [t for t in ts if now_ts - t < window] for b, ts in bw.items()}

    sweep_posted = set()

    for s in reversed(new_sales):
        tx       = s["tx"]
        buyer    = s["buyer"]
        price    = s["price"]
        token_id = s["tokenId"]
        url      = s["url"] or opensea_link(token_id)
        short    = f"{buyer[:6]}...{buyer[-4:]}"
        usd      = fmt_usd(price)

        if buyer not in bw: bw[buyer] = []
        bw[buyer].append(now_ts)

        buyer_total_eth = sum(s2["price"] for s2 in new_sales if s2["buyer"] == buyer)

        # SWEEP
        if len(bw[buyer]) >= col["sweep_count"] and buyer_total_eth >= col["sweep_min_eth"] and buyer not in sweep_posted:
            cnt       = len(bw[buyer])
            total_usd = fmt_usd(buyer_total_eth)
            tw = tweet_sweep(cnt, buyer_total_eth, total_usd, buyer)
            dc = {
                "title":  f"👀 Sweep — {cnt} Normies!",
                "color":  0xFF6600,
                "fields": [
                    {"name": "Buyer", "value": f"`{short}`",                          "inline": True},
                    {"name": "Count", "value": str(cnt),                               "inline": True},
                    {"name": "Total", "value": f"{buyer_total_eth:.3f} ETH ({total_usd})", "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc, priority=True)
            sweep_posted.add(buyer)
            bw[buyer] = []

        elif buyer not in sweep_posted and price >= col["sale_min_eth"]:
            # Deduplicate — skip if already posted
            posted_txs = cd.get("posted_txs", [])
            if tx in posted_txs:
                log.info(f"[Dedupe] Tx {tx[:10]}... already posted, skipping.")
                continue
            ntype    = get_normie_type(token_id)
            is_grail = ntype in GRAIL_TYPES

            if is_grail:
                tw    = tweet_grail(token_id, ntype, price, usd)
                color = 0x9B59B6
            else:
                floor = get_cached_floor(col["slug"])
                tw    = tweet_single_sale(token_id, price, usd, floor)
                color = 0x00CC66

            dc = {
                "title":  f"{'🥶 GRAIL' if is_grail else '😯 Sale'} — #{token_id}",
                "color":  color,
                "fields": [
                    {"name": "Buyer", "value": f"`{short}`",          "inline": True},
                    {"name": "Price", "value": f"{price:.4f} ETH ({usd})", "inline": True},
                    {"name": "Type",  "value": ntype or "Human",       "inline": True},
                    {"name": "Link",  "value": f"[OpenSea]({url})",    "inline": False},
                ],
                "image":     {"url": get_normie_img(token_id)},
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc, priority=is_grail)

        cd["total_volume"] = cd.get("total_volume", 0.0) + price
        cd["last_sale_tx"] = tx
        # Track posted txs for deduplication (keep last 200)
        posted_txs = cd.get("posted_txs", [])
        if tx not in posted_txs:
            posted_txs.append(tx)
            cd["posted_txs"] = posted_txs[-200:]

    cd["buyer_window"] = bw
    log.info(f"[{col['name']}] {len(new_sales)} new sales processed.")

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
        receiver = burn.get("receiverTokenId", "?")
        count    = burn.get("tokenCount", 0)
        owner    = burn.get("owner", "")
        short    = f"{owner[:6]}...{owner[-4:]}"
        ap       = get_normie_ap(receiver)

        if ap >= col["burn_ap_min"]:
            tw = tweet_burn(receiver, count, owner, ap)
            dc = {
                "title":  f"🔥 Burn — #{receiver} got {ap} AP!",
                "color":  0xFF3300,
                "fields": [
                    {"name": "Owner",  "value": f"`{short}`", "inline": True},
                    {"name": "Burned", "value": str(count),   "inline": True},
                    {"name": "AP",     "value": str(ap),       "inline": True},
                ],
                "image":     {"url": get_normie_img(receiver)},
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)

        cd["last_burn_id"] = str(burn.get("commitId"))

# ================================================================
# FEATURE 3: CANVAS
# ================================================================

def check_canvas_changes(col, db):
    slug     = col["slug"]
    cd       = db["collections"][slug]
    last_cvs = cd.get("last_canvas_tx", {})

    # Seed canvas watcher from sold token IDs in last_sale_tx
    # so it automatically discovers tokens that were recently sold
    recent_sales = get_recent_sales(slug, limit=20)
    for s in recent_sales[:5]:
        tid = str(s.get("tokenId", ""))
        if tid and tid not in last_cvs:
            ap = get_normie_ap(tid)
            if ap >= col["canvas_ap_min"]:
                last_cvs[tid] = ""  # seed with empty tx to start watching
                log.info(f"[Canvas] Auto-seeded token #{tid} with {ap} AP")

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
                        {"name": "Pixels", "value": str(changes), "inline": True},
                        {"name": "AP",     "value": str(ap),       "inline": True},
                    ],
                    "image":     {"url": get_normie_img(token_id)},
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                post_all(col, tw, dc)
            last_cvs[str(token_id)] = latest_tx

    cd["last_canvas_tx"] = last_cvs

# ================================================================
# FEATURE 4: FLOOR
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
                    {"name": "Old",    "value": f"{last:.4f} ETH",                     "inline": True},
                    {"name": "Change", "value": f"{pct:+.1f}%",                         "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            post_all(col, tw, dc)
    cd["last_floor"] = floor



_startup_done = False

def main():
    log.info("=" * 50)
    log.info("  Normies Bot — Secure Edition")
    log.info(f"  Collections: {[c['name'] for c in COLLECTIONS]}")
    log.info(f"  Rate limit: {MAX_TWEETS_PER_HOUR}/h, {MAX_TWEETS_PER_DAY}/day")
    log.info(f"  Killswitch accounts: {KILLSWITCH_ACCOUNTS}")
    log.info("=" * 50)

    init_db()
    # Sync paused state from DB on startup
    _startup_db = load_db()
    if _startup_db.get("bot_paused"):
        _rate_limiter.emergency_pause(86400)
        log.warning("[Startup] Bot was paused from previous session.")
    fc        = 0

    while True:
        now = datetime.datetime.utcnow()
        db  = load_db()

        # Check killswitch DMs every loop
        for acc in TWITTER_CONFIGS:
            try: check_killswitch(acc)
            except Exception as e: log.warning(f"[Killswitch ERR] {e}")

        # Skip all posting if bot is paused
        if not db.get("bot_paused", False):

            for col in COLLECTIONS:
                try: process_sales(col, db)
                except Exception as e: log.error(f"[Sales ERR] {e}")

                try: check_burns(col, db)
                except Exception as e: log.error(f"[Burns ERR] {e}")

                try: check_canvas_changes(col, db)
                except Exception as e: log.error(f"[Canvas ERR] {e}")


            fc += 1
            if fc >= FLOOR_CHECK_EVERY:
                for col in COLLECTIONS:
                    try: check_floor(col, db)
                    except Exception as e: log.error(f"[Floor ERR] {e}")
                fc = 0



        save_db(db)

        # On first loop: skip queue to avoid posting old backlog
        global _startup_done
        if not _startup_done:
            _tweet_queue.clear()
            log.info("[Startup] Queue cleared — only new events will be posted.")
            _startup_done = True
        else:
            flush_queue()

        # Log queue status
        if _tweet_queue:
            log.info(f"[Queue] {len(_tweet_queue)} tweets pending.")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
