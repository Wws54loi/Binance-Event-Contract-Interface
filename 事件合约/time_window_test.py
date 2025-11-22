import json
import os
import math
from datetime import datetime

class TimeWindowTester:
    def __init__(self, data_file='ETHUSDT_1m_klines.json'):
        self.data_file = data_file
        self.klines = []
        
    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r', encoding='utf-8') as f:
                self.klines = json.load(f)
            print(f"✅ 成功加载 {len(self.klines)} 条K线数据")
            return True
        else:
            print(f"❌ 数据文件 {self.data_file} 不存在")
            return False

    def calculate_indicators(self):
        print("正在计算指标 (BB, RSI, ATR)...")
        closes = [k['close'] for k in self.klines]
        
        # 1. Bollinger Bands (20, 2)
        period_bb = 20
        std_dev = 2
        for i in range(len(self.klines)):
            if i < period_bb - 1:
                self.klines[i]['bb_upper'] = None
                self.klines[i]['bb_lower'] = None
                continue
            
            slice_data = closes[i-period_bb+1 : i+1]
            ma = sum(slice_data) / period_bb
            variance = sum([(x - ma) ** 2 for x in slice_data]) / period_bb
            std = math.sqrt(variance)
            
            self.klines[i]['bb_upper'] = ma + (std * std_dev)
            self.klines[i]['bb_lower'] = ma - (std * std_dev)
            
        # 2. RSI (14)
        period_rsi = 14
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        avg_gain = 0
        avg_loss = 0
        
        # 初始 RSI
        for i in range(period_rsi):
            if deltas[i] > 0: avg_gain += deltas[i]
            else: avg_loss -= deltas[i]
        avg_gain /= period_rsi
        avg_loss /= period_rsi
        
        self.klines[period_rsi]['rsi'] = 100 - (100 / (1 + avg_gain/avg_loss)) if avg_loss != 0 else 100
        
        # 平滑 RSI
        for i in range(period_rsi + 1, len(self.klines)):
            delta = deltas[i-1]
            gain = delta if delta > 0 else 0
            loss = -delta if delta < 0 else 0
            
            avg_gain = (avg_gain * (period_rsi - 1) + gain) / period_rsi
            avg_loss = (avg_loss * (period_rsi - 1) + loss) / period_rsi
            
            rs = avg_gain / avg_loss if avg_loss != 0 else 0
            self.klines[i]['rsi'] = 100 - (100 / (1 + rs))

        # 3. ATR / 振幅均值 (用于过滤巨型K线)
        for i in range(20, len(self.klines)):
            avg_amp = sum([self.klines[j]['high'] - self.klines[j]['low'] for j in range(i-20, i)]) / 20
            self.klines[i]['avg_amp'] = avg_amp

    def run_backtest(self, start_hour, end_hour, label):
        """
        运行回测
        start_hour: 开始小时 (包含)
        end_hour: 结束小时 (不包含)
        """
        balance = 1000.0
        bet_size = 10.0
        win_payout = 0.8
        loss_payout = -1.0
        
        total_trades = 0
        wins = 0
        
        # 遍历数据
        for i in range(100, len(self.klines) - 10):
            curr_k = self.klines[i]
            prev_k = self.klines[i-1]
            
            # --- 时间检查 ---
            dt = datetime.strptime(curr_k['datetime'], '%Y-%m-%d %H:%M:%S')
            
            # 处理跨午夜的情况 (例如 22点 到 2点)
            in_time_window = False
            if start_hour < end_hour:
                if start_hour <= dt.hour < end_hour:
                    in_time_window = True
            else: # 跨午夜
                if dt.hour >= start_hour or dt.hour < end_hour:
                    in_time_window = True
            
            if not in_time_window:
                continue

            # --- 数据完整性 ---
            if prev_k.get('bb_lower') is None or prev_k.get('rsi') is None:
                continue
                
            # --- 巨型K线过滤 ---
            prev_amp = prev_k['high'] - prev_k['low']
            avg_amp = prev_k.get('avg_amp', 0)
            if avg_amp > 0 and prev_amp > 3 * avg_amp:
                continue

            # --- 信号检测 ---
            signal = None
            entry_price = 0
            
            if prev_k['rsi'] < 25:
                if curr_k['low'] <= prev_k['bb_lower']:
                    signal = 'LONG'
                    entry_price = min(curr_k['open'], prev_k['bb_lower'])
            
            elif prev_k['rsi'] > 75:
                if curr_k['high'] >= prev_k['bb_upper']:
                    signal = 'SHORT'
                    entry_price = max(curr_k['open'], prev_k['bb_upper'])
            
            # --- 结算 ---
            if signal:
                settle_k = self.klines[i+10]
                
                # [修正] 使用 (Open + Close) / 2 作为结算价，模拟随机秒数入场/出场
                # 这样比单纯用 Open (9.5分钟) 或 Close (10.5分钟) 更接近 10分钟均值
                settle_price = (settle_k['open'] + settle_k['close']) / 2
                
                # [压力测试] 模拟 0.01% 的滑点 (入场价变差)
                # slippage = entry_price * 0.0001
                # if signal == 'LONG': entry_price += slippage
                # else: entry_price -= slippage
                
                is_win = False
                if signal == 'LONG':
                    is_win = settle_price > entry_price
                else:
                    is_win = settle_price < entry_price
                
                pnl = bet_size * win_payout if is_win else bet_size * loss_payout
                balance += pnl
                total_trades += 1
                if is_win: wins += 1

        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        net_profit = balance - 1000.0
        
        return {
            "label": label,
            "trades": total_trades,
            "win_rate": win_rate,
            "net_profit": net_profit
        }

if __name__ == "__main__":
    tester = TimeWindowTester()
    if tester.load_data():
        tester.calculate_indicators()
        
        results = []
        
        print("\n📊 开始多时段对比回测 (固定10U下注)...")
        
        # 1. 纯亚盘 (08:00 - 16:00 UTC+8 -> UTC 0-8)
        results.append(tester.run_backtest(0, 8, "亚盘 (08:00-16:00)"))
        
        # 2. 亚盘+欧早 (08:00 - 20:00 UTC+8 -> UTC 0-12)
        results.append(tester.run_backtest(0, 12, "亚盘+欧早 (08:00-20:00)"))

        # 3. 延迟启动 A (09:00 - 20:00 UTC+8 -> UTC 1-12)
        results.append(tester.run_backtest(1, 12, "延迟启动A (09:00-20:00)"))

        # 4. 延迟启动 B (10:00 - 20:00 UTC+8 -> UTC 2-12)
        results.append(tester.run_backtest(2, 12, "延迟启动B (10:00-20:00)"))

        # 5. 延迟启动 C (11:00 - 20:00 UTC+8 -> UTC 3-12)
        results.append(tester.run_backtest(3, 12, "延迟启动C (11:00-20:00)"))

        # 6. 早盘毒药 (08:00 - 10:00 UTC+8 -> UTC 0-2)
        results.append(tester.run_backtest(0, 2, "早盘毒药 (08:00-10:00)"))

        print("\n" + "="*65)
        print(f"{'时段名称':<20} | {'交易单量':<8} | {'胜率':<8} | {'净利润 (10U/单)'}")
        print("-" * 65)
        
        for res in results:
            print(f"{res['label']:<20} | {res['trades']:<12} | {res['win_rate']:.2f}%   | {res['net_profit']:+.2f} U")
        print("="*65)
