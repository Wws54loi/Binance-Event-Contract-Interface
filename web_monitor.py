import streamlit as st
import threading
import asyncio
import websockets
import json
import time
from datetime import datetime
import pandas as pd

# 设置页面配置
st.set_page_config(
    page_title="ETHUSDT 箱体震荡监控",
    page_icon="📈",
    layout="wide"
)

# === 核心逻辑类 (与之前类似，但去除了GUI代码) ===
class BoxMonitorBot:
    def __init__(self):
        self.running = False
        self.symbol = "ethusdt"
        self.levels = {
            "s_res": 0.0, "w_res": 0.0, "w_sup": 0.0, "s_sup": 0.0
        }
        self.active_trades = []
        self.history = []
        self.logs = []
        self.current_price = 0.0
        self.last_trade_time = {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0}
        self.cooldown_seconds = 60
        self.stop_reason = None

    def set_levels(self, s_res, w_res, w_sup, s_sup):
        self.levels = {
            "s_res": float(s_res), "w_res": float(w_res),
            "w_sup": float(w_sup), "s_sup": float(s_sup)
        }
        self.log(f"✅ 参数更新: 强压{s_res} | 弱压{w_res} | 弱撑{w_sup} | 强撑{s_sup}")

    def log(self, msg):
        timestamp = datetime.now().strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg)
        self.logs.insert(0, full_msg) # 最新日志在最前
        if len(self.logs) > 100: self.logs.pop()

    def start(self):
        if self.running: return
        self.running = True
        self.stop_reason = None
        # 在后台线程启动 WebSocket
        threading.Thread(target=self._run_ws_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _run_ws_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_ws())

    async def _connect_ws(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol}@aggTrade"
        self.log(f"正在连接 {url} ...")
        try:
            async with websockets.connect(url) as ws:
                self.log("🟢 监听已启动")
                while self.running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        price = float(data['p'])
                        self.current_price = price
                        
                        self.check_price(price)
                        self.check_trades(price)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        self.log(f"WebSocket Error: {e}")
                        break
        except Exception as e:
            self.log(f"连接失败: {e}")
        finally:
            self.running = False
            self.log("🔴 连接断开")

    def check_price(self, price):
        now = time.time()
        # 逻辑同前...
        if self.levels["s_res"] > 0 and price >= self.levels["s_res"]:
            if now - self.last_trade_time["s_res"] > self.cooldown_seconds:
                self.execute_trade("SHORT", price, "强压力位", "s_res")
        elif self.levels["w_res"] > 0 and price >= self.levels["w_res"]:
             if price < self.levels["s_res"] or self.levels["s_res"] == 0: 
                if now - self.last_trade_time["w_res"] > self.cooldown_seconds:
                    self.execute_trade("SHORT", price, "弱压力位", "w_res")
        if self.levels["s_sup"] > 0 and price <= self.levels["s_sup"]:
            if now - self.last_trade_time["s_sup"] > self.cooldown_seconds:
                self.execute_trade("LONG", price, "强支撑位", "s_sup")
        elif self.levels["w_sup"] > 0 and price <= self.levels["w_sup"]:
            if price > self.levels["s_sup"] or self.levels["s_sup"] == 0:
                if now - self.last_trade_time["w_sup"] > self.cooldown_seconds:
                    self.execute_trade("LONG", price, "弱支撑位", "w_sup")

    def execute_trade(self, direction, price, reason, level_key):
        self.last_trade_time[level_key] = time.time()
        trade = {
            "id": len(self.history) + len(self.active_trades) + 1,
            "direction": direction,
            "entry_price": price,
            "entry_time": time.time(),
            "expiry_time": time.time() + 600,
            "reason": reason,
            "level_key": level_key,
            "status": "OPEN"
        }
        self.active_trades.append(trade)
        self.log(f"🚀 触发交易! {direction} @ {price} | {reason}")

    def check_trades(self, current_price):
        for trade in self.active_trades[:]:
            if time.time() >= trade["expiry_time"]:
                self.settle_trade(trade, current_price)

    def settle_trade(self, trade, current_price):
        self.active_trades.remove(trade)
        is_win = (trade["direction"] == "LONG" and current_price > trade["entry_price"]) or \
                 (trade["direction"] == "SHORT" and current_price < trade["entry_price"])
        
        trade["status"] = "WIN" if is_win else "LOSS"
        trade["exit_price"] = current_price
        trade["exit_time"] = datetime.now().strftime('%H:%M:%S')
        trade["entry_time_str"] = datetime.fromtimestamp(trade["entry_time"]).strftime('%H:%M:%S')
        self.history.append(trade)
        
        res_str = "✅ 赢" if is_win else "❌ 输"
        self.log(f"🏁 结算 #{trade['id']}: {res_str} ({trade['entry_price']} -> {current_price})")
        
        if not is_win:
            if trade["level_key"] == "s_res":
                self.stop_reason = "强压力位突破"
                self.log("🛑 强压力位交易失败 -> 箱体突破，停止监听！")
                self.stop()
            elif trade["level_key"] == "s_sup":
                self.stop_reason = "强支撑位跌破"
                self.log("🛑 强支撑位交易失败 -> 箱体突破，停止监听！")
                self.stop()

