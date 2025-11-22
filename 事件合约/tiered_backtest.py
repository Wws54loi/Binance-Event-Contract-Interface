import json
import os
import math
from datetime import datetime

class TieredBacktest:
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
        print("正在计算指标 (BB, RSI, AvgAmp)...")
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

        # 3. Avg Amp (20)
        for i in range(20, len(self.klines)):
            avg_amp = sum([self.klines[j]['high'] - self.klines[j]['low'] for j in range(i-20, i)]) / 20
            self.klines[i]['avg_amp'] = avg_amp

    def run_backtest(self):
        # 统计容器
        stats = {
            '15U': {'wins': 0, 'losses': 0, 'profit': 0},
            '10U': {'wins': 0, 'losses': 0, 'profit': 0},
            'total': {'wins': 0, 'losses': 0, 'profit': 0}
        }
        
        print("\n🚀 开始分级策略回测 (09:00 - 20:00 UTC+8 - Plan B 精简版)...")
        print("配置: 1. 移除5U单  2. 巨型K线优化(>15U才过滤)  3. 仅限黄金时段")
        
        for i in range(100, len(self.klines) - 10):
            curr_k = self.klines[i]
            prev_k = self.klines[i-1]
            
            # --- 时间检查 (09:00 - 20:00 UTC+8 -> UTC 1-12) ---
            dt = datetime.strptime(curr_k['datetime'], '%Y-%m-%d %H:%M:%S')
            if not (1 <= dt.hour < 12):
                continue

            # --- 数据完整性 ---
            if prev_k.get('bb_lower') is None or prev_k.get('rsi') is None:
                continue
                
            # --- 巨型K线检测 (优化版) ---
            is_giant_candle = False
            prev_amp = prev_k['high'] - prev_k['low']
            avg_amp = prev_k.get('avg_amp', 0)
            
            # 只有当波动真的很大(>15U)且倍数高时才标记
            if avg_amp > 0 and prev_amp > 3 * avg_amp and prev_amp > 15.0:
                is_giant_candle = True

            # --- 信号检测与分级 ---
            rsi = prev_k['rsi']
            signal_type = None
            entry_price = 0
            bet_amount = 0
            
            # 逻辑复刻自 realtime_asian_sniper.py (Plan B)
            if rsi < 25: # 阈值收紧
                if not is_giant_candle:
                    signal_type = 'LONG'
                    entry_price = min(curr_k['open'], prev_k['bb_lower'])
                    
                    if rsi < 20:
                        bet_amount = 15
                    else:
                        bet_amount = 10
            
            elif rsi > 75: # 阈值收紧
                if not is_giant_candle:
                    signal_type = 'SHORT'
                    entry_price = max(curr_k['open'], prev_k['bb_upper'])
                    
                    if rsi > 80:
                        bet_amount = 15
                    else:
                        bet_amount = 10
            
            # --- 结算 ---
            if signal_type and bet_amount > 0:
                settle_k = self.klines[i+10]
                settle_price = (settle_k['open'] + settle_k['close']) / 2
                
                is_win = False
                if signal_type == 'LONG':
                    is_win = settle_price > entry_price
                else:
                    is_win = settle_price < entry_price
                
                # 计算盈亏
                pnl = 0
                if is_win:
                    pnl = bet_amount * 0.8
                    stats[f'{bet_amount}U']['wins'] += 1
                    stats['total']['wins'] += 1
                else:
                    pnl = -bet_amount
                    stats[f'{bet_amount}U']['losses'] += 1
                    stats['total']['losses'] += 1
                
                stats[f'{bet_amount}U']['profit'] += pnl
                stats['total']['profit'] += pnl

        # --- 输出结果 ---
        print("\n" + "="*60)
        print(f"{'级别':<10} | {'单量':<8} | {'胜率':<8} | {'净利润':<10} | {'单笔均利'}")
        print("-" * 60)
        
        for tier in ['15U', '10U']:
            s = stats[tier]
            total = s['wins'] + s['losses']
            win_rate = (s['wins'] / total * 100) if total > 0 else 0
            avg_profit = (s['profit'] / total) if total > 0 else 0
            print(f"{tier:<10} | {total:<8} | {win_rate:.2f}%   | {s['profit']:+.2f} U   | {avg_profit:+.2f} U")
            
        print("-" * 60)
        t = stats['total']
        total = t['wins'] + t['losses']
        win_rate = (t['wins'] / total * 100) if total > 0 else 0
        print(f"{'总计':<10} | {total:<8} | {win_rate:.2f}%   | {t['profit']:+.2f} U")
        print("="*60)

if __name__ == "__main__":
    tester = TieredBacktest()
    if tester.load_data():
        tester.calculate_indicators()
        tester.run_backtest()
