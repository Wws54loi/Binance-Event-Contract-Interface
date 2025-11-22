import streamlit as st
import threading
import asyncio
import websockets
import json
import time
import os
import requests
from datetime import datetime
import pandas as pd

# === 通知工具 ===
def send_ntfy(msg, file_data=None, filename=None):
    try:
        url = "https://ntfy.sh/bnb"
        if file_data:
            # 发送文件: PUT 请求，Body 是文件内容，Header 指定文件名
            # 注意: ntfy 接收文件时，Body 必须纯粹是文件数据
            # 我们发送两个请求：一个提示消息，一个文件 (或者直接发文件，ntfy 会显示附件)
            headers = {
                "Filename": filename,
            }
            requests.put(url, data=file_data.encode('utf-8'), headers=headers)
        else:
            requests.post(url, data=msg.encode('utf-8'))
    except Exception as e:
        print(f"Ntfy error: {e}")

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
        
        # 1. 发送文本通知
        msg = f"🛑 箱体 #{self.id} 停止: {reason}"
        self.log(msg)
        send_ntfy(msg)
        
        # 2. 自动保存到本地 (服务器端)
        self.save_to_file()
        
        # 3. 自动推送到 ntfy (作为云端自动下载的替代方案)
        try:
            # 准备数据 (使用 safe_to_dict 逻辑的简化版，因为在类内部可以直接调用 to_dict)
            json_str = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
            filename = f"box_{self.id}_{self.start_time.strftime('%Y%m%d_%H%M')}.json"
            send_ntfy(f"📂 数据文件", file_data=json_str, filename=filename)
            self.log(f"📤 数据已推送至 ntfy")
        except Exception as e:
            self.log(f"❌ ntfy 推送失败: {e}")

    def save_to_file(self):
        try:
            folder = "sessions_data"
            if not os.path.exists(folder):
                os.makedirs(folder)
            
            # 文件名格式: box_{id}_{start_time}.json
            time_str = self.start_time.strftime("%Y%m%d_%H%M%S")
            filename = f"{folder}/box_{self.id}_{time_str}.json"
            
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            
            self.log(f"💾 数据已自动保存至 {filename}")
        except Exception as e:
            self.log(f"❌ 自动保存失败: {e}")

    def to_dict(self):
        return {
            "id": self.id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "levels": self.levels,
            "active_trades": self.active_trades,
            "history": self.history,
            "logs": self.logs,
            "is_active": self.is_active,
            "stop_reason": self.stop_reason,
            "last_trade_time": self.last_trade_time
        }

    @staticmethod
    def from_dict(data):
        session = BoxSession(data["id"], data["levels"])
        session.start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
        session.end_time = datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None
        session.active_trades = data.get("active_trades", [])
        session.history = data.get("history", [])
        session.logs = data.get("logs", [])
        session.is_active = data.get("is_active", False)
        session.stop_reason = data.get("stop_reason")
        session.last_trade_time = data.get("last_trade_time", {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0})
        return session

