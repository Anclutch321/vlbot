import time
import math
import requests

# ==========================
# CONFIG
# ==========================

MEXC_BASE_URL = "https://contract.mexc.com"
TOP_GAINER_PCT = 10.0
SPREAD_HTF_MAX = 0.01
WEEKLY_DIST_MAX = 0.005
SLEEP_SECONDS = 60

EMA_LEN_1 = 34
EMA_LEN_2 = 89
EMA_LEN_3 = 200
BOLL_LEN = 21
BOLL_DEV = 2.0

TF_1D = "Day1"
TF_1W = "Week1"

# ==========================
# TELEGRAM CONFIG
# ==========================

TELEGRAM_BOT_TOKEN = "8055185544:AAFXqsxeK6j-Sjm24vyc5IF9pdM-xJ7dLDY"
TELEGRAM_CHAT_ID   = "6975292643"

# ==========================
# TELEGRAM SEND
# ==========================

def send_telegram(text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except:
        pass

# ==========================
# EMA + BOLL
# ==========================

def ema(values, length):
    if len(values) < length:
        return []
    k = 2 / (length + 1)
    out = []
    sma = sum(values[:length]) / length
    out.append(sma)
    prev = sma
    for v in values[length:]:
        val = v * k + prev * (1 - k)
        out.append(val)
        prev = val
    return out

def bollinger(values, length, dev):
    if len(values) < length:
        return [], []
    basis = []
    upper = []
    for i in range(length - 1, len(values)):
        win = values[i - length + 1 : i + 1]
        m = sum(win) / length
        sd = math.sqrt(sum((x - m) ** 2 for x in win) / length)
        basis.append(m)
        upper.append(m + dev * sd)
    return basis, upper

# ==========================
# MEXC API
# ==========================

def get_tickers_mexc():
    url = MEXC_BASE_URL + "/api/v1/contract/ticker"
    r = requests.get(url, timeout=10).json()
    return r["data"]

def get_klines_mexc(symbol: str, interval: str, limit=300):
    url = MEXC_BASE_URL + f"/api/v1/contract/kline/{symbol}"
    params = {"interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10).json()

    if "data" not in r or r["data"] is None:
        return []

    kl = []
    for k in r["data"]:

        # Case 1: kline trả dạng LIST
        if isinstance(k, list) or isinstance(k, tuple):
            kl.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5])
            })

        # Case 2: kline trả dạng DICT
        elif isinstance(k, dict):
            kl.append({
                "open_time": k.get("t"),
                "open": float(k.get("o")),
                "high": float(k.get("h")),
                "low": float(k.get("l")),
                "close": float(k.get("c")),
                "volume": float(k.get("v"))
            })

    return kl


# ==========================
# LEVEL CALC
# ==========================

def compute_levels(closes):
    e34 = ema(closes, EMA_LEN_1)
    e89 = ema(closes, EMA_LEN_2)
    e200 = ema(closes, EMA_LEN_3)
    basis, upper = bollinger(closes, BOLL_LEN, BOLL_DEV)
    if not e34 or not e89 or not e200 or not upper:
        return None
    return e34[-1], e89[-1], e200[-1], upper[-1]

# ==========================
# CHECK CONFLUENCE
# ==========================

def check_confluence(symbol):
    d = get_klines_mexc(symbol, TF_1D)
    w = get_klines_mexc(symbol, TF_1W)

    if not d or not w:
        return False, ""

    closes_d = [k["close"] for k in d]
    closes_w = [k["close"] for k in w]

    d_vals = compute_levels(closes_d)
    w_vals = compute_levels(closes_w)

    if not d_vals or not w_vals:
        return False, ""

    ema34d, ema89d, ema200d, bollUd = d_vals
    ema34w, ema89w, ema200w, bollUw = w_vals

    price = closes_d[-1]

    levels = [
        ema34d, ema89d, ema200d, bollUd,
        ema34w, ema89w, ema200w, bollUw
    ]

    spread = (max(levels) - min(levels)) / price
    if spread > SPREAD_HTF_MAX:
        return False, ""

    weekly_levels = [ema34w, ema89w, ema200w, bollUw]
    best_dist = min(abs(price - lv) / price for lv in weekly_levels)

    if best_dist > WEEKLY_DIST_MAX:
        return False, ""

    msg = (
        f"🔥 *HTF CONFLUENCE ALERT*\n\n"
        f"Symbol: `{symbol}`\n"
        f"Price: `{price}`\n"
        f"- EMA34/89/200 + Boll(21,2) D + W trùng vùng.\n"
        f"- Giá gần cản Weekly (~{best_dist*100:.2f}%).\n"
        f"👉 Kiểm tra SHORT.\n"
    )
    return True, msg

# ==========================
# MAIN LOOP
# ==========================

def scan_once():
    data = get_tickers_mexc()
    gainers = []
    for x in data:
        pct = float(x["riseFallRate"]) * 100
        if pct >= TOP_GAINER_PCT:
            gainers.append((x["symbol"], pct))

    gainers.sort(key=lambda x: x[1], reverse=True)
    print("Gainers:", gainers)

    for sym, pct in gainers:
        ok, msg = check_confluence(sym)
        if ok:
            send_telegram(msg)
        time.sleep(0.3)

def main():
    print("Bot đang chạy…")
    send_telegram("🤖 MEXC HTF bot started")
    while True:
        scan_once()
        time.sleep(SLEEP_SECONDS)

main()
