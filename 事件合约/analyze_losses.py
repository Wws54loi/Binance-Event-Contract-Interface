import json
import os
import math
from datetime import datetime
import statistics

class LossAnalyzer:
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
        print("正在计算指标 (BB, RSI, ATR, Volume MA)...")
        closes = [k['close'] for k in self.klines]
        volumes = [k['volume'] for k in self.klines]
        
        # 1. Bollinger Bands (20, 2)
        period_bb = 20
        std_dev = 2
        for i in range(len(self.klines)):
            if i < period_bb - 1:
                self.klines[i]['bb_upper'] = None
                self.klines[i]['bb_lower'] = None
                self.klines[i]['bb_width'] = None
                continue
            
            slice_data = closes[i-period_bb+1 : i+1]
            ma = sum(slice_data) / period_bb
            variance = sum([(x - ma) ** 2 for x in slice_data]) / period_bb
            std = math.sqrt(variance)
            
            self.klines[i]['bb_upper'] = ma + (std * std_dev)
            self.klines[i]['bb_lower'] = ma - (std * std_dev)
            self.klines[i]['bb_middle'] = ma
            # BB Width %
            if ma != 0:
                self.klines[i]['bb_width'] = (self.klines[i]['bb_upper'] - self.klines[i]['bb_lower']) / ma * 100
            
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
            
            # Volume MA (20)
            avg_vol = sum(volumes[i-20:i]) / 20
            self.klines[i]['vol_ma'] = avg_vol
            self.klines[i]['vol_ratio'] = self.klines[i]['volume'] / avg_vol if avg_vol > 0 else 0

    def analyze(self):
        # 设定最佳时间窗口: 09:00 - 20:00 (UTC 1 - 12)
        start_hour = 1
        end_hour = 12
        
        print(f"🔍 开始参数优化分析 {start_hour}:00 - {end_hour}:00 (UTC)...")
        
        # 1. 测试巨型K线倍数 (Amp Multiplier)
        print("\n[测试] 巨型K线过滤倍数 (Amp > X * AvgAmp 则过滤):")
        for amp_mult in [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 999]: # 999 = 几乎不过滤
            self.run_simulation(start_hour, end_hour, amp_mult, 25, f"Amp > {amp_mult}x")

        # 2. 测试 RSI 阈值
        print("\n[测试] RSI 阈值 (Long < X, Short > 100-X):")
        for rsi_th in [15, 20, 25, 30]:
            self.run_simulation(start_hour, end_hour, 3.0, rsi_th, f"RSI {rsi_th}/{100-rsi_th}")

        # 3. 组合测试: 高胜率组合
        print("\n[测试] 高胜率组合 (RSI 20/80 + 无巨型K线过滤):")
        self.run_simulation(start_hour, end_hour, 999, 20, "RSI 20/80 + NoFilter")


    def run_simulation(self, start_hour, end_hour, amp_mult, rsi_threshold, label):
        wins = 0
        losses = 0
        
        for i in range(100, len(self.klines) - 10):
            curr_k = self.klines[i]
            prev_k = self.klines[i-1]
            
            # 时间
            dt = datetime.strptime(curr_k['datetime'], '%Y-%m-%d %H:%M:%S')
            if not (start_hour <= dt.hour < end_hour): continue

            # 数据
            if prev_k.get('bb_lower') is None or prev_k.get('rsi') is None: continue
                
            # 巨型K线过滤
            prev_amp = prev_k['high'] - prev_k['low']
            avg_amp = prev_k.get('avg_amp', 0)
            if avg_amp > 0 and prev_amp > amp_mult * avg_amp:
                continue

            # 信号
            signal = None
            entry_price = 0
            
            if prev_k['rsi'] < rsi_threshold:
                if curr_k['low'] <= prev_k['bb_lower']:
                    signal = 'LONG'
                    entry_price = min(curr_k['open'], prev_k['bb_lower'])
            
            elif prev_k['rsi'] > (100 - rsi_threshold):
                if curr_k['high'] >= prev_k['bb_upper']:
                    signal = 'SHORT'
                    entry_price = max(curr_k['open'], prev_k['bb_upper'])
            
            # 结算
            if signal:
                settle_k = self.klines[i+10]
                settle_price = (settle_k['open'] + settle_k['close']) / 2
                
                is_win = False
                if signal == 'LONG': is_win = settle_price > entry_price
                else: is_win = settle_price < entry_price
                
                if is_win: wins += 1
                else: losses += 1

        total = wins + losses
        if total == 0: return
        
        win_rate = wins / total * 100
        profit = (wins * 0.8) - (losses * 1.0)
        
        print(f"{label:<15} | 单量: {total:<5} | 胜率: {win_rate:.2f}% | 利润: {profit:.1f} U")



if __name__ == "__main__":
    analyzer = LossAnalyzer()
    if analyzer.load_data():
        analyzer.calculate_indicators()
        analyzer.analyze()