class BoxMonitorBot:
    def __init__(self):
        self.running = False
        self.symbol = "ethusdt"
        self.sessions = [] # 存储所有 BoxSession
        self.current_price = 0.0
        self.cooldown_seconds = 60
        self.lock = threading.Lock()
        self.bot_start_time = datetime.now() # 记录机器人启动时间
        self.stop_reason = None # 记录机器人停止原因
        self.previous_price = 0.0 # 记录上一次价格，用于判断穿越

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
            msg = f"🚀 新箱体 #{new_id} 启动 | 参数: {levels}"
            new_session.log(msg)
            send_ntfy(msg)
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
        if self.running: return
        self.running = True
        threading.Thread(target=self._run_ws_loop, daemon=True).start()

    def _run_ws_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        while self.running:
            try:
                loop.run_until_complete(self._connect_ws())
            except Exception as e:
                print(f"WS Loop Error: {e}")
            
            if self.running:
                print("⚠️ 连接断开，3秒后自动重连...")
                time.sleep(3)

    async def _connect_ws(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol}@aggTrade"
        try:
            print(f"正在连接 {url} ...")
            async with websockets.connect(url) as ws:
                print("🟢 WebSocket 连接成功")
                self.previous_price = 0.0 # 重置上一价格
                
                while self.running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        price = float(data['p'])
                        
                        # 初始化上一价格
                        if self.previous_price == 0:
                            self.previous_price = price
                            self.current_price = price
                            continue

                        self.current_price = price
                        
                        self.check_price(price)
                        self.check_trades(price)
                        
                        # 更新上一价格
                        self.previous_price = price
                        
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"WebSocket Error: {e}")
                        break
        except Exception as e:
            print(f"连接失败: {e}")
            # 不在这里设置 self.running = False，让外层循环重连

    def check_price(self, price):
        with self.lock:
            session = self.get_active_session()
            if not session: return
            
            # 限制只能有一个持仓
            if len(session.active_trades) > 0:
                return

            levels = session.levels
            now = time.time()
            prev = self.previous_price
            
            # 交易逻辑 - 必须是穿越触发 (Cross Over/Under)
            # 注意: >= 和 <= 包含 "刚好碰到" 的情况
            
            # 1. 压力位 (做空): 价格从下往上穿越或触碰 (prev < level <= price)
            if levels["s_res"] > 0 and prev < levels["s_res"] and price >= levels["s_res"]:
                if now - session.last_trade_time["s_res"] > self.cooldown_seconds:
                    self.execute_trade(session, "SHORT", price, "强压力位", "s_res")
            
            elif levels["w_res"] > 0 and prev < levels["w_res"] and price >= levels["w_res"]:
                 if price < levels["s_res"] or levels["s_res"] == 0: 
                    if now - session.last_trade_time["w_res"] > self.cooldown_seconds:
                        self.execute_trade(session, "SHORT", price, "弱压力位", "w_res")
            
            # 2. 支撑位 (做多): 价格从上往下穿越或触碰 (prev > level >= price)
            if levels["s_sup"] > 0 and prev > levels["s_sup"] and price <= levels["s_sup"]:
                if now - session.last_trade_time["s_sup"] > self.cooldown_seconds:
                    self.execute_trade(session, "LONG", price, "强支撑位", "s_sup")
            
            elif levels["w_sup"] > 0 and prev > levels["w_sup"] and price <= levels["w_sup"]:
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
        msg = f"🚀 触发交易! {direction} @ {price} | {reason}"
        session.log(msg)
        send_ntfy(msg)

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
        msg = f"🏁 结算 #{trade['id']}: {res_str} ({trade['entry_price']} -> {current_price})"
        session.log(msg)
        send_ntfy(msg)
        
        if not is_win and session.is_active:
            if trade["level_key"] == "s_res":
                session.stop("强压力位突破")
            elif trade["level_key"] == "s_sup":
                session.stop("强支撑位跌破")

    def clear_all(self):
        with self.lock:
            self.sessions = []
            self.running = False

    def save_to_disk(self, filename="box_data.json"):
        with self.lock:
            # 增加兼容性处理
            data = []
            for s in self.sessions:
                if hasattr(s, 'to_dict'):
                    data.append(s.to_dict())
                else:
                    data.append({
                        "id": s.id,
                        "start_time": s.start_time.isoformat() if s.start_time else None,
                        "end_time": s.end_time.isoformat() if s.end_time else None,
                        "levels": s.levels,
                        "active_trades": s.active_trades,
                        "history": s.history,
                        "logs": s.logs,
                        "is_active": s.is_active,
                        "stop_reason": getattr(s, 'stop_reason', None),
                        "last_trade_time": getattr(s, 'last_trade_time', {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0})
                    })
            try:
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"数据已保存到 {filename}")
                return True
            except Exception as e:
                print(f"保存失败: {e}")
                return False

    def load_from_disk(self, filename="box_data.json"):
        if not os.path.exists(filename):
            return False
        with self.lock:
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.sessions = [BoxSession.from_dict(d) for d in data]
                print(f"从 {filename} 加载了 {len(self.sessions)} 个箱体")
                return True
            except Exception as e:
                print(f"加载失败: {e}")
                return False

# === Streamlit 界面逻辑 ===

@st.cache_resource
def get_session_bot():
    return BoxMonitorBot()

