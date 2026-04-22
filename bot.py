import matplotlib
matplotlib.use('Agg')
import asyncio, websockets, json, pandas as pd, numpy as np
from datetime import datetime, timezone
import matplotlib.pyplot as plt
import os, io
from telegram import Bot
from threading import Thread
from flask import Flask

# --- Render keep-alive server ---
app = Flask('')
@app.route('/')
def home(): return "SMC Bot Running"
Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()

# --- CONFIG ---
DERIV_TOKEN = os.getenv("DERIV_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "R_75"
DAILY_PROFIT_TARGET = 3.0
DAILY_LOSS_LIMIT = 2.0
STAKE = 1.0

bot = Bot(token=TELEGRAM_TOKEN)
candles = []
daily_pnl = 0.0
trade_active = False

# --- TELEGRAM ---
async def send_telegram(msg, chart_buf=None):
    try:
        if chart_buf:
            await bot.send_photo(chat_id=CHAT_ID, photo=chart_buf, caption=msg)
        else:
            await bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print("Telegram error:", e)

def send_chart(df, signal, entry):
    plt.figure(figsize=(10,5))
    plt.plot(df['close'], label='Close', linewidth=1.5)
    plt.scatter(df.index[-1], entry, color='green' if signal=='BUY' else 'red', s=100, zorder=5)
    plt.title(f"VIX75 SMC Signal: {signal} @ {entry:.2f}")
    plt.legend()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf

# --- SMC LOGIC ---
def detect_ob(df):
    obs = []
    for i in range(2, len(df)-2):
        if df['close'][i] < df['open'][i] and df['close'][i+1] > df['high'][i]: # bullish OB
            obs.append((i, 'bull'))
        if df['close'][i] > df['open'][i] and df['close'][i+1] < df['low'][i]: # bearish OB
            obs.append((i, 'bear'))
    return obs[-1] if obs else None

def detect_bos(df):
    if len(df) < 5: return None
    hh = df['high'].iloc[-2] > df['high'].iloc[-3]
    ll = df['low'].iloc[-2] < df['low'].iloc[-3]
    if hh and df['close'].iloc[-1] > df['high'].iloc[-2]: return 'bull'
    if ll and df['close'].iloc[-1] < df['low'].iloc[-2]: return 'bear'
    return None

def is_sweep(df):
    if len(df) < 3: return False
    return df['low'].iloc[-2] < df['low'].iloc[-3] and df['close'].iloc[-1] > df['low'].iloc[-2]

def smc_signal(df):
    ob = detect_ob(df)
    bos = detect_bos(df)
    sweep = is_sweep(df)
    if ob and bos and sweep:
        if ob[1]=='bull' and bos=='bull': return 'BUY'
        if ob[1]=='bear' and bos=='bear': return 'SELL'
    return None

# --- DERIV ---
async def get_candles():
    uri = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"ticks_history": SYMBOL, "end": "latest", "count": 100, "style": "candles", "granularity": 60}))
        data = json.loads(await ws.recv())
        return data['candles']

async def trade_loop():
    global candles, daily_pnl, trade_active
    await send_telegram("✅ SMC Bot Online 24/7 - Render")

    while True:
        try:
            if daily_pnl >= DAILY_PROFIT_TARGET:
                await send_telegram(f"🎯 Daily target ${DAILY_PROFIT_TARGET} reached. Stopping.")
                await asyncio.sleep(3600); continue
            if daily_pnl <= -DAILY_LOSS_LIMIT:
                await send_telegram(f"🛑 Daily loss ${DAILY_LOSS_LIMIT} hit. Stopping.")
                await asyncio.sleep(3600); continue

            raw = await get_candles()
            df = pd.DataFrame(raw)
            df['epoch'] = pd.to_datetime(df['epoch'], unit='s')
            df.set_index('epoch', inplace=True)
            df = df.astype(float)

            signal = smc_signal(df)
            price = df['close'].iloc[-1]

            if signal and not trade_active:
                trade_active = True
                chart = send_chart(df, signal, price)
                await send_telegram(f"🔥 {signal} Signal\nEntry: {price:.2f}\nTime: {datetime.now(timezone.utc).strftime('%H:%M:%S')}", chart)

                # Simulate trade outcome (replace with real Deriv buy later)
                await asyncio.sleep(60)
                pnl = STAKE * 0.9 if np.random.rand() > 0.4 else -STAKE
                daily_pnl += pnl
                result = "WIN" if pnl>0 else "LOSS"
                await send_telegram(f"{result} {pnl:+.2f} | Daily PnL: ${daily_pnl:.2f}")
                trade_active = False

            await asyncio.sleep(30)
        except Exception as e:
            print("Loop error:", e)
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(trade_loop())
