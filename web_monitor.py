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

# === 核心逻辑类 ===

class BoxSession:
    def __init__(self, session_id, levels):
        self.id = session_id
        self.start_time = datetime.now()
        self.end_time = None
        self.levels = levels
        self.active_trades = []
        self.history = []
        self.logs = []
        self.is_active = True
        self.stop_reason = None
        self.last_trade_time = {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0}
    
    def log(self, msg):
        timestamp = datetime.now().strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg)
        self.logs.insert(0, full_msg)
        if len(self.logs) > 200: self.logs.pop()

    def stop(self, reason):
        if not self.is_active: return
        self.is_active = False
        self.stop_reason = reason
        self.end_time = datetime.now()
        self.log(f"🛑 箱体 #{self.id} 停止: {reason}")

class BoxMonitorBot:
    def __init__(self):
        self.running = False
        self.symbol = "ethusdt"
        self.sessions = [] # 存储所有 BoxSession
        self.current_price = 0.0
        self.cooldown_seconds = 60
        self.lock = threading.Lock()

    def start_new_session(self, s_res, w_res, w_sup, s_sup):
        with self.lock:
            # 停止当前活动的 session
            for s in self.sessions:
                if s.is_active:
                    s.stop("新箱体启动，自动停止旧箱体")
            
            levels = {
                "s_res": float(s_res), "w_res": float(w_res),
                "w_sup": float(w_sup), "s_sup": float(s_sup)
            }
            new_id = len(self.sessions) + 1
            new_session = BoxSession(new_id, levels)
            new_session.log(f"🚀 新箱体 #{new_id} 启动 | 参数: {levels}")
            self.sessions.append(new_session)
            
        if not self.running:
            self.start_ws()

    def update_current_session(self, s_res, w_res, w_sup, s_sup):
        with self.lock:
            session = self.get_active_session()
            if session:
                session.levels = {
                    "s_res": float(s_res), "w_res": float(w_res),
                    "w_sup": float(w_sup), "s_sup": float(s_sup)
                }
                session.log(f"✅ 参数更新: {session.levels}")
                return True
            return False

    def stop_current_session(self):
        with self.lock:
            session = self.get_active_session()
            if session:
                session.stop("手动停止")

    def get_active_session(self):
        # 返回最后一个且处于活动状态的 session
        if self.sessions and self.sessions[-1].is_active:
            return self.sessions[-1]
        return None

    def start_ws(self):
        self.running = True
        threading.Thread(target=self._run_ws_loop, daemon=True).start()

    def _run_ws_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_ws())

    async def _connect_ws(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol}@aggTrade"
        try:
            async with websockets.connect(url) as ws:
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
                        print(f"WebSocket Error: {e}")
                        break
        except Exception as e:
            print(f"连接失败: {e}")
        finally:
            self.running = False

    def check_price(self, price):
        with self.lock:
            session = self.get_active_session()
            if not session: return
            
            # 限制只能有一个持仓
            if len(session.active_trades) > 0:
                return

            levels = session.levels
            now = time.time()
            
            # 交易逻辑
            if levels["s_res"] > 0 and price >= levels["s_res"]:
                if now - session.last_trade_time["s_res"] > self.cooldown_seconds:
                    self.execute_trade(session, "SHORT", price, "强压力位", "s_res")
            elif levels["w_res"] > 0 and price >= levels["w_res"]:
                 if price < levels["s_res"] or levels["s_res"] == 0: 
                    if now - session.last_trade_time["w_res"] > self.cooldown_seconds:
                        self.execute_trade(session, "SHORT", price, "弱压力位", "w_res")
            
            if levels["s_sup"] > 0 and price <= levels["s_sup"]:
                if now - session.last_trade_time["s_sup"] > self.cooldown_seconds:
                    self.execute_trade(session, "LONG", price, "强支撑位", "s_sup")
            elif levels["w_sup"] > 0 and price <= levels["w_sup"]:
                if price > levels["s_sup"] or levels["s_sup"] == 0:
                    if now - session.last_trade_time["w_sup"] > self.cooldown_seconds:
                        self.execute_trade(session, "LONG", price, "弱支撑位", "w_sup")

    def execute_trade(self, session, direction, price, reason, level_key):
        session.last_trade_time[level_key] = time.time()
        trade = {
            "id": len(session.history) + len(session.active_trades) + 1,
            "direction": direction,
            "entry_price": price,
            "entry_time": time.time(),
            "expiry_time": time.time() + 600,
            "reason": reason,
            "level_key": level_key,
            "status": "OPEN"
        }
        session.active_trades.append(trade)
        session.log(f"🚀 触发交易! {direction} @ {price} | {reason}")

    def check_trades(self, current_price):
        with self.lock:
            # 检查所有 session 的持仓 (即使 session 已停止，持仓仍需结算)
            for session in self.sessions:
                trades_to_check = session.active_trades[:]
                for trade in trades_to_check:
                    if time.time() >= trade["expiry_time"]:
                        self.settle_trade(session, trade, current_price)

    def settle_trade(self, session, trade, current_price):
        if trade in session.active_trades:
            session.active_trades.remove(trade)
        
        is_win = (trade["direction"] == "LONG" and current_price > trade["entry_price"]) or \
                 (trade["direction"] == "SHORT" and current_price < trade["entry_price"])
        
        trade["status"] = "WIN" if is_win else "LOSS"
        trade["exit_price"] = current_price
        trade["exit_time"] = datetime.now().strftime('%H:%M:%S')
        trade["entry_time_str"] = datetime.fromtimestamp(trade["entry_time"]).strftime('%H:%M:%S')
        
        session.history.append(trade)
        
        res_str = "✅ 赢" if is_win else "❌ 输"
        session.log(f"🏁 结算 #{trade['id']}: {res_str} ({trade['entry_price']} -> {current_price})")
        
        if not is_win and session.is_active:
            if trade["level_key"] == "s_res":
                session.stop("强压力位突破")
            elif trade["level_key"] == "s_sup":
                session.stop("强支撑位跌破")

    def clear_all(self):
        with self.lock:
            self.sessions = []
            self.running = False