# === Streamlit 界面逻辑 ===

# 使用 cache_resource 保证 Bot 实例是全局唯一的 (所有用户看到同一个 Bot)
@st.cache_resource
def get_bot():
    return BoxMonitorBot()

bot = get_bot()

# 侧边栏：控制面板
with st.sidebar:
    st.header("⚙️ 参数设置")
    
    # 如果正在运行，禁用输入框
    disabled = bot.running
    
    s_res = st.number_input("强压力位 (做空)", value=bot.levels["s_res"], disabled=disabled, format="%.2f")
    w_res = st.number_input("弱压力位 (做空)", value=bot.levels["w_res"], disabled=disabled, format="%.2f")
    w_sup = st.number_input("弱支撑位 (做多)", value=bot.levels["w_sup"], disabled=disabled, format="%.2f")
    s_sup = st.number_input("强支撑位 (做多)", value=bot.levels["s_sup"], disabled=disabled, format="%.2f")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 启动/更新", disabled=bot.running, type="primary"):
            bot.set_levels(s_res, w_res, w_sup, s_sup)
            bot.start()
            st.rerun()
            
    with col2:
        if st.button("🛑 停止", disabled=not bot.running):
            bot.stop()
            st.rerun()

    st.markdown("---")
    st.markdown("**状态:**")
    if bot.running:
        st.success("🟢 正在运行")
    else:
        st.error("🔴 已停止")
        if bot.stop_reason:
            st.warning(f"停止原因: {bot.stop_reason}")

# 主界面
st.title("📊 ETHUSDT 箱体震荡实盘监控")

# 实时指标
col_price, col_trades, col_winrate = st.columns(3)
with col_price:
    st.metric("当前价格", f"{bot.current_price:.2f}")
with col_trades:
    total_trades = len(bot.history)
    st.metric("总交易数", total_trades)
with col_winrate:
    wins = len([t for t in bot.history if t["status"] == "WIN"])
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    st.metric("胜率", f"{win_rate:.1f}%")

# 自动刷新 (每2秒刷新一次页面以更新数据)
if bot.running:
    time.sleep(2)
    st.rerun()

# 两个 Tab：当前持仓 和 历史记录
tab1, tab2, tab3 = st.tabs(["📈 当前持仓", "📜 历史记录", "📝 运行日志"])

with tab1:
    if bot.active_trades:
        df_active = pd.DataFrame(bot.active_trades)
        # 格式化显示
        display_cols = ["id", "direction", "entry_price", "reason", "status"]
        st.dataframe(df_active[display_cols], use_container_width=True)
    else:
        st.info("暂无持仓")

with tab2:
    if bot.history:
        df_history = pd.DataFrame(bot.history)
        display_cols = ["id", "direction", "entry_price", "exit_price", "status", "reason", "entry_time_str", "exit_time"]
        # 倒序显示
        st.dataframe(df_history[display_cols].iloc[::-1], use_container_width=True)
    else:
        st.info("暂无历史交易")

with tab3:
    log_text = "\n".join(bot.logs)
    st.text_area("日志", log_text, height=300, disabled=True)