bot = get_session_bot()

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
    st.subheader("💾 数据管理")
    
    # 1. 服务器端保存 (适用于本地运行/VPS)
    st.caption("服务器端操作 (本地/VPS)")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📥 服务器保存", help="保存到运行程序的服务器硬盘", use_container_width=True):
            if bot.save_to_disk():
                st.success("已保存")
            else:
                st.error("保存失败")
    with c2:
        if st.button("📤 服务器加载", help="从运行程序的服务器硬盘加载", use_container_width=True):
            if bot.load_from_disk():
                st.success("加载成功")
                st.rerun()

    # 2. 浏览器端保存 (适用于 Streamlit Cloud 等云端环境)
    st.caption("客户端操作 (下载到您电脑)")
    
    # 准备下载数据 (增加兼容性处理)
    def safe_to_dict(s):
        if hasattr(s, 'to_dict'):
            return s.to_dict()
        else:
            # 兼容旧版本对象
            return {
                "id": s.id,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
                "levels": s.levels,
                "active_trades": s.active_trades,
                "history": s.history,
                "logs": s.logs,
                "is_active": s.is_active,
                "stop_reason": getattr(s, 'stop_reason', None),
                "last_trade_time": getattr(s, 'last_trade_time', {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0})
            }

    json_str = json.dumps([safe_to_dict(s) for s in bot.sessions], ensure_ascii=False, indent=2)
    
    col_dl, col_up = st.columns(2)
    with col_dl:
        st.download_button(
            label="⬇️ 下载备份",
            data=json_str,
            file_name=f"box_data_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            use_container_width=True
        )
    
    with col_up:
        # 上传组件比较特殊，通常不放在按钮里，直接显示
        pass

    uploaded_file = st.file_uploader("上传备份文件恢复", type=["json"], label_visibility="collapsed")
    if uploaded_file is not None:
        try:
            data = json.load(uploaded_file)
            with bot.lock:
                bot.sessions = [BoxSession.from_dict(d) for d in data]
            st.success(f"成功恢复 {len(bot.sessions)} 个箱体记录！")
            # 稍微延迟后刷新，避免立即重置上传组件导致的问题
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.error(f"文件格式错误: {e}")

    st.markdown("---")
    st.markdown("**系统状态:**")
    if bot.running:
        st.success("🟢 正在运行")
        # 计算运行时间 (兼容旧实例)
        try:
            start_time = bot.bot_start_time
        except AttributeError:
            start_time = datetime.now()
            bot.bot_start_time = start_time
            
        uptime = datetime.now() - start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{hours}小时 {minutes}分"
        if days > 0:
            time_str = f"{days}天 {time_str}"
        st.caption(f"已连续运行: {time_str}")
        st.caption("提示: 只要不关闭黑色终端窗口，关闭浏览器网页也会继续运行。")
    else:
        st.error("🔴 已停止")
        if bot.stop_reason:
            st.warning(f"停止原因: {bot.stop_reason}")

    # if st.button("🗑️ 清空所有数据", type="secondary", use_container_width=True):
    #     bot.clear_all()
    #     st.rerun()

# 主界面
st.title("📊 ETHUSDT 箱体震荡实盘监控")

# 顶部状态栏
active_session = bot.get_active_session()
status_color = "green" if active_session else "red"
status_text = f"运行中 (箱体 #{active_session.id})" if active_session else "已停止"
st.markdown(f"### 状态: :{status_color}[{status_text}] | 当前价格: **{bot.current_price:.2f}**")

# 箱体列表展示
if not bot.sessions:
    st.info("暂无箱体记录，请在左侧启动新箱体。")
