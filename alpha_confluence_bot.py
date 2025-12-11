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

# Boll squeeze 1D
BB_SQUEEZE_ABS = 0.08   # dải Boll < 8% giá -> tương đối hẹp
BB_SQUEEZE_REL = 0.50   # dải hiện tại < 50% dải trung bình lịch sử

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
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
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
# DAILY BOLL SQUEEZE 1D
# ==========================

def is_daily_boll_squeeze_1d(closes):
    """
    Squeeze 1D khi:
      - Dải Boll(21,2) hiện tại hẹp so với giá (width_abs)
      - Đồng thời hẹp rõ rệt so với chính lịch sử của nó (width_rel)
    Trả về: (is_squeeze, width_abs, width_rel)
    """
    lookback_hist = BOLL_LEN * 4  # ví dụ: 21 * 4 = 84 nến D làm lịch sử

    if len(closes) < lookback_hist:
        return False, None, None

    # --- width hiện tại trên đoạn 21 nến cuối ---
    window_now = closes[-BOLL_LEN:]
    mid_now = sum(window_now) / BOLL_LEN
    if mid_now == 0:
        return False, None, None

    var_now = sum((x - mid_now) ** 2 for x in window_now) / BOLL_LEN
    sd_now = math.sqrt(var_now)
    width_now = 2 * BOLL_DEV * sd_now          # UP - DN
    width_abs = width_now / mid_now            # so với giá hiện tại

    # --- width lịch sử: trung bình width của nhiều đoạn 21 nến trước đó ---
    widths_hist = []
    # bỏ đoạn 21 nến cuối cùng để không trùng window_now
    for i in range(BOLL_LEN - 1, len(closes) - BOLL_LEN):
        win = closes[i - BOLL_LEN + 1 : i + 1]
        m = sum(win) / BOLL_LEN
        if m == 0:
            continue
        var = sum((x - m) ** 2 for x in win) / BOLL_LEN
        sd = math.sqrt(var)
        widths_hist.append(2 * BOLL_DEV * sd)

    if not widths_hist:
        return False, None, None

    avg_width_hist = sum(widths_hist) / len(widths_hist)
    if avg_width_hist == 0:
        return False, None, None

    width_rel = width_now / avg_width_hist     # dải hiện tại / trung bình lịch sử

    is_sq = (width_abs <= BB_SQUEEZE_ABS) and (width_rel <= BB_SQUEEZE_REL)
    return is_sq, width_abs, width_rel

def check_daily_boll_squeeze(symbol: str):
    """
    Quét BÓP BOLL 1D cho 1 symbol.
    Dùng trong scan_once để quét toàn sàn.
    """
    d_klines = get_klines_mexc(symbol, TF_1D)
    if not d_klines:
        return False, ""

    closes = [k["close"] for k in d_klines]
    sq, width_abs, width_rel = is_daily_boll_squeeze_1d(closes)
    if not sq:
        return False, ""

    price = closes[-1]

    msg = (
        f"📉 *DAILY BOLL SQUEEZE*\n\n"
        f"Symbol: `{symbol}` (1D)\n"
        f"Price: `{price}`\n"
        f"- Dải Boll hiện tại ≈ {width_abs*100:.2f}% giá.\n"
        f"- Hẹp còn ≈ {width_rel*100:.1f}% so với dải trung bình lịch sử.\n"
        f"👉 Pattern nén mạnh kiểu STBL/KGEN: pump/xả xong biên thu hẹp, dễ chuẩn bị pha biến động mới."
    )
    return True, msg

# ==========================
# CHECK CONFLUENCE EMA/BOLL D+W
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

    # 1) Top gainer cho logic hợp lưu EMA/Boll D+W
    gainers = []
    for x in data:
        pct = float(x["riseFallRate"]) * 100
        if pct >= TOP_GAINER_PCT:
            gainers.append((x["symbol"], pct))

    gainers.sort(key=lambda x: x[1], reverse=True)
    print("Gainers:", gainers)

    # Hợp lưu EMA/Boll: chỉ cho top gainer
    for sym, pct in gainers:
        ok, msg = check_confluence(sym)
        if ok:
            send_telegram(msg)
        time.sleep(0.2)

    # 2) Bóp Boll 1D: QUÉT TOÀN SÀN
    for x in data:
        sym = x["symbol"]
        ok_sq, msg_sq = check_daily_boll_squeeze(sym)
        if ok_sq:
            send_telegram(msg_sq)
        time.sleep(0.2)

def main():
    print("Bot đang chạy…")
    send_telegram("🤖 MEXC HTF bot started")
    while True:
        scan_once()
        time.sleep(SLEEP_SECONDS)

main()
