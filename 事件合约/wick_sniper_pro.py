import requests
import json
import os
import time
from datetime import datetime, timedelta
import math

class BinanceDataFetcher:
    def __init__(self, symbol='ETHUSDT', interval='1m', days=100):
        self.symbol = symbol
        self.interval = interval
        self.days = days
        self.filename = f'{symbol}_{interval}_klines.json'
        self.base_url = 'https://fapi.binance.com/fapi/v1/klines'

    def fetch(self):
        end_time = int(time.time() * 1000)
        start_time = int((datetime.now() - timedelta(days=self.days)).timestamp() * 1000)
        
        all_klines = []
        current_end = end_time
        
        print(f"开始获取 {self.days} 天的 {self.symbol} {self.interval} K线数据 (合约)...")
        
        while True:
            params = {
                'symbol': self.symbol,
                'interval': self.interval,
                'limit': 1500,
                'endTime': current_end
            }
            
            try:
                resp = requests.get(self.base_url, params=params)
                if resp.status_code != 200:
                    print(f"API Error: {resp.text}")
                    break
                    
                data = resp.json()
                if not data: 
                    break
                
                all_klines.extend(data)
                
                first_time = data[0][0]
                current_end = first_time - 1
                
                print(f"\r已收集 {len(all_klines)} 条K线...", end="")
                
                if first_time <= start_time:
                    break
                    
                time.sleep(0.05) # 避免频率限制
                
            except Exception as e:
                print(f"Error: {e}")
                break
                
        # 去重并排序
        kline_dict = {k[0]: k for k in all_klines}
        sorted_times = sorted(kline_dict.keys())
        final_klines = [kline_dict[t] for t in sorted_times if t >= start_time]
        
        print(f"\n整理完成，共 {len(final_klines)} 条数据")
        
        # 格式化
        formatted = []
        for k in final_klines:
            formatted.append({
                'datetime': datetime.fromtimestamp(k[0]/1000).strftime('%Y-%m-%d %H:%M:%S'),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5])
            })
            
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(formatted, f)
        print(f"数据已保存至 {self.filename}")
        return formatted