else:
    # 按时间倒序显示 (最新的在最上面)
    for session in reversed(bot.sessions):
        # 标题格式：日期 时间 (ID)
        start_str = session.start_time.strftime('%Y-%m-%d %H:%M:%S')
        status_icon = "🟢" if session.is_active else "🔴"
        title = f"{status_icon} {start_str} | 箱体 #{session.id}"
        
        # 默认展开正在运行的，或者最新的一个
        is_expanded = session.is_active or (session == bot.sessions[-1])
        
        with st.expander(title, expanded=is_expanded):
            # 箱体统计
            total = len(session.history)
            wins = len([t for t in session.history if t["status"] == "WIN"])
            rate = (wins / total * 100) if total > 0 else 0.0
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("箱体状态", "活跃" if session.is_active else "已结束")
            c2.metric("总交易", total)
            c3.metric("胜率", f"{rate:.1f}%")
            c4.metric("停止原因", session.stop_reason if session.stop_reason else "-")

            # 提供单独下载该箱体数据的按钮
            if not session.is_active:
                session_json = json.dumps(safe_to_dict(session), ensure_ascii=False, indent=2)
                st.download_button(
                    label="⬇️ 下载该箱体记录",
                    data=session_json,
                    file_name=f"box_{session.id}_{session.start_time.strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                    key=f"dl_{session.id}"
                )

            # 两个 Tab：交易记录 (合并) 和 运行日志
            tab_trades, tab_logs = st.tabs(["📜 交易记录", "📝 运行日志"])
            
            with tab_trades:
                all_display_data = []
                current_price = bot.current_price

                # 1. 处理当前持仓 (Active Trades)
                if session.active_trades:
                    for t in session.active_trades:
                        # 计算倒计时
                        remaining = int(t['expiry_time'] - time.time())
                        if remaining < 0: remaining = 0
                        mins, secs = divmod(remaining, 60)
                        countdown_str = f"{mins:02d}:{secs:02d}"
                        
                        # 计算浮动盈亏
                        if t['direction'] == "LONG":
                            pnl = current_price - t['entry_price']
                        else:
                            pnl = t['entry_price'] - current_price
                        
                        # 盈亏状态文字
                        if pnl > 0:
                            pnl_text = "🟢浮盈"
                        elif pnl < 0:
                            pnl_text = "🔴浮亏"
                        else:
                            pnl_text = "⚪持平"
                        
                        status_combined = f"持仓中 ({countdown_str}) ({pnl_text})"
                        
                        all_display_data.append({
                            "买入时间": datetime.fromtimestamp(t['entry_time']).strftime('%H:%M:%S'),
                            "买入价格": f"{t['entry_price']:.2f}",
                            "方向": "做多" if t['direction'] == "LONG" else "做空",
                            "状态": status_combined,
                            "原因": t['reason'],
                            "平仓/当前价": f"{current_price:.2f}",
                            "累计胜率": "-",
                            "失败原因": "-",
                            "sort_time": t['entry_time']
                        })

                # 2. 处理历史记录 (History Trades)
                if session.history:
                    df_hist = pd.DataFrame(session.history)
                    # 计算累计胜率
                    df_hist['is_win'] = df_hist['status'] == 'WIN'
                    df_hist['cumsum_win'] = df_hist['is_win'].cumsum()
                    df_hist['row_num'] = range(1, len(df_hist) + 1)
                    df_hist['cum_win_rate'] = (df_hist['cumsum_win'] / df_hist['row_num']) * 100
                    
                    for _, row in df_hist.iterrows():
                        fail_reason = "-"
                        if row['status'] == 'LOSS':
                            mapping = {
                                "s_res": "离开强压力位", "w_res": "离开弱压力位",
                                "s_sup": "离开强支撑位", "w_sup": "离开弱支撑位"
                            }
                            fail_reason = mapping.get(row.get('level_key'), "未知")
                        
                        status_cn = "✅ 胜" if row['status'] == 'WIN' else "❌ 负"
                        
                        all_display_data.append({
                            "买入时间": row.get('entry_time_str', '-'),
                            "买入价格": f"{row['entry_price']:.2f}",
                            "方向": "做多" if row['direction'] == "LONG" else "做空",
                            "状态": status_cn,
                            "原因": row['reason'],
                            "平仓/当前价": f"{row['exit_price']:.2f}",
                            "累计胜率": f"{row['cum_win_rate']:.1f}%",
                            "失败原因": fail_reason,
                            "sort_time": row['entry_time']
                        })

                if all_display_data:
                    df_display = pd.DataFrame(all_display_data)
                    # 按时间倒序排列 (最新的在最上面)
                    df_display = df_display.sort_values('sort_time', ascending=False).drop(columns=['sort_time'])
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.info("暂无交易记录")
                    
            with tab_logs:
                log_text = "\n".join(session.logs)
                # 使用 unique key 避免冲突
                st.text_area("箱体日志", log_text, height=300, disabled=True, key=f"log_{session.id}")

# 自动刷新
if bot.running:
    time.sleep(2)
    st.rerun()