# === Streamlit 界面逻辑 ===

@st.cache_resource
def get_bot():
    return BoxMonitorBot()

bot = get_bot()

# 侧边栏
with st.sidebar:
    st.header("⚙️ 箱体控制")
    
    # 输入框 (始终可用，用于启动新箱体或更新)
    # 获取当前活动 session 的参数作为默认值
    active_session = bot.get_active_session()
    defaults = active_session.levels if active_session else {"s_res": 0.0, "w_res": 0.0, "w_sup": 0.0, "s_sup": 0.0}
    
    s_res = st.number_input("强压力位 (做空)", value=defaults["s_res"], format="%.2f")
    w_res = st.number_input("弱压力位 (做空)", value=defaults["w_res"], format="%.2f")
    w_sup = st.number_input("弱支撑位 (做多)", value=defaults["w_sup"], format="%.2f")
    s_sup = st.number_input("强支撑位 (做多)", value=defaults["s_sup"], format="%.2f")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 启动新箱体", type="primary", use_container_width=True):
            bot.start_new_session(s_res, w_res, w_sup, s_sup)
            st.rerun()
    with col2:
        if st.button("🔄 更新参数", disabled=(active_session is None), use_container_width=True):
            if bot.update_current_session(s_res, w_res, w_sup, s_sup):
                st.success("已更新")
            else:
                st.error("无活动箱体")
    
    if st.button("🛑 停止当前箱体", disabled=(active_session is None), use_container_width=True):
        bot.stop_current_session()
        st.rerun()

    st.markdown("---")
    if st.button("🗑️ 清空所有数据", type="secondary", use_container_width=True):
        bot.clear_all()
        st.rerun()

# 主界面
st.title("📊 ETHUSDT 箱体震荡实盘监控")

# 顶部状态栏
active_session = bot.get_active_session()
status_color = "green" if active_session else "red"
status_text = f"运行中 (箱体 #{active_session.id})" if active_session else "已停止"
st.markdown(f"### 状态: :{status_color}[{status_text}] | 当前价格: **{bot.current_price:.2f}**")

