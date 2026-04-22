import matplotlib
matplotlib.use('Agg') # Railway has no display

import asyncio
import json
import pandas as pd
import numpy as np
import websockets
import os
from datetime import datetime
from telegram import Bot
import matplotlib.pyplot as plt

# ==================== CONFIG FROM RAILWAY ====================
DERIV_TOKEN = os.getenv("9DGNebiMBPp4f2g")
TELEGRAM_TOKEN = os.getenv("8210683598:AAFqpRFWDNT1xu-zr5zVD3YlU4U58aA8ux8")
CHAT_ID = os.getenv("5997058899")
AFFILIATE_LINK = os.getenv("AFFILIATE_LINK", "https://deriv.com")

SYMBOL = "R_75"
STAKE = 1.0
MAX_DAILY_LOSS = 3
DAILY_PROFIT_TARGET = 5.0
PROFIT_LOCK_TRIGGER = 3.0
PROFIT_LOCK_LEVEL = 2.0
# =============================================================

bot = Bot(token=TELEGRAM_TOKEN)
ws = None
loss_streak = 0
daily_profit = 0.0
peak_profit = 0.0
lock_active = False
daily_stopped = False
current_day = datetime.utcnow().date()
last_report_date = None

LOG_FILE = "trades.csv"
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w") as f:
        f.write("date,time,direction,profit,balance,win_rate,reason\n")

async def send_tg(msg, with_link=False):
    try:
        full_msg = msg
        if with_link:
            full_msg += f"\n\n🎯 Trade VIX75: {AFFILIATE_LINK}\n⚠️ 76% lose money. 18+ only."
        await bot.send_message(chat_id=CHAT_ID, text=full_msg, disable_web_page_preview=True)
        print(f"[{datetime.utcnow().strftime('%H:%M')}] {msg}")
    except Exception as e:
        print(f"TG error: {e}")

async def send_chart(df, direction, ob, reason):
    try:
        plt.figure(figsize=(10,5))
        last60 = df.tail(60)
        plt.plot(last60['close'].values, linewidth=2, color='black')
        if ob:
            plt.axhspan(ob['bottom'], ob['top'], xmin=0.7, xmax=1.0, color='blue', alpha=0.25)
        plt.scatter(59, last60['close'].iloc[-1], color='green' if direction=='long' else 'red', s=200, edgecolor='black')
        plt.title(f'{direction.upper()} | {reason}', weight='bold')
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig("setup.png", dpi=120)
        plt.close()
        with open("setup.png", 'rb') as p:
            await bot.send_photo(CHAT_ID, p, caption=f"📊 {direction.upper()}\n{reason}")
    except Exception as e:
        print(f"Chart: {e}")

def reset_daily():
    global daily_profit, peak_profit, lock_active, loss_streak, daily_stopped, current_day
    today = datetime.utcnow().date()
    if today!= current_day:
        daily_profit = 0.0
        peak_profit = 0.0
        lock_active = False
        daily_stopped = False
        loss_streak = 0
        current_day = today

def update_trailing_lock():
    global peak_profit, lock_active, daily_stopped
    peak_profit = max(peak_profit, daily_profit)
    if peak_profit >= PROFIT_LOCK_TRIGGER and not lock_active:
        lock_active = True
        asyncio.create_task(send_tg(f"🔒 Profit lock ON at +${peak_profit:.2f}"))
    if lock_active and daily_profit <= PROFIT_LOCK_LEVEL and not daily_stopped:
        daily_stopped = True
        asyncio.create_task(send_tg(f"🛑 Lock hit. Secured +${daily_profit:.2f} today", with_link=True))

def get_win_rate():
    try:
        df = pd.read_csv(LOG_FILE)
        return round(len(df[df['profit']>0])/len(df)*100,1) if len(df)>0 else 0
    except: return 0

def log_trade(direction, profit, balance, reason):
    win_rate = get_win_rate()
    now = datetime.utcnow()
    with open(LOG_FILE, "a") as f:
        f.write(f"{now.date()},{now.strftime('%H:%M')},{direction},{profit},{balance},{win_rate},{reason}\n")
    return win_rate

async def deriv_connect():
    global ws
    ws = await websockets.connect('wss://ws.derivws.com/websockets/v3?app_id=1089')
    await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
    await ws.recv()
    await send_tg("✅ Railway Bot Online 24/7", with_link=True)

async def deriv_send(r):
    await ws.send(json.dumps(r))
    return json.loads(await ws.recv())

