import asyncio
import websockets
import json
import time
import math
import os
import requests
from datetime import datetime, timezone

class RealtimeAsianSniper:
    def __init__(self, symbol='ethusdt', log_file='asian_sniper_log.txt'):
        self.symbol = symbol.lower()
        self.log_file = log_file
        self.klines = [] # Stores 1m klines: {time, open, high, low, close, volume}
        self.active_trades = [] # List of {entry_time, type, entry_price, expiry_time}
        self.pending_signal = None # {type, trigger_price} from previous closed candle
        
        # Parameters
        self.period_bb = 20
        self.std_dev = 2
        self.period_rsi = 14
        self.period_atr = 20
        
        print(f"🔥 亚盘狙击手实盘监控启动 ({self.symbol.upper()})")
        print(f"📝 日志文件: {self.log_file}")
        self.log("=== 系统启动 ===")

    def log(self, message):
        """记录日志到文件和控制台"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{timestamp}] {message}"
        print(line)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line + "\n")

    def get_historical_data(self):
        """获取最近100根K线用于初始化指标"""
        print("⏳ 正在获取历史数据初始化指标...")
        url = f"https://api.binance.com/api/v3/klines?symbol={self.symbol.upper()}&interval=1m&limit=100"
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            for k in data:
                # Binance kline: [time, open, high, low, close, vol, close_time, ...]
                self.klines.append({
                    'time': int(k[0]),
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5])
                })
            print(f"✅ 已加载 {len(self.klines)} 根历史K线")
            self.calculate_indicators()
        except Exception as e:
            print(f"❌ 获取历史数据失败: {e}")

    def calculate_indicators(self):
        """计算所有K线的指标 (BB, RSI, AvgAmp)"""
        # 只需要计算最后几根即可，但为了简单，全量计算或优化计算
        # 这里为了代码清晰，全量计算，性能在100根时不是问题
        
        closes = [k['close'] for k in self.klines]
        
        # 1. Bollinger Bands
        for i in range(len(self.klines)):
            if i < self.period_bb - 1:
                continue
            slice_data = closes[i-self.period_bb+1 : i+1]
            ma = sum(slice_data) / self.period_bb
            variance = sum([(x - ma) ** 2 for x in slice_data]) / self.period_bb
            std = math.sqrt(variance)
            self.klines[i]['bb_upper'] = ma + (std * self.std_dev)
            self.klines[i]['bb_lower'] = ma - (std * self.std_dev)
            self.klines[i]['bb_middle'] = ma

        # 2. RSI
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        avg_gain = 0
        avg_loss = 0
        
        # 初始 RSI
        if len(deltas) >= self.period_rsi:
            for i in range(self.period_rsi):
                if deltas[i] > 0: avg_gain += deltas[i]
                else: avg_loss -= deltas[i]
            avg_gain /= self.period_rsi
            avg_loss /= self.period_rsi
            
            # 填充第一个RSI (索引为 period_rsi)
            # klines索引对应 deltas索引+1
            # klines[14] 对应 deltas[0]...deltas[13]
            
            # 平滑计算
            for i in range(self.period_rsi, len(deltas)):
                delta = deltas[i]
                gain = delta if delta > 0 else 0
                loss = -delta if delta < 0 else 0
                
                avg_gain = (avg_gain * (self.period_rsi - 1) + gain) / self.period_rsi
                avg_loss = (avg_loss * (self.period_rsi - 1) + loss) / self.period_rsi
                
                rs = avg_gain / avg_loss if avg_loss != 0 else 0
                rsi = 100 - (100 / (1 + rs))
                self.klines[i+1]['rsi'] = rsi

        # 3. Avg Amp (ATR simplified)
        for i in range(self.period_atr, len(self.klines)):
            amps = [self.klines[j]['high'] - self.klines[j]['low'] for j in range(i-self.period_atr, i)]
            self.klines[i]['avg_amp'] = sum(amps) / self.period_atr

    def check_signal_on_close(self):
        """K线收盘时检查是否有潜在信号"""
        if len(self.klines) < 2: return
        
        prev_k = self.klines[-1] # 刚刚收盘的K线
        
        # 1. 时间检查 (UTC 0-12, 对应 UTC+8 08:00-20:00)
        # 黄金窗口: 亚盘 + 欧盘前半段
        current_hour = datetime.now(timezone.utc).hour
        if not (0 <= current_hour < 12):
            # 仅在小时变更时打印一次，避免刷屏
            if not hasattr(self, '_last_hour_log') or self._last_hour_log != current_hour:
                self.log(f"⏳ 当前时间 (UTC {current_hour}) 不在策略窗口 (UTC 0-12), 暂停信号检测")
                self._last_hour_log = current_hour
            
            self.pending_signal = None
            return

        # 2. 巨型K线过滤
        if 'avg_amp' in prev_k:
            amp = prev_k['high'] - prev_k['low']
            if amp > 3 * prev_k['avg_amp']:
                self.log(f"⚠️ 巨型K线过滤: 振幅 {amp:.2f} > 3 * {prev_k['avg_amp']:.2f}")
                self.pending_signal = None
                return

        # 3. 信号检测
        if 'rsi' not in prev_k or 'bb_upper' not in prev_k:
            return

        rsi = prev_k['rsi']
        
        if rsi < 25:
            self.log(f"👀 发现潜在做多机会: RSI={rsi:.2f} < 25, 等待触碰下轨 {prev_k['bb_lower']:.2f}")
            self.pending_signal = {
                'type': 'LONG',
                'trigger_price': prev_k['bb_lower'],
                'setup_time': prev_k['time']
            }
        elif rsi > 75:
            self.log(f"👀 发现潜在做空机会: RSI={rsi:.2f} > 75, 等待触碰上轨 {prev_k['bb_upper']:.2f}")
            self.pending_signal = {
                'type': 'SHORT',
                'trigger_price': prev_k['bb_upper'],
                'setup_time': prev_k['time']
            }
        else:
            self.pending_signal = None

    def check_entry_on_tick(self, current_price):
        """实时价格检查是否触发入场"""
        if not self.pending_signal:
            return

        signal = self.pending_signal
        triggered = False
        entry_price = 0
        
        if signal['type'] == 'LONG':
            # 价格 <= 下轨
            if current_price <= signal['trigger_price']:
                triggered = True
                # 实际入场价：如果是跳空低开，取开盘价(这里简化为当前价)，否则取下轨
                # 模拟逻辑是 min(open, bb_lower)。
                # 在实时中，如果当前价格已经低于 trigger，就立即成交
                entry_price = current_price 
        
        elif signal['type'] == 'SHORT':
            # 价格 >= 上轨
            if current_price >= signal['trigger_price']:
                triggered = True
                entry_price = current_price

        if triggered:
            self.execute_trade(signal['type'], entry_price)
            self.pending_signal = None # 清除信号，防止重复入场

    def execute_trade(self, trade_type, price):
        """执行交易并记录"""
        # 10分钟后结算
        expiry_time = time.time() + 600 
        
        trade = {
            'id': len(self.active_trades) + 1,
            'type': trade_type,
            'entry_price': price,
            'entry_time': time.time(),
            'expiry_time': expiry_time,
            'status': 'OPEN'
        }
        self.active_trades.append(trade)
        
        icon = "🚀" if trade_type == 'LONG' else "📉"
        self.log(f"{icon} 触发交易! 方向: {trade_type} | 价格: {price:.2f} | 结算时间: {datetime.fromtimestamp(expiry_time).strftime('%H:%M:%S')}")

    def check_exits(self, current_price):
        """检查是否有交易到期"""
        now = time.time()
        for trade in self.active_trades:
            if trade['status'] == 'OPEN' and now >= trade['expiry_time']:
                # 结算
                is_win = False
                if trade['type'] == 'LONG':
                    is_win = current_price > trade['entry_price']
                else:
                    is_win = current_price < trade['entry_price']
                
                trade['status'] = 'CLOSED'
                trade['exit_price'] = current_price
                trade['pnl'] = 'WIN' if is_win else 'LOSS'
                
                result_icon = "🟢 赢" if is_win else "🔴 输"
                self.log(f"🏁 交易结算 #{trade['id']} | {trade['type']} | 入场: {trade['entry_price']:.2f} -> 当前: {current_price:.2f} | {result_icon}")

    async def start(self):
        self.get_historical_data()
        
        url = f"wss://fstream.binance.com/ws/{self.symbol}@kline_1m"
        
        while True: # 自动重连循环
            try:
                print(f"🔗 连接 WebSocket: {url}")
                async with websockets.connect(url) as ws:
                    print("✅ WebSocket 连接成功，等待数据...")
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        
                        if 'k' in data:
                            k = data['k']
                            is_closed = k['x']
                            current_price = float(k['c'])
                            
                            # 1. 实时检查入场和出场
                            self.check_entry_on_tick(current_price)
                            self.check_exits(current_price)
                            
                            # 2. K线收盘处理
                            if is_closed:
                                # 更新K线历史
                                new_kline = {
                                    'time': int(k['t']),
                                    'open': float(k['o']),
                                    'high': float(k['h']),
                                    'low': float(k['l']),
                                    'close': float(k['c']),
                                    'volume': float(k['v'])
                                }
                                self.klines.append(new_kline)
                                if len(self.klines) > 200: # 保持列表不过大
                                    self.klines.pop(0)
                                    
                                # 重新计算指标
                                self.calculate_indicators()
                                
                                # 打印当前状态
                                last_k = self.klines[-1]
                                rsi_str = f"{last_k.get('rsi', 0):.1f}" if 'rsi' in last_k else "N/A"
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] K线收盘: {new_kline['close']} | RSI: {rsi_str}")
                                
                                # 检查新信号
                                self.check_signal_on_close()
                                
            except Exception as e:
                print(f"❌ WebSocket 连接断开或发生错误: {e}")
                print("🔄 5秒后尝试重连...")
                await asyncio.sleep(5)

if __name__ == "__main__":
    # 默认使用 ETHUSDT
    sniper = RealtimeAsianSniper(symbol='ethusdt')
    try:
        asyncio.run(sniper.start())
    except KeyboardInterrupt:
        print("🛑 程序已停止")
