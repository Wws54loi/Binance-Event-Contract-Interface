import threading
import asyncio
import websockets
import json
import time
import os
import requests
from datetime import datetime, timedelta, timezone
from 通知模块 import send_ntfy

# === 配置 ===
BJ_TZ = timezone(timedelta(hours=8))

# === 核心逻辑类 ===

class BoxSession:
    # 初始化箱体监控会话
    def __init__(self, session_id, levels, name=None, slippage=1.0):
        self.id = session_id
        self.name = name
        self.slippage = slippage
        self.start_time = datetime.now(timezone.utc).astimezone(BJ_TZ)
        self.end_time = None
        self.levels = levels
        self.active_trades = []
        self.history = []
        self.logs = []
        self.is_active = True
        self.stop_reason = None
        self.last_trade_time = {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0, "mid_res": 0, "mid_sup": 0}
    # 记录日志
    def log(self, msg):
        # 强制使用 UTC 时间转换为北京时间，确保准确
        timestamp = datetime.now(timezone.utc).astimezone(BJ_TZ).strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg)
        self.logs.insert(0, full_msg)
        if len(self.logs) > 200: self.logs.pop()

    def stop(self, reason):
        if not self.is_active: return
        self.is_active = False
        self.stop_reason = reason
        self.end_time = datetime.now(timezone.utc).astimezone(BJ_TZ)
        
        # 1. 发送文本通知
        msg = f"🛑 箱体 #{self.id} 停止: {reason}"
        self.log(msg)
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
            "name": getattr(self, 'name', None),
            "slippage": getattr(self, 'slippage', 1.0),
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
        session.name = data.get("name")
        session.slippage = data.get("slippage", 1.0)
        session.start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
        session.end_time = datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None
        
        # 修复时区问题：确保所有时间都转换为北京时间
        if session.start_time:
            if session.start_time.tzinfo is None:
                session.start_time = session.start_time.replace(tzinfo=BJ_TZ)
            else:
                session.start_time = session.start_time.astimezone(BJ_TZ)
                
        if session.end_time:
            if session.end_time.tzinfo is None:
                session.end_time = session.end_time.replace(tzinfo=BJ_TZ)
            else:
                session.end_time = session.end_time.astimezone(BJ_TZ)

        session.active_trades = data.get("active_trades", [])
        session.history = data.get("history", [])
        session.logs = data.get("logs", [])
        session.is_active = data.get("is_active", False)
        session.stop_reason = data.get("stop_reason")
        session.last_trade_time = data.get("last_trade_time", {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0, "mid_res": 0, "mid_sup": 0})
        return session

