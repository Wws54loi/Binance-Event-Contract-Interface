import json
import os
import math
from datetime import datetime

class BbOptimizer:
    def __init__(self, data_file='ETHUSDT_1m_klines.json'):
        self.data_file = data_file
        self.klines = []
        
    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r', encoding='utf-8') as f:
                self.klines = json.load(f)
            print(f"✅ 成功加载 {len(self.klines)} 条K线数据")
            return True
        return False

    def calculate_rsi(self, period=14):
        # 固定 RSI 14
        closes = [k['close'] for k in self.klines]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        rsi_values = [None] * len(self.klines)
        
        avg_gain = 0
        avg_loss = 0
        
        for i in range(period):
            if deltas[i] > 0: avg_gain += deltas[i]
            else: avg_loss -= deltas[i]
        avg_gain /= period
        avg_loss /= period
        
        if avg_loss == 0:
            rsi_values[period] = 100
        else:
            rs = avg_gain / avg_loss
            rsi_values[period] = 100 - (100 / (1 + rs))
        
        for i in range(period + 1, len(self.klines)):
            delta = deltas[i-1]
            gain = delta if delta > 0 else 0
            loss = -delta if delta < 0 else 0
            
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            
            if avg_loss == 0:
                rsi_values[i] = 100
            else:
                rs = avg_gain / avg_loss
                rsi_values[i] = 100 - (100 / (1 + rs))
                
        return rsi_values

    def calculate_bb(self, period_bb, std_dev):
        closes = [k['close'] for k in self.klines]
        
        bb_uppers = [None] * len(self.klines)
        bb_lowers = [None] * len(self.klines)
        
        for i in range(period_bb - 1, len(self.klines)):
            slice_data = closes[i-period_bb+1 : i+1]
            ma = sum(slice_data) / period_bb
            variance = sum([(x - ma) ** 2 for x in slice_data]) / period_bb
            std = math.sqrt(variance)
            
            bb_uppers[i] = ma + (std * std_dev)
            bb_lowers[i] = ma - (std * std_dev)
            
        return bb_uppers, bb_lowers

    def calculate_avg_amp(self):
        avg_amps = [0] * len(self.klines)
        for i in range(20, len(self.klines)):
            avg_amp = sum([self.klines[j]['high'] - self.klines[j]['low'] for j in range(i-20, i)]) / 20
            avg_amps[i] = avg_amp
        return avg_amps

    def run_test(self, bb_period, bb_std, rsi_values, avg_amps):
        bb_uppers, bb_lowers = self.calculate_bb(bb_period, bb_std)
        
        balance = 1000.0
        bet_size = 10.0
        win_payout = 0.8
        loss_payout = -1.0
        
        total_trades = 0
        wins = 0
        
        # 08:00 - 20:00 Window
        for i in range(100, len(self.klines) - 10):
            curr_k = self.klines[i]
            prev_k = self.klines[i-1]
            
            dt = datetime.strptime(curr_k['datetime'], '%Y-%m-%d %H:%M:%S')
            if not (0 <= dt.hour < 12): # UTC 0-12 = UTC+8 08-20
                continue
                
            if rsi_values[i-1] is None or bb_lowers[i-1] is None:
                continue
                
            prev_amp = prev_k['high'] - prev_k['low']
            avg_amp = avg_amps[i-1]
            if avg_amp > 0 and prev_amp > 3 * avg_amp:
                continue
                
            signal = None
            entry_price = 0
            prev_rsi = rsi_values[i-1]
            
            if prev_rsi < 25:
                if curr_k['low'] <= bb_lowers[i-1]:
                    signal = 'LONG'
                    entry_price = min(curr_k['open'], bb_lowers[i-1])
            
            elif prev_rsi > 75:
                if curr_k['high'] >= bb_uppers[i-1]:
                    signal = 'SHORT'
                    entry_price = max(curr_k['open'], bb_uppers[i-1])
            
            if signal:
                settle_k = self.klines[i+10]
                settle_price = (settle_k['open'] + settle_k['close']) / 2
                
                is_win = False
                if signal == 'LONG': is_win = settle_price > entry_price
                else: is_win = settle_price < entry_price
                
                pnl = bet_size * win_payout if is_win else bet_size * loss_payout
                balance += pnl
                total_trades += 1
                if is_win: wins += 1
                
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        net_profit = balance - 1000.0
        
        return {
            "period": bb_period,
            "std": bb_std,
            "trades": total_trades,
            "win_rate": win_rate,
            "net_profit": net_profit
        }

if __name__ == "__main__":
    optimizer = BbOptimizer()
    if optimizer.load_data():
        print("\n🔍 开始布林带参数优化 (周期 & 标准差)...")
        print(f"固定参数: RSI 14 | 策略窗口: 08:00 - 20:00")
        print("-" * 75)
        print(f"{'BB参数 (周期, Std)':<20} | {'交易单量':<10} | {'胜率':<10} | {'净利润'}")
        print("-" * 75)
        
        # 预计算 RSI 和 ATR
        rsi_values = optimizer.calculate_rsi(14)
        avg_amps = optimizer.calculate_avg_amp()
        
        periods = [18, 20, 22, 24, 26, 30]
        stds = [1.8, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5]
        
        best_result = None
        
        for p in periods:
            for s in stds:
                res = optimizer.run_test(p, s, rsi_values, avg_amps)
                label = f"BB ({p}, {s})"
                print(f"{label:<20} | {res['trades']:<10} | {res['win_rate']:.2f}%    | {res['net_profit']:+.2f} U")
                
                if best_result is None or res['net_profit'] > best_result['net_profit']:
                    best_result = res
                    best_result['label'] = label
                    
        print("-" * 75)
        print(f"🏆 最佳参数: {best_result['label']} (净利润 {best_result['net_profit']:.2f} U)")