async def get_candles():
    d = await deriv_send({"ticks_history": SYMBOL, "count": 300, "end": "latest", "granularity": 300, "style": "candles"})
    df = pd.DataFrame(d['candles'])
    for c in ['open','high','low','close']: df[c] = df[c].astype(float)
    return df

def analyze(df):
    ema50 = df['close'].ewm(50).mean()
    ema200 = df['close'].ewm(200).mean()
    bull_trend = ema50.iloc[-1] > ema200.iloc[-1]
    bear_trend = ema50.iloc[-1] < ema200.iloc[-1]

    sh = df['high'].iloc[-25:-5].max()
    sl = df['low'].iloc[-25:-5].min()
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    bos_up = curr['close'] > sh and prev['close'] <= sh
    bos_down = curr['close'] < sl and prev['close'] >= sl
    bull_sweep = curr['low'] < sl and curr['close'] > sl
    bear_sweep = curr['high'] > sh and curr['close'] < sh
    bull_fvg = df['low'].iloc[-1] > df['high'].iloc[-3]
    bear_fvg = df['high'].iloc[-1] < df['low'].iloc[-3]

    long_ok = short_ok = False
    ob = None
    reason = ""

    if bull_trend and bos_up and bull_sweep and bull_fvg:
        for i in range(len(df)-10, len(df)-30, -1):
            c = df.iloc[i]
            if c['close'] < c['open']:
                ob = {'top': c['open'], 'bottom': c['low']}
                if curr['low'] <= ob['top']:
                    long_ok = True
                    reason = f"UP | BOS {sh:.1f} | OB"
                break

    if bear_trend and bos_down and bear_sweep and bear_fvg:
        for i in range(len(df)-10, len(df)-30, -1):
            c = df.iloc[i]
            if c['close'] > c['open']:
                ob = {'top': c['high'], 'bottom': c['open']}
                if curr['high'] >= ob['bottom']:
                    short_ok = True
                    reason = f"DOWN | BOS {sl:.1f} | OB"
                break

    return long_ok, short_ok, ob, reason

async def send_daily_report():
    global last_report_date
    try:
        today = datetime.utcnow().date()
        if last_report_date == today: return
        df = pd.read_csv(LOG_FILE)
        today_df = df[df['date'] == str(today)]
        if len(today_df) > 0:
            wr = round(len(today_df[today_df['profit']>0])/len(today_df)*100,1)
            pnl = today_df['profit'].sum()
            await send_tg(f"📊 DAILY REPORT\nTrades: {len(today_df)} | WR: {wr}%\nP&L: ${pnl:+.2f}", with_link=True)
            last_report_date = today
    except: pass

async def trade(direction, ob, reason, df):
    global loss_streak, daily_profit
    reset_daily()
    if daily_stopped or loss_streak >= MAX_DAILY_LOSS or daily_profit >= DAILY_PROFIT_TARGET:
        return
    await send_chart(df, direction, ob, reason)
    await asyncio.sleep(1)
    contract = "CALL" if direction == "long" else "PUT"
    buy = await deriv_send({"buy":1,"price":STAKE,"parameters":{"contract_type":contract,"symbol":SYMBOL,"duration":5,"duration_unit":"m","basis":"stake","currency":"USD"}})
    cid = buy['buy']['contract_id']
    await asyncio.sleep(310)
    res = await deriv_send({"proposal_open_contract":1,"contract_id":cid})
    profit = float(res['proposal_open_contract']['profit'])
    daily_profit += profit
    loss_streak = 0 if profit>0 else loss_streak+1
    update_trailing_lock()
    win_rate = log_trade(direction, profit, float(res['proposal_open_contract']['balance']), reason)
    await send_tg(f"{'✅' if profit>0 else '❌'} ${profit:+.2f} | Day ${daily_profit:+.2f} | WR {win_rate}%")

async def main():
    await deriv_connect()
    while True:
        try:
            reset_daily()
            # Daily report at 18:00 UTC = 21:00 Nairobi
            if datetime.utcnow().hour == 18 and datetime.utcnow().minute < 2:
                await send_daily_report()

            if not daily_stopped:
                df = await get_candles()
                long_ok, short_ok, ob, reason = analyze(df)
                if long_ok: await trade("long", ob, reason, df)
                elif short_ok: await trade("short", ob, reason, df)

            await asyncio.sleep(30)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(10)
            await deriv_connect()

asyncio.run(main())