class BoxMonitorBot:
    def __init__(self):
        self.running = False
        self.symbol = "ethusdt"
        self.sessions = [] # 存储所有 BoxSession
        self.current_price = 0.0
        self.cooldown_seconds = 60
        self.lock = threading.Lock()
        self.bot_start_time = datetime.now(timezone.utc).astimezone(BJ_TZ) # 记录机器人启动时间
        self.stop_reason = None # 记录机器人停止原因
        self.previous_price = 0.0 # 记录上一次价格，用于判断穿越
        self.last_ws_update = 0 # 记录最后一次 WS 更新时间戳
        self.recent_klines = [] # 存储最近的K线 (o, h, l, c) 用于趋势判断
        self.last_kline_fetch_time = 0 # 上次尝试获取K线的时间

    def start_new_session(self, s_res, w_res, w_sup, s_sup, mid_line=0.0, name=None, slippage=1.0):
        with self.lock:
            # 停止当前活动的 session
            for s in self.sessions:
                if s.is_active:
                    s.stop("新箱体启动，自动停止旧箱体")
            
            levels = {
                "s_res": float(s_res), "w_res": float(w_res),
                "w_sup": float(w_sup), "s_sup": float(s_sup),
                "mid_line": float(mid_line)
            }
            new_id = len(self.sessions) + 1
            new_session = BoxSession(new_id, levels, name, slippage)
            msg = f"🚀 新箱体 #{new_id} ({name if name else '未命名'}) 启动 | 参数: {levels} | 滑点保护: {slippage}"
            new_session.log(msg)
            send_ntfy(msg)
            self.sessions.append(new_session)
            
        if not self.running:
            self.start_ws()
        
        # 启动时尝试获取一次K线数据，确保中介线逻辑可用
        if mid_line > 0:
            threading.Thread(target=self.fetch_initial_klines, daemon=True).start()

    def update_current_session(self, s_res, w_res, w_sup, s_sup, mid_line=0.0, name=None, slippage=1.0):
        with self.lock:
            session = self.get_active_session()
            if session:
                session.levels = {
                    "s_res": float(s_res), "w_res": float(w_res),
                    "w_sup": float(w_sup), "s_sup": float(s_sup),
                    "mid_line": float(mid_line)
                }
                if name is not None:
                    session.name = name
                session.slippage = slippage
                current_name = getattr(session, 'name', '未命名')
                session.log(f"✅ 参数更新: {session.levels}, 名称: {current_name}, 滑点: {session.slippage}")
                
                if len(self.recent_klines) == 0:
                     threading.Thread(target=self.fetch_initial_klines, daemon=True).start()
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

    def log_system(self, msg):
        # 辅助函数：将系统级消息记录到当前活动的 session 日志中
        print(f"[System] {msg}")
        session = self.get_active_session()
        if session:
            session.log(f"🔧 {msg}")

    def fetch_initial_klines(self):
        try:
            self.last_kline_fetch_time = time.time()
            self.log_system("正在初始化K线数据...")
            url = "https://fapi.binance.com/fapi/v1/klines"
            params = {
                "symbol": self.symbol.upper(),
                "interval": "1m",
                "limit": 5 # 获取5条，取前4条完整的
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # data format: [open_time, open, high, low, close, ...]
                # 排除最后一个（当前未完成的K线），取最后4个完整的
                if len(data) > 1:
                    completed = data[:-1]
                    target = completed[-4:]
                    with self.lock:
                        self.recent_klines = [
                            {"o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])}
                            for x in target
                        ]
                    self.log_system(f"K线数据初始化完成: {len(self.recent_klines)}根")
                else:
                    self.log_system(f"K线数据不足: {len(data)}")
            else:
                self.log_system(f"K线初始化失败: HTTP {resp.status_code}")
        except Exception as e:
            self.log_system(f"K线初始化异常: {e}")

    def _run_ws_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 初始获取K线
        self.fetch_initial_klines()

        while self.running:
            try:
                loop.run_until_complete(self._connect_ws())
            except Exception as e:
                self.log_system(f"WS Loop Error: {e}")
            
            if self.running:
                self.log_system("⚠️ 连接断开，3秒后自动重连...")
                time.sleep(3)

    async def _connect_ws(self):
        # 使用组合流同时订阅 aggTrade 和 kline_1m
        url = f"wss://fstream.binance.com/stream?streams={self.symbol}@aggTrade/{self.symbol}@kline_1m"
        try:
            self.log_system(f"正在连接行情服务器 (AggTrade + Kline)...")
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                self.log_system("🟢 WebSocket 连接成功")
                self.previous_price = 0.0 
                
                async for msg in ws:
                    if not self.running:
                        break
                    
                    try:
                        payload = json.loads(msg)
                        stream = payload.get('stream')
                        data = payload.get('data')

                        if 'aggTrade' in stream:
                            price = float(data['p'])
                            
                            if self.previous_price == 0:
                                # 智能初始化: 如果刚启动/重连时价格已经在位置下方，
                                # 尝试使用最近一根K线的收盘价作为"前价"，以捕捉启动期间的穿越
                                if self.recent_klines:
                                    self.previous_price = self.recent_klines[-1]['c']
                                    self.current_price = price
                                    # 不使用 continue，直接进入 check_price 进行判断
                                else:
                                    self.previous_price = price
                                    self.current_price = price
                                    continue

                            self.current_price = price
                            self.last_ws_update = time.time()
                            
                            self.check_price(price)
                            self.check_trades(price)
                            
                            self.previous_price = price
                        
                        elif 'kline' in stream:
                            k = data['k']
                            if k['x']: # K线收盘
                                with self.lock:
                                    self.recent_klines.append({
                                        "o": float(k['o']),
                                        "h": float(k['h']),
                                        "l": float(k['l']),
                                        "c": float(k['c'])
                                    })
                                    if len(self.recent_klines) > 4:
                                        self.recent_klines.pop(0)
                                    else:
                                        self.log_system(f"K线缓存累积中: {len(self.recent_klines)}/4")
                                self.log_system(f"K线收盘更新: O:{k['o']} C:{k['c']}")

                    except Exception as e:
                        print(f"Process Error: {e}")
                        
        except Exception as e:
            self.log_system(f"连接失败: {e}")

    def check_price(self, price):
        with self.lock:
            session = self.get_active_session()
            if not session: return
            
            now = time.time()
            levels = session.levels
            prev = self.previous_price
            
            # === 现有逻辑 ===
            
            # 1. 压力位 (做空)
            if levels["s_res"] > 0 and prev < levels["s_res"] and price >= levels["s_res"]:
                # 持仓限制: 允许0持仓，或1持仓且为弱压力位
                can_trade = False
                if len(session.active_trades) == 0:
                    can_trade = True
                elif len(session.active_trades) == 1 and session.active_trades[0]['level_key'] == 'w_res':
                    can_trade = True
                
                if can_trade:
                    if now - session.last_trade_time["s_res"] > self.cooldown_seconds:
                        slippage = getattr(session, 'slippage', 1.0)
                        if (price - levels["s_res"]) > slippage:
                            session.log(f"⚠️ 忽略交易: 强压力位触发但滑点过大")
                        else:
                            self.execute_trade(session, "SHORT", price, "强压力位", "s_res", prev_price=prev)
            
            elif levels["w_res"] > 0 and prev < levels["w_res"] and price >= levels["w_res"]:
                 if price < levels["s_res"] or levels["s_res"] == 0: 
                    # === 弱压力位优化: 检查前4根K线 ===
                    allow_trade = True
                    if len(self.recent_klines) < 4:
                        session.log(f"⚠️ 弱压力位: K线数据不足4根 ({len(self.recent_klines)})，跳过趋势检查")
                    else:
                        # 检查前4根K线的实柱 (Open, Close) 是否都在 w_res 之下
                        for i, k in enumerate(self.recent_klines[-4:]):
                            body_top = max(k['o'], k['c'])
                            if body_top >= levels["w_res"]:
                                allow_trade = False
                                session.log(f"⚠️ 忽略弱压力位: 前第{4-i}根K线实体顶部 {body_top} >= {levels['w_res']}")
                                break
                    
                    if allow_trade:
                        # 持仓限制: 仅允许0持仓
                        if len(session.active_trades) == 0:
                            if now - session.last_trade_time["w_res"] > self.cooldown_seconds:
                                slippage = getattr(session, 'slippage', 1.0)
                                if (price - levels["w_res"]) > slippage:
                                    session.log(f"⚠️ 忽略交易: 弱压力位触发但滑点过大")
                                else:
                                    self.execute_trade(session, "SHORT", price, "弱压力位", "w_res", prev_price=prev)
            
            # 2. 支撑位 (做多)
            if levels["s_sup"] > 0 and prev > levels["s_sup"] and price <= levels["s_sup"]:
                # 持仓限制: 允许0持仓，或1持仓且为弱支撑位
                can_trade = False
                if len(session.active_trades) == 0:
                    can_trade = True
                elif len(session.active_trades) == 1 and session.active_trades[0]['level_key'] == 'w_sup':
                    can_trade = True

                if can_trade:
                    if now - session.last_trade_time["s_sup"] > self.cooldown_seconds:
                        slippage = getattr(session, 'slippage', 1.0)
                        if (levels["s_sup"] - price) > slippage:
                            session.log(f"⚠️ 忽略交易: 强支撑位触发但滑点过大")
                        else:
                            self.execute_trade(session, "LONG", price, "强支撑位", "s_sup", prev_price=prev)
            
            elif levels["w_sup"] > 0 and prev > levels["w_sup"] and price <= levels["w_sup"]:
                if price > levels["s_sup"] or levels["s_sup"] == 0:
                    # === 弱支撑位优化: 检查前4根K线 ===
                    allow_trade = True
                    if len(self.recent_klines) < 4:
                        session.log(f"⚠️ 弱支撑位: K线数据不足4根 ({len(self.recent_klines)})，跳过趋势检查")
                    else:
                        # 检查前4根K线的实柱 (Open, Close) 是否都在 w_sup 之上
                        for i, k in enumerate(self.recent_klines[-4:]):
                            body_bottom = min(k['o'], k['c'])
                            if body_bottom <= levels["w_sup"]:
                                allow_trade = False
                                session.log(f"⚠️ 忽略弱支撑位: 前第{4-i}根K线实体底部 {body_bottom} <= {levels['w_sup']}")
                                break

                    if allow_trade:
                        # 持仓限制: 仅允许0持仓
                        if len(session.active_trades) == 0:
                            if now - session.last_trade_time["w_sup"] > self.cooldown_seconds:
                                slippage = getattr(session, 'slippage', 1.0)
                                if (levels["w_sup"] - price) > slippage:
                                    session.log(f"⚠️ 忽略交易: 弱支撑位触发但滑点过大")
                                else:
                                    self.execute_trade(session, "LONG", price, "弱支撑位", "w_sup", prev_price=prev)

            # === 中介线逻辑 ===
            mid = levels.get("mid_line", 0.0)
            if mid > 0 and self.recent_klines:
                last_kline = self.recent_klines[-1]
                # 判断前1根K线实体的位置
                # 实体在下箱体: Open < mid 且 Close < mid
                is_lower_box = last_kline['o'] < mid and last_kline['c'] < mid
                # 实体在上箱体: Open > mid 且 Close > mid
                is_upper_box = last_kline['o'] > mid and last_kline['c'] > mid
                
                slippage = getattr(session, 'slippage', 1.0)

                if is_lower_box:
                    # 在下箱体 -> 中介线是压力位 -> 向上触碰做空
                    # 触发条件: 价格从下往上穿越或触碰
                    if prev < mid and price >= mid:
                        # 持仓限制: 仅允许0持仓
                        if len(session.active_trades) == 0:
                            if now - session.last_trade_time.get("mid_res", 0) > self.cooldown_seconds:
                                if (price - mid) > slippage:
                                    session.log(f"⚠️ 忽略交易: 中介线压力触发但滑点过大")
                                else:
                                    self.execute_trade(session, "SHORT", price, "中介线压力(下箱体)", "mid_res", prev_price=prev)
                
                elif is_upper_box:
                    # 在上箱体 -> 中介线是支撑位 -> 向下触碰做多
                    # 触发条件: 价格从上往下穿越或触碰
                    if prev > mid and price <= mid:
                        # 持仓限制: 仅允许0持仓
                        if len(session.active_trades) == 0:
                            if now - session.last_trade_time.get("mid_sup", 0) > self.cooldown_seconds:
                                if (mid - price) > slippage:
                                    session.log(f"⚠️ 忽略交易: 中介线支撑触发但滑点过大")
                                else:
                                    self.execute_trade(session, "LONG", price, "中介线支撑(上箱体)", "mid_sup", prev_price=prev)
                    
                    # else: 含糊不清，不做处理

    def execute_trade(self, session, direction, price, reason, level_key, prev_price=0.0):
        session.last_trade_time[level_key] = time.time()
        trade = {
            "id": len(session.history) + len(session.active_trades) + 1,
            "direction": direction,
            "entry_price": price,
            "entry_time": time.time(),
            "expiry_time": time.time() + 600,
            "reason": reason,
            "level_key": level_key,
            "status": "OPEN",
            "prev_price": prev_price # 记录触发时的前一笔价格，方便排查
        }
        session.active_trades.append(trade)
        msg = f"🚀 触发交易! {direction} @ {price} | {reason} (前价: {prev_price})"
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
        trade["exit_time"] = datetime.now(BJ_TZ).strftime('%H:%M:%S')
        trade["entry_time_str"] = datetime.fromtimestamp(trade["entry_time"], BJ_TZ).strftime('%H:%M:%S')
        
        session.history.append(trade)
        
        res_str = "✅ 赢" if is_win else "❌ 输"
        msg = f"🏁 结算 #{trade['id']}: {res_str} ({trade['entry_price']} -> {current_price})"
        session.log(msg)
        send_ntfy(msg)
        
        if not is_win and session.is_active:
            # 止损逻辑：如果是强压力/支撑位失败，则停止
            if trade["level_key"] == "s_res":
                session.stop("强压力位突破")
            elif trade["level_key"] == "s_sup":
                session.stop("强支撑位跌破")

    def clear_all(self):
        with self.lock:
            self.sessions = []
            self.running = False
