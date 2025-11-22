import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import asyncio
import websockets
import json
import time
from datetime import datetime

class BoxMonitorBot:
    def __init__(self):
        self.running = False
        self.symbol = "ethusdt"
        self.levels = {
            "s_res": 0.0, # Strong Resistance (Highest)
            "w_res": 0.0, # Weak Resistance
            "w_sup": 0.0, # Weak Support
            "s_sup": 0.0  # Strong Support (Lowest)
        }
        self.active_trades = []
        self.history = []
        self.loop = None
        self.log_callback = None
        self.price_callback = None
        self.stats_callback = None
        
        # Cooldowns to prevent spamming trades on the same level (in seconds)
        self.last_trade_time = {
            "s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0
        }
        self.cooldown_seconds = 60 

    def set_levels(self, s_res, w_res, w_sup, s_sup):
        try:
            self.levels["s_res"] = float(s_res)
            self.levels["w_res"] = float(w_res)
            self.levels["w_sup"] = float(w_sup)
            self.levels["s_sup"] = float(s_sup)
            self.log(f"✅ 参数更新: 强压{s_res} | 弱压{w_res} | 弱撑{w_sup} | 强撑{s_sup}")
            return True
        except ValueError:
            self.log("❌ 参数错误: 请输入有效的数字")
            return False

    def log(self, msg):
        timestamp = datetime.now().strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg)
        if self.log_callback:
            self.log_callback(full_msg)

    async def connect_ws(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol}@aggTrade"
        self.log(f"正在连接 {url} ...")
        try:
            async with websockets.connect(url) as ws:
                self.log("🟢 监听已启动 (等待价格触碰...)")
                while self.running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        price = float(data['p'])
                        
                        if self.price_callback:
                            self.price_callback(price)
                            
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
        
        # 1. Strong Resistance (Short) - Highest Priority
        if self.levels["s_res"] > 0 and price >= self.levels["s_res"]:
            if now - self.last_trade_time["s_res"] > self.cooldown_seconds:
                self.execute_trade("SHORT", price, "强压力位 (Strong Res)", "s_res")
                
        # 2. Weak Resistance (Short)
        elif self.levels["w_res"] > 0 and price >= self.levels["w_res"]:
             # Only trigger if we are NOT above Strong Res (to avoid double trigger, though cooldown handles it too)
             if price < self.levels["s_res"] or self.levels["s_res"] == 0: 
                if now - self.last_trade_time["w_res"] > self.cooldown_seconds:
                    self.execute_trade("SHORT", price, "弱压力位 (Weak Res)", "w_res")

        # 3. Strong Support (Long) - Highest Priority
        if self.levels["s_sup"] > 0 and price <= self.levels["s_sup"]:
            if now - self.last_trade_time["s_sup"] > self.cooldown_seconds:
                self.execute_trade("LONG", price, "强支撑位 (Strong Sup)", "s_sup")

        # 4. Weak Support (Long)
        elif self.levels["w_sup"] > 0 and price <= self.levels["w_sup"]:
            if price > self.levels["s_sup"] or self.levels["s_sup"] == 0:
                if now - self.last_trade_time["w_sup"] > self.cooldown_seconds:
                    self.execute_trade("LONG", price, "弱支撑位 (Weak Sup)", "w_sup")

    def execute_trade(self, direction, price, reason, level_key):
        self.last_trade_time[level_key] = time.time()
        trade = {
            "id": len(self.history) + len(self.active_trades) + 1,
            "direction": direction,
            "entry_price": price,
            "entry_time": time.time(),
            "expiry_time": time.time() + 600, # 10 mins
            "reason": reason,
            "level_key": level_key,
            "status": "OPEN"
        }
        self.active_trades.append(trade)
        self.log(f"🚀 触发交易! {direction} @ {price} | 原因: {reason}")
        self.update_stats()

    def check_trades(self, current_price):
        # Check exits
        for trade in self.active_trades[:]:
            if time.time() >= trade["expiry_time"]:
                self.settle_trade(trade, current_price)

    def settle_trade(self, trade, current_price):
        self.active_trades.remove(trade)
        
        is_win = False
        if trade["direction"] == "LONG":
            is_win = current_price > trade["entry_price"]
        else:
            is_win = current_price < trade["entry_price"]
            
        trade["status"] = "WIN" if is_win else "LOSS"
        trade["exit_price"] = current_price
        self.history.append(trade)
        
        result_str = "✅ 赢" if is_win else "❌ 输"
        self.log(f"🏁 结算 #{trade['id']} {trade['direction']}: {result_str} (入场: {trade['entry_price']} -> 当前: {current_price})")
        self.update_stats()
        
        # Stop Condition Check
        # "如果箱体突破到我设置的最高价或者最低价，并且买入的那一单输了则判断出箱体了，停止监听"
        if not is_win:
            if trade["level_key"] == "s_res":
                self.log("🛑 强压力位交易失败 (价格突破) -> 判断为箱体突破，停止监听！")
                self.stop()
            elif trade["level_key"] == "s_sup":
                self.log("🛑 强支撑位交易失败 (价格跌破) -> 判断为箱体突破，停止监听！")
                self.stop()

    def update_stats(self):
        if self.stats_callback:
            total = len(self.history)
            wins = len([t for t in self.history if t["status"] == "WIN"])
            rate = (wins / total * 100) if total > 0 else 0
            active = len(self.active_trades)
            self.stats_callback(total, wins, rate, active)

    def start_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.connect_ws())

    def start(self):
        if self.running: return
        self.running = True
        threading.Thread(target=self.start_loop, daemon=True).start()

    def stop(self):
        self.running = False

class MonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ETHUSDT 箱体震荡策略监控")
        self.root.geometry("600x700")
        
        self.bot = BoxMonitorBot()
        self.bot.log_callback = self.append_log
        self.bot.price_callback = self.update_price
        self.bot.stats_callback = self.update_stats
        
        self.create_widgets()
        
    def create_widgets(self):
        # 1. Price Display
        price_frame = ttk.LabelFrame(self.root, text="实时行情 (ETHUSDT)")
        price_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_price = ttk.Label(price_frame, text="等待连接...", font=("Arial", 24, "bold"), foreground="blue")
        self.lbl_price.pack(pady=10)
        
        # 2. Settings (Input Box)
        settings_frame = ttk.LabelFrame(self.root, text="箱体参数设置 (价格)")
        settings_frame.pack(fill="x", padx=10, pady=5)
        
        grid_opts = {'padx': 5, 'pady': 5}
        
        ttk.Label(settings_frame, text="强压力位 (做空):", foreground="red").grid(row=0, column=0, **grid_opts)
        self.ent_s_res = ttk.Entry(settings_frame)
        self.ent_s_res.grid(row=0, column=1, **grid_opts)
        
        ttk.Label(settings_frame, text="弱压力位 (做空):", foreground="orange").grid(row=1, column=0, **grid_opts)
        self.ent_w_res = ttk.Entry(settings_frame)
        self.ent_w_res.grid(row=1, column=1, **grid_opts)
        
        ttk.Label(settings_frame, text="弱支撑位 (做多):", foreground="green").grid(row=2, column=0, **grid_opts)
        self.ent_w_sup = ttk.Entry(settings_frame)
        self.ent_w_sup.grid(row=2, column=1, **grid_opts)
        
        ttk.Label(settings_frame, text="强支撑位 (做多):", foreground="darkgreen").grid(row=3, column=0, **grid_opts)
        self.ent_s_sup = ttk.Entry(settings_frame)
        self.ent_s_sup.grid(row=3, column=1, **grid_opts)
        
        btn_frame = ttk.Frame(settings_frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        
        self.btn_apply = ttk.Button(btn_frame, text="应用参数 & 开始监听", command=self.apply_and_start)
        self.btn_apply.pack(side="left", padx=5)
        
        self.btn_stop = ttk.Button(btn_frame, text="停止监听", command=self.stop_bot, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        # 3. Stats
        stats_frame = ttk.LabelFrame(self.root, text="统计信息")
        stats_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_stats = ttk.Label(stats_frame, text="总交易: 0 | 胜场: 0 | 胜率: 0.0% | 持仓中: 0", font=("Arial", 10))
        self.lbl_stats.pack(pady=5)

        # 4. Logs
        log_frame = ttk.LabelFrame(self.root, text="运行日志")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.txt_log = scrolledtext.ScrolledText(log_frame, height=15, state='disabled')
        self.txt_log.pack(fill="both", expand=True, padx=5, pady=5)

    def apply_and_start(self):
        s_res = self.ent_s_res.get()
        w_res = self.ent_w_res.get()
        w_sup = self.ent_w_sup.get()
        s_sup = self.ent_s_sup.get()
        
        if not all([s_res, w_res, w_sup, s_sup]):
            messagebox.showerror("错误", "请填写所有价格字段")
            return
            
        if self.bot.set_levels(s_res, w_res, w_sup, s_sup):
            if not self.bot.running:
                self.bot.start()
                self.btn_apply.config(text="更新参数")
                self.btn_stop.config(state="normal")
                self.append_log("系统已启动...")

    def stop_bot(self):
        self.bot.stop()
        self.btn_apply.config(text="开始监听")
        self.btn_stop.config(state="disabled")
        self.append_log("系统已停止")

    def append_log(self, msg):
        self.root.after(0, self._append_log_thread_safe, msg)

    def _append_log_thread_safe(self, msg):
        self.txt_log.config(state='normal')
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state='disabled')

    def update_price(self, price):
        self.root.after(0, lambda: self.lbl_price.config(text=f"{price:.2f}"))

    def update_stats(self, total, wins, rate, active):
        text = f"总交易: {total} | 胜场: {wins} | 胜率: {rate:.1f}% | 持仓中: {active}"
        self.root.after(0, lambda: self.lbl_stats.config(text=text))

if __name__ == "__main__":
    root = tk.Tk()
    app = MonitorGUI(root)
    root.mainloop()
