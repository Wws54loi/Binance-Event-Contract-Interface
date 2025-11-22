import asyncio
import websockets
import json
import time
import math
import os
import requests
from datetime import datetime, timezone, timedelta

class RealtimeAsianSniper:
    def __init__(self, symbol='ethusdt', log_file='asian_sniper_log.txt'):
        self.symbol = symbol.lower()
        self.log_file = log_file
        self.state_file = 'asian_sniper_state.json' # 状态保存文件
        self.klines = [] # Stores 1m klines: {time, open, high, low, close, volume}
        self.active_trades = [] # List of {entry_time, type, entry_price, expiry_time}
        self.pending_signal = None # {type, trigger_price} from previous closed candle
        
        # Parameters
        self.period_bb = 20
        self.std_dev = 2
        self.period_rsi = 14
        self.period_atr = 20
        
        # Risk Management
        self.daily_stop_loss = -45.0  # 每日止损阈值
        self.max_active_trades = 5    # 最大同时持仓数
        self.daily_pnl = 0.0          # 当日累计盈亏
        self.last_reset_date = None   # 上次重置日期
        self.is_trading_stopped = False # 是否触发止损停止交易
        
        # Dynamic RSI Thresholds
        self.volatility_p25 = 0.0     # 波动率25分位值 (用于判断死鱼盘)
        
        self.load_state() # 启动时恢复状态
        
        print(f"🔥 亚盘狙击手实盘监控启动 ({self.symbol.upper()})")
        print(f"📝 日志文件: {self.log_file}")
        print(f"🛡️ 每日止损: {self.daily_stop_loss}U | 最大持仓: {self.max_active_trades}单 | 交易时段: 09:00-20:00")
        self.log("=== 系统启动 ===")

    def load_state(self):
        """从文件恢复状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.daily_pnl = state.get('daily_pnl', 0.0)
                    self.last_reset_date = state.get('last_reset_date', None)
                    self.is_trading_stopped = state.get('is_trading_stopped', False)
                    print(f"🔄 已恢复历史状态 | 日期: {self.last_reset_date} | 盈亏: {self.daily_pnl:.2f}U | 止损: {self.is_trading_stopped}")
            except Exception as e:
                print(f"⚠️ 读取状态文件失败: {e}")

    def save_state(self):
        """保存状态到文件"""
        state = {
            'daily_pnl': self.daily_pnl,
            'last_reset_date': self.last_reset_date,
            'is_trading_stopped': self.is_trading_stopped
        }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            print(f"⚠️ 保存状态失败: {e}")

    def log(self, message):
        """记录日志到文件和控制台"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{timestamp}] {message}"
        print(line)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line + "\n")

    def get_historical_data(self):
        """获取最近10000根K线用于初始化指标 (修正为合约数据源)"""
        print("⏳ 正在获取历史数据初始化指标 (目标: 10000根)...")
        # 使用合约API (fapi) 以匹配 websocket 数据源，减少RSI误差
        base_url = "https://fapi.binance.com/fapi/v1/klines"
        limit = 1500 # 合约API单次最大限制
        target_count = 10000
        
        all_klines = []
        end_time = None
        
        try:
            while len(all_klines) < target_count:
                params = {
                    'symbol': self.symbol.upper(),
                    'interval': '1m',
                    'limit': limit
                }
                if end_time:
                    params['endTime'] = end_time
                
                resp = requests.get(base_url, params=params, timeout=10)
                data = resp.json()
                
                if not data or len(data) == 0:
                    break
                
                batch_klines = []
                for k in data:
                    batch_klines.append({
                        'time': int(k[0]),
                        'open': float(k[1]),
                        'high': float(k[2]),
                        'low': float(k[3]),
                        'close': float(k[4]),
                        'volume': float(k[5])
                    })
                
                # 新获取的(更旧的)放在前面
                all_klines = batch_klines + all_klines
                
                # 更新下一次请求的截止时间
                end_time = batch_klines[0]['time'] - 1
                
                print(f"已加载 {len(all_klines)} / {target_count} 根K线...")
                time.sleep(0.1)

            self.klines = all_klines
            print(f"✅ 历史数据加载完成，共 {len(self.klines)} 根")
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

        # 4. Update Volatility Thresholds
        self.calculate_volatility_thresholds()

    def calculate_volatility_thresholds(self):
        """计算波动率阈值 (P25)"""
        # 收集所有有效的 avg_amp
        amps = [k['avg_amp'] for k in self.klines if 'avg_amp' in k]
        if len(amps) < 100:
            return
            
        # 计算 P25 (25分位数)
        sorted_amps = sorted(amps)
        index = int(len(sorted_amps) * 0.25)
        self.volatility_p25 = sorted_amps[index]
        # print(f"DEBUG: 当前波动率 P25={self.volatility_p25:.4f}")

    def check_daily_reset(self):
        """检查是否需要重置每日盈亏 (每天09:00重置)"""
        now = datetime.now()
        # 如果当前时间 >= 9点，且上次重置日期不是今天，则重置
        # 或者如果当前时间 < 9点，且上次重置日期是昨天之前，则重置(跨天逻辑较复杂，简化为每天9点重置)
        
        # 简单逻辑：每天 09:00:00 后第一次运行时重置
        # 我们使用一个日期字符串来标记 '交易日'
        # 如果当前时间 < 9点，归属为前一天的交易日
        # 如果当前时间 >= 9点，归属为今天的交易日
        
        current_trading_day = now.strftime('%Y-%m-%d') if now.hour >= 9 else (now - timedelta(days=1)).strftime('%Y-%m-%d')
        
        if self.last_reset_date != current_trading_day:
            self.log(f"🔄 新交易日开始 ({current_trading_day}) | 重置每日盈亏: {self.daily_pnl:.2f} -> 0.00")
            self.daily_pnl = 0.0
            self.is_trading_stopped = False
            self.last_reset_date = current_trading_day
            self.save_state() # 保存重置后的状态

    def check_signal_on_close(self):
        """K线收盘时检查是否有潜在信号"""
        if len(self.klines) < 2: return
        
        # 0. 每日重置检查
        self.check_daily_reset()
        
        # 1. 止损检查
        if self.is_trading_stopped:
            # 仅打印一次或低频打印
            # self.log(f"🛑 今日已触发止损 ({self.daily_pnl:.2f}U)，停止交易直到明日09:00")
            return

        # 1.5 最大持仓检查
        open_trades = [t for t in self.active_trades if t['status'] == 'OPEN']
        if len(open_trades) >= self.max_active_trades:
            self.pending_signal = None
            return

        prev_k = self.klines[-1] # 刚刚收盘的K线
        
        # 2. 时间检查 (仅在 09:00 - 20:00 运行)
        current_hour = datetime.now().hour
        if not (9 <= current_hour < 20):
            # 非交易时段
            return

        # 3. 巨型K线检测 (作为风险标记)
        # 优化: 在死鱼盘(低波动)时，ATR很小，容易误判正常波动为巨型K线
        # 增加绝对阈值: 振幅必须同时大于 3倍均值 AND 大于 15 USDT (约0.5%)
        is_giant_candle = False
        if 'avg_amp' in prev_k:
            amp = prev_k['high'] - prev_k['low']
            avg_amp = prev_k['avg_amp']
            
            # 只有当波动真的很大时才标记
            if amp > 3 * avg_amp and amp > 15.0:
                is_giant_candle = True
                self.log(f"⚠️ 检测到巨型K线: 振幅 {amp:.2f} > 3 * {avg_amp:.2f} (且 > 15U)")
            elif amp > 3 * avg_amp:
                # 虽然倍数大，但绝对值小，忽略
                pass

        # 3. 信号检测与分级 (Dynamic C 策略)
        if 'rsi' not in prev_k or 'bb_upper' not in prev_k or 'avg_amp' not in prev_k:
            return

        rsi = prev_k['rsi']
        current_avg_amp = prev_k['avg_amp']
        
        # 判断市场状态: 死鱼盘 (Quiet) vs 正常盘 (Normal)
        # 如果当前波动率 < 历史P25，则认为是死鱼盘，可以放宽RSI阈值
        is_quiet_market = current_avg_amp < self.volatility_p25
        
        # 动态阈值设置
        if is_quiet_market:
            # 死鱼盘: 放宽阈值 (Aggressive)
            # Long: < 30 (10U), < 25 (15U)
            # Short: > 70 (10U), > 75 (15U)
            long_threshold = 30
            long_strong_threshold = 25
            short_threshold = 70
            short_strong_threshold = 75
            market_status = "Quiet (🐟)"
        else:
            # 正常盘: 严格阈值 (Baseline)
            # Long: < 25 (10U), < 20 (15U)
            # Short: > 75 (10U), > 80 (15U)
            long_threshold = 25
            long_strong_threshold = 20
            short_threshold = 75
            short_strong_threshold = 80
            market_status = "Normal (🌊)"

        signal_type = None
        trigger_price = 0
        bet_amount = 0
        
        if rsi < long_threshold: 
            if not is_giant_candle: # 必须非巨型K线
                signal_type = 'LONG'
                trigger_price = prev_k['bb_lower']
                if rsi < long_strong_threshold:
                    bet_amount = 15
                else:
                    bet_amount = 10
                
        elif rsi > short_threshold: 
            if not is_giant_candle: # 必须非巨型K线
                signal_type = 'SHORT'
                trigger_price = prev_k['bb_upper']
                if rsi > short_strong_threshold:
                    bet_amount = 15
                else:
                    bet_amount = 10
        
        if signal_type and bet_amount > 0:
            self.log(f"👀 发现 {signal_type} 机会 | RSI={rsi:.1f} | 市场: {market_status} ({current_avg_amp:.2f}/{self.volatility_p25:.2f}) | 巨型={is_giant_candle} | 计划投入: {bet_amount}U")
            self.pending_signal = {
                'type': signal_type,
                'trigger_price': trigger_price,
                'setup_time': prev_k['time'],
                'amount': bet_amount

            }
        else:
            self.pending_signal = None

    def check_entry_on_tick(self, current_price):
        """实时价格检查是否触发入场"""
        # 0. 再次检查止损 (防止在等待成交期间触发止损)
        if self.is_trading_stopped:
            self.pending_signal = None
            return

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
            self.execute_trade(signal['type'], entry_price, signal['amount'])
            self.pending_signal = None # 清除信号，防止重复入场

    def execute_trade(self, trade_type, price, amount):
        """执行交易并记录"""
        # 10分钟后结算
        expiry_time = time.time() + 600 
        
        trade = {
            'id': len(self.active_trades) + 1,
            'type': trade_type,
            'entry_price': price,
            'amount': amount,
            'entry_time': time.time(),
            'expiry_time': expiry_time,
            'status': 'OPEN'
        }
        self.active_trades.append(trade)
        
        icon = "🚀" if trade_type == 'LONG' else "📉"
        self.log(f"{icon} 触发交易! 方向: {trade_type} | 价格: {price:.2f} | 金额: {amount}U | 结算时间: {datetime.fromtimestamp(expiry_time).strftime('%H:%M:%S')}")

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
                
                payout = 0
                if is_win:
                    payout = trade['amount'] * 0.8
                    trade['pnl_amount'] = payout
                    trade['pnl'] = 'WIN'
                else:
                    payout = -trade['amount']
                    trade['pnl_amount'] = payout
                    trade['pnl'] = 'LOSS'
                
                result_icon = "🟢 赢" if is_win else "🔴 输"
                
                # 更新每日盈亏
                self.daily_pnl += payout
                self.log(f"🏁 交易结算 #{trade['id']} | {trade['type']} | 投入: {trade['amount']}U | 盈亏: {payout:+.1f}U | {result_icon}")
                self.log(f"💰 今日累计盈亏: {self.daily_pnl:+.2f}U (止损线: {self.daily_stop_loss}U)")
                
                # 检查是否触发止损
                if self.daily_pnl <= self.daily_stop_loss:
                    self.is_trading_stopped = True
                    self.log(f"🛑 警告: 触发每日止损! 今日交易停止。")
                    self.pending_signal = None # 清除所有待处理信号
                
                self.save_state() # 每次盈亏变动后保存状态

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
                                if len(self.klines) > 10000: # 保持列表不过大
                                    self.klines.pop(0)
                                    
                                # 重新计算指标
                                self.calculate_indicators()
                                
                                # 打印当前状态
                                last_k = self.klines[-1]
                                rsi_str = f"{last_k.get('rsi', 0):.1f}" if 'rsi' in last_k else "N/A"
                                
                                # 增加波动率状态显示
                                vol_info = ""
                                if 'avg_amp' in last_k:
                                    is_quiet = last_k['avg_amp'] < self.volatility_p25
                                    state_str = "🐟Quiet" if is_quiet else "🌊Normal"
                                    vol_info = f" | {state_str}({last_k['avg_amp']:.1f}/{self.volatility_p25:.1f})"
                                
                                self.log(f"📊 K线收盘 | Close: {new_kline['close']} | RSI: {rsi_str}{vol_info}")
                                
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