# 箱体选择器
session_options = {s.id: f"箱体 #{s.id} ({s.start_time.strftime('%H:%M')})" for s in bot.sessions}
selected_session_id = None

if bot.sessions:
    # 默认选择最新的
    selected_session_id = st.selectbox("选择要查看的箱体记录:", 
                                     options=sorted(session_options.keys(), reverse=True),
                                     format_func=lambda x: session_options[x])
else:
    st.info("暂无箱体记录，请在左侧启动新箱体。")

# 显示选中箱体的数据
if selected_session_id:
    # 找到对应的 session 对象
    session = next((s for s in bot.sessions if s.id == selected_session_id), None)
    
    if session:
        # 箱体统计
        total = len(session.history)
        wins = len([t for t in session.history if t["status"] == "WIN"])
        rate = (wins / total * 100) if total > 0 else 0.0
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("箱体状态", "🟢 活跃" if session.is_active else "🔴 已结束")
        c2.metric("总交易", total)
        c3.metric("胜率", f"{rate:.1f}%")
        c4.metric("停止原因", session.stop_reason if session.stop_reason else "-")

        # 三个 Tab
        tab1, tab2, tab3 = st.tabs(["📈 当前持仓", "📜 历史记录", "📝 运行日志"])
        
        with tab1:
            if session.active_trades:
                df_active = pd.DataFrame(session.active_trades)
                df_active['time_str'] = df_active['entry_time'].apply(lambda x: datetime.fromtimestamp(x).strftime('%H:%M:%S'))
                
                display_data = []
                for t in session.active_trades:
                    display_data.append({
                        "买入时间": datetime.fromtimestamp(t['entry_time']).strftime('%H:%M:%S'),
                        "买入价格": f"{t['entry_price']:.2f}",
                        "方向": "做多" if t['direction'] == "LONG" else "做空",
                        "状态": "持仓中",
                        "原因": t['reason']
                    })
                st.dataframe(pd.DataFrame(display_data), use_container_width=True, hide_index=True)
            else:
                st.info("当前箱体无持仓")
                
        with tab2:
            if session.history:
                df_hist = pd.DataFrame(session.history)
                # 计算累计胜率
                df_hist['is_win'] = df_hist['status'] == 'WIN'
                df_hist['cumsum_win'] = df_hist['is_win'].cumsum()
                df_hist['row_num'] = range(1, len(df_hist) + 1)
                df_hist['cum_win_rate'] = (df_hist['cumsum_win'] / df_hist['row_num']) * 100
                
                display_hist = []
                for _, row in df_hist.iterrows():
                    fail_reason = "-"
                    if row['status'] == 'LOSS':
                        mapping = {
                            "s_res": "离开强压力位", "w_res": "离开弱压力位",
                            "s_sup": "离开强支撑位", "w_sup": "离开弱支撑位"
                        }
                        fail_reason = mapping.get(row.get('level_key'), "未知")
                    
                    status_cn = "胜" if row['status'] == 'WIN' else "负"
                    
                    display_hist.append({
                        "买入时间": row.get('entry_time_str', '-'),
                        "买入价格": f"{row['entry_price']:.2f}",
                        "状态": status_cn,
                        "原因": row['reason'],
                        "平仓价": f"{row['exit_price']:.2f}",
                        "累计胜率": f"{row['cum_win_rate']:.1f}%",
                        "失败原因": fail_reason,
                        "sort_time": row['entry_time']
                    })
                
                df_display = pd.DataFrame(display_hist)
                df_display = df_display.sort_values('sort_time', ascending=False).drop(columns=['sort_time'])
                st.dataframe(df_display, use_container_width=True, hide_index=True)
            else:
                st.info("当前箱体无历史交易")
                
        with tab3:
            log_text = "\n".join(session.logs)
            st.text_area("箱体日志", log_text, height=300, disabled=True)

# 自动刷新
if bot.running:
    time.sleep(2)
    st.rerun()
