import json
import os
import math
from datetime import datetime

class RsiOptimizer:
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

    def calculate_rsi(self, period):
        closes = [k['close'] for k in self.klines]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        rsi_values = [None] * len(self.klines)
        
        avg_gain = 0
        avg_loss = 0
        
        # 初始 RSI
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
        
        # 平滑 RSI
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

    def calculate_bb(self):
        # 布林带参数固定 (20, 2)
        closes = [k['close'] for k in self.klines]
        period_bb = 20
        std_dev = 2
        
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
        # ATR 简化版
        avg_amps = [0] * len(self.klines)
        for i in range(20, len(self.klines)):
            avg_amp = sum([self.klines[j]['high'] - self.klines[j]['low'] for j in range(i-20, i)]) / 20
            avg_amps[i] = avg_amp
        return avg_amps

    def run_test(self, rsi_period):
        # 预计算指标
        rsi_values = self.calculate_rsi(rsi_period)
        bb_uppers, bb_lowers = self.calculate_bb()
        avg_amps = self.calculate_avg_amp()
        
        balance = 1000.0
        bet_size = 10.0
        win_payout = 0.8
        loss_payout = -1.0
        
        total_trades = 0
        wins = 0
        
        # 遍历数据 (使用 08:00 - 20:00 黄金窗口)
        for i in range(100, len(self.klines) - 10):
            curr_k = self.klines[i]
            prev_k = self.klines[i-1]
            
            # 1. 时间检查 (UTC 0-12)
            dt = datetime.strptime(curr_k['datetime'], '%Y-%m-%d %H:%M:%S')
            if not (0 <= dt.hour < 12):
                continue
                
            # 2. 数据检查
            if rsi_values[i-1] is None or bb_lowers[i-1] is None:
                continue
                
            # 3. 巨型K线过滤
            prev_amp = prev_k['high'] - prev_k['low']
            avg_amp = avg_amps[i-1]
            if avg_amp > 0 and prev_amp > 3 * avg_amp:
                continue
                
            # 4. 信号检测
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
            
            # 5. 结算
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
            "period": rsi_period,
            "trades": total_trades,
            "win_rate": win_rate,
            "net_profit": net_profit
        }

if __name__ == "__main__":
    optimizer = RsiOptimizer()
    if optimizer.load_data():
        print("\n🔍 开始 RSI 周期参数优化 (范围 6-24)...")
        print(f"策略窗口: 08:00 - 20:00 | 结算: 10分钟")
        print("-" * 60)
        print(f"{'RSI周期':<10} | {'交易单量':<10} | {'胜率':<10} | {'净利润'}")
        print("-" * 60)
        
        best_result = None
        
        for p in range(6, 25, 2): # 步长为2
            res = optimizer.run_test(p)
            print(f"RSI {res['period']:<6} | {res['trades']:<10} | {res['win_rate']:.2f}%    | {res['net_profit']:+.2f} U")
            
            if best_result is None or res['net_profit'] > best_result['net_profit']:
                best_result = res
                
        print("-" * 60)
        print(f"🏆 最佳参数: RSI {best_result['period']} (净利润 {best_result['net_profit']:.2f} U)")