class WickSniperStrategyPro:
    def __init__(self, data_file='ETHUSDT_1m_klines.json'):
        self.data_file = data_file
        self.klines_1m = []
        self.klines_10m = []
        
    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r', encoding='utf-8') as f:
                self.klines_1m = json.load(f)
            print(f"加载了 {len(self.klines_1m)} 条1分钟K线")
            return True
        return False

    def resample_to_10m(self):
        self.klines_10m = []
        current_10m = None
        
        for k in self.klines_1m:
            dt = datetime.strptime(k['datetime'], '%Y-%m-%d %H:%M:%S')
            minute_floor = (dt.minute // 10) * 10
            k_10m_time = dt.replace(minute=minute_floor, second=0)
            k_10m_time_str = k_10m_time.strftime('%Y-%m-%d %H:%M:%S')
            
            if current_10m is None or current_10m['datetime'] != k_10m_time_str:
                if current_10m:
                    self.klines_10m.append(current_10m)
                
                current_10m = {
                    'datetime': k_10m_time_str,
                    'open': k['open'],
                    'high': k['high'],
                    'low': k['low'],
                    'close': k['close'],
                    'volume': k['volume'],
                    'count': 1
                }
            else:
                current_10m['high'] = max(current_10m['high'], k['high'])
                current_10m['low'] = min(current_10m['low'], k['low'])
                current_10m['close'] = k['close']
                current_10m['volume'] += k['volume']
                current_10m['count'] += 1
        
        if current_10m:
            self.klines_10m.append(current_10m)
        print(f"重采样生成 {len(self.klines_10m)} 条10分钟K线")

    def calculate_bollinger_bands(self, period=20, std_dev=2):
        """
        计算布林带
        注意：这里的BB[i]是基于close[i]计算的。
        在回测时，必须使用BB[i-1]来作为第i根K线的入场参考，以避免未来函数。
        """
        closes = [k['close'] for k in self.klines_1m]
        
        for i in range(len(self.klines_1m)):
            if i < period - 1:
                self.klines_1m[i]['bb_upper'] = None
                self.klines_1m[i]['bb_middle'] = None
                self.klines_1m[i]['bb_lower'] = None
                continue
            
            # 使用包含当前K线在内的过去period根K线计算
            # 这样计算出的BB值代表"K线收盘时的BB值"
            slice_closes = closes[i - period + 1 : i + 1]
            ma = sum(slice_closes) / period
            variance = sum([((x - ma) ** 2) for x in slice_closes]) / period
            std = math.sqrt(variance)
            
            self.klines_1m[i]['bb_middle'] = ma
            self.klines_1m[i]['bb_upper'] = ma + (std * std_dev)
            self.klines_1m[i]['bb_lower'] = ma - (std * std_dev)

    def calculate_rsi(self, period=14):
        closes = [float(k['close']) for k in self.klines_1m]
        if len(closes) < period + 1: return

        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        avg_gain = 0
        avg_loss = 0
        
        for i in range(period):
            if deltas[i] > 0: avg_gain += deltas[i]
            else: avg_loss -= deltas[i]
            
        avg_gain /= period
        avg_loss /= period
        
        rsis = [None] * len(closes)
        
        if avg_loss == 0: rsis[period] = 100
        else: rsis[period] = 100 - (100 / (1 + avg_gain/avg_loss))
        
        for i in range(period + 1, len(closes)):
            delta = deltas[i-1]
            gain = delta if delta > 0 else 0
            loss = -delta if delta < 0 else 0
            
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            
            if avg_loss == 0: rsis[i] = 100
            else: rsis[i] = 100 - (100 / (1 + avg_gain/avg_loss))
            
        for i, rsi in enumerate(rsis):
            self.klines_1m[i]['rsi'] = rsi

    def calculate_ma(self, period=100):
        closes = [float(k['close']) for k in self.klines_1m]
        for i in range(len(closes)):
            if i < period - 1:
                self.klines_1m[i]['ma'] = None
                continue
            self.klines_1m[i]['ma'] = sum(closes[i-period+1:i+1]) / period

    def calculate_ema(self, period=100):
        closes = [float(k['close']) for k in self.klines_1m]
        ema = [None] * len(closes)
        
        # 简单移动平均作为初始值
        if len(closes) > period:
            sma = sum(closes[:period]) / period
            ema[period-1] = sma
            
            multiplier = 2 / (period + 1)
            
            for i in range(period, len(closes)):
                ema[i] = (closes[i] - ema[i-1]) * multiplier + ema[i-1]
                
        for i, val in enumerate(ema):
            self.klines_1m[i][f'ema_{period}'] = val

    def backtest_complex(self, strategy_name, rsi_limits=(20, 80), time_ranges=None, bb_confirm=False, avoid_news=False):
        """
        复杂组合策略回测
        :param time_ranges: list of tuple, e.g. [(0, 8), (20, 24)] 代表只在 UTC 0-8点 和 20-24点交易
        :param bb_confirm: 是否要求同时触碰布林带
        :param avoid_news: 是否避开巨型K线
        """
        trades = []
        rsi_low, rsi_high = rsi_limits
        
        # 计算ATR用于判断巨型K线 (简化版：使用过去20根K线的平均振幅)
        # 预计算平均振幅
        avg_amplitudes = [0.0] * len(self.klines_1m)
        for i in range(20, len(self.klines_1m)):
            s = 0
            for j in range(i-20, i):
                k = self.klines_1m[j]
                s += (k['high'] - k['low'])
            avg_amplitudes[i] = s / 20

        print(f"\n>>> 正在回测: {strategy_name}")
        
        for i in range(101, len(self.klines_1m) - 10):
            k1m = self.klines_1m[i]
            prev_k1m = self.klines_1m[i-1]
            
            if k1m.get('rsi') is None or k1m.get('bb_lower') is None:
                continue
                
            # 1. 时间过滤
            if time_ranges:
                dt = datetime.strptime(k1m['datetime'], '%Y-%m-%d %H:%M:%S')
                hour = dt.hour
                in_time = False
                for start_h, end_h in time_ranges:
                    if start_h <= hour < end_h:
                        in_time = True
                        break
                if not in_time:
                    continue

            # 2. 巨型K线过滤 (避开新闻/瀑布)
            # 如果前一根K线振幅超过平均振幅的 3 倍，认为是不稳定状态，跳过
            if avoid_news:
                prev_amp = prev_k1m['high'] - prev_k1m['low']
                avg_amp = avg_amplitudes[i-1]
                if avg_amp > 0 and prev_amp > 3 * avg_amp:
                    continue

            trade = None
            prev_rsi = prev_k1m['rsi']
            
            # 3. 信号触发
            # 做多
            if prev_rsi < rsi_low:
                # 布林带确认
                if not bb_confirm or (bb_confirm and k1m['low'] <= prev_k1m['bb_lower']):
                    fill_price = min(k1m['open'], prev_k1m['bb_lower']) if bb_confirm else k1m['open']
                    trade = {'type': 'LONG', 'entry_price': fill_price, 'entry_time': k1m['datetime']}
            
            # 做空
            elif prev_rsi > rsi_high:
                # 布林带确认
                if not bb_confirm or (bb_confirm and k1m['high'] >= prev_k1m['bb_upper']):
                    fill_price = max(k1m['open'], prev_k1m['bb_upper']) if bb_confirm else k1m['open']
                    trade = {'type': 'SHORT', 'entry_price': fill_price, 'entry_time': k1m['datetime']}

            if trade:
                settlement_kline = self.klines_1m[i+10]
                settlement_price = settlement_kline['open']
                
                trade['exit_time'] = settlement_kline['datetime']
                trade['exit_price'] = settlement_price
                
                is_win = False
                if trade['type'] == 'LONG':
                    is_win = settlement_price > trade['entry_price']
                else:
                    is_win = settlement_price < trade['entry_price']
                    
                trade['is_win'] = is_win
                trade['profit'] = 0.8 if is_win else -1.0
                trades.append(trade)
        
        return trades

if __name__ == "__main__":
    # 1. 获取数据
    if not os.path.exists('ETHUSDT_1m_klines.json'):
        fetcher = BinanceDataFetcher(days=100)
        fetcher.fetch()
    
    # 2. 运行回测
    strategy = WickSniperStrategyPro()
    if strategy.load_data():
        strategy.calculate_bollinger_bands()
        strategy.calculate_rsi()
        
        print("\n开始复杂组合策略回测 (固定10分钟结算)...")
        print("="*60)
        
        # 场景 A: 亚盘狙击 (UTC 0-8点，震荡为主) + RSI极端 + BB确认
        # 逻辑：亚洲时间市场比较安静，适合做反转
        trades = strategy.backtest_complex(
            "亚盘狙击 (UTC 0-8) + RSI<25/>75 + BB确认", 
            rsi_limits=(25, 75), 
            time_ranges=[(0, 8)], 
            bb_confirm=True,
            avoid_news=True
        )
        if trades:
            win_rate = len([t for t in trades if t['is_win']]) / len(trades) * 100
            ev = sum(t['profit'] for t in trades) / len(trades)
            print(f"交易数: {len(trades)}, 胜率: {win_rate:.2f}%, EV: {ev:.4f}")
            if win_rate > 55.6: print("✅ 盈利") 
            else: print("❌ 亏损")

        # 场景 B: 避开美股 (只做 UTC 0-13点) + RSI极端
        trades = strategy.backtest_complex(
            "避开美股 (UTC 0-13) + RSI<20/>80", 
            rsi_limits=(20, 80), 
            time_ranges=[(0, 13)], 
            bb_confirm=False,
            avoid_news=True
        )
        if trades:
            win_rate = len([t for t in trades if t['is_win']]) / len(trades) * 100
            ev = sum(t['profit'] for t in trades) / len(trades)
            print(f"交易数: {len(trades)}, 胜率: {win_rate:.2f}%, EV: {ev:.4f}")
            if win_rate > 55.6: print("✅ 盈利") 
            else: print("❌ 亏损")

        # 场景 C: 全天候 + 极度恐慌 (RSI<15/>85) + 避开新闻
        trades = strategy.backtest_complex(
            "极度恐慌 (RSI<15/>85) + 避开新闻", 
            rsi_limits=(15, 85), 
            time_ranges=None, 
            bb_confirm=False,
            avoid_news=True
        )
        if trades:
            win_rate = len([t for t in trades if t['is_win']]) / len(trades) * 100
            ev = sum(t['profit'] for t in trades) / len(trades)
            print(f"交易数: {len(trades)}, 胜率: {win_rate:.2f}%, EV: {ev:.4f}")
            if win_rate > 55.6: print("✅ 盈利") 
            else: print("❌ 亏损")

if __name__ == "__main__":
    # 1. 获取数据 (如果文件不存在)
    if not os.path.exists('ETHUSDT_1m_klines.json'):
        fetcher = BinanceDataFetcher(days=100)
        fetcher.fetch()
    
    # 2. 运行回测
    strategy = WickSniperStrategyPro()
    if strategy.load_data():
        # 计算所有指标
        strategy.calculate_bollinger_bands()
        strategy.calculate_rsi()
        strategy.calculate_ema(100)
        
        print("\n开始多策略对比回测 (固定10分钟结算)...")
        print("="*60)
        
        strategies = ['trend_surfer', 'bb_breakout', 'rsi_extreme']
        
        for s_name in strategies:
            trades = strategy.backtest_strategy(s_name)
            if trades:
                total = len(trades)
                wins = len([t for t in trades if t['is_win']])
                win_rate = wins / total * 100
                ev = sum(t['profit'] for t in trades) / total
                
                print(f"策略: {s_name}")
                print(f"交易数: {total}")
                print(f"胜率: {win_rate:.2f}%")
                print(f"期望值(EV): {ev:.4f} U/单")
                
                if win_rate > 55.6:
                    print("✅ 盈利！")
                else:
                    print("❌ 亏损")
                print("-" * 30)
            else:
                print(f"策略 {s_name} 未产生交易")

