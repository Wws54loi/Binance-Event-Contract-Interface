print("DEBUG: Starting optimize_mtf.py...")
import json
import os
from datetime import datetime, timedelta
import sys
import numpy as np
import pandas as pd

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from wick_sniper_pro import WickSniperStrategyPro
except ImportError:
    sys.path.append(os.path.join(os.getcwd(), '事件合约'))
    from wick_sniper_pro import WickSniperStrategyPro

class MtfOptimizer(WickSniperStrategyPro):
    def __init__(self):
        # Fix path
        data_path = 'ETHUSDT_1m_klines.json'
        if not os.path.exists(data_path):
            possible_path = os.path.join('事件合约', 'ETHUSDT_1m_klines.json')
            if os.path.exists(possible_path):
                data_path = possible_path
        
        super().__init__(data_file=data_path)
        self.klines_10m = {} # timestamp -> {close, ma20}

    def preprocess_data(self):
        """Ensure data has 'time' field in ms timestamp"""
        if not self.klines_1m: return
        
        first = self.klines_1m[0]
        if 'time' not in first and 'datetime' in first:
            print("🔄 Converting datetime strings to timestamps...")
            for k in self.klines_1m:
                dt = datetime.strptime(k['datetime'], '%Y-%m-%d %H:%M:%S')
                k['time'] = int(dt.timestamp() * 1000)

    def resample_10m(self):
        """Resample 1m klines to 10m and calculate MA20"""
        print("⏳ Resampling 1m data to 10m timeframe...")
        
        # Ensure data is preprocessed
        self.preprocess_data()
        
        # Convert to DataFrame for easier resampling
        df = pd.DataFrame(self.klines_1m)
        
        # Ensure time column is datetime object for resampling
        if 'time' in df.columns:
            df['datetime_obj'] = pd.to_datetime(df['time'], unit='ms')
        else:
            # Fallback
            df['datetime_obj'] = pd.to_datetime(df['datetime'])
            
        df.set_index('datetime_obj', inplace=True)
        
        # Resample logic
        # Open: first 1m open
        # High: max of 1m highs
        # Low: min of 1m lows
        # Close: last 1m close
        df_10m = df.resample('10min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        })
        
        # Drop NaNs (if any gaps)
        df_10m.dropna(inplace=True)
        
        # Calculate MA20 on 10m
        df_10m['ma20'] = df_10m['close'].rolling(window=20).mean()
        
        # Convert back to dict for fast lookup: timestamp(ms) -> data
        for index, row in df_10m.iterrows():
            # index is the start time of the bin (e.g. 09:00:00)
            ts = int(index.timestamp() * 1000)
            self.klines_10m[ts] = {
                'close': row['close'],
                'ma20': row['ma20']
            }
            
        print(f"✅ Generated {len(self.klines_10m)} 10m candles.")

    def get_trend_10m(self, current_time_ms):
        """
        Determine trend based on the LAST COMPLETED 10m candle.
        """
        # Current time is e.g. 09:13.
        # The current 10m candle started at 09:10.
        # The last COMPLETED 10m candle started at 09:00.
        
        # 1. Find start of current 10m period
        current_period_start = (current_time_ms // 600000) * 600000
        
        # 2. Look back 1 period (10 mins ago)
        last_period_start = current_period_start - 600000
        
        if last_period_start in self.klines_10m:
            k = self.klines_10m[last_period_start]
            if pd.isna(k['ma20']):
                return 'NEUTRAL'
            
            if k['close'] > k['ma20']:
                return 'UP'
            else:
                return 'DOWN'
        
        return 'NEUTRAL'

    def check_exits(self, current_time, current_price):
        for trade in self.active_trades:
            if trade['status'] == 'OPEN' and current_time >= trade['expiry_time']:
                # Close trade
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
                    trade['pnl'] = 'WIN'
                else:
                    payout = -trade['amount']
                    trade['pnl'] = 'LOSS'
                
                trade['pnl_amount'] = payout
                self.daily_pnl += payout
                self.trade_history.append(trade)

    def run_backtest_mtf(self, use_filter=False):
        """
        Run backtest with optional MTF filter.
        """
        # Reset state
        self.daily_pnl = 0
        self.active_trades = []
        self.trade_history = []
        self.stopped_days = set()
        
        # Use Dynamic C params (Best so far)
        # Quiet: 30/70, Normal: 25/75
        # We need to calculate volatility first (simplified here, just use static best for comparison to isolate MTF variable)
        # Actually, let's use the "Baseline" params (25/75) to keep it simple and see if MTF improves it.
        # If we mix Dynamic + MTF, it gets complex. Let's test: Baseline vs Baseline+MTF.
        
        rsi_long = 25
        rsi_short = 75
        
        # Pre-calculate indicators if needed
        if 'rsi' not in self.klines_1m[-1]:
            self.calculate_rsi(14)
            self.calculate_bollinger_bands(20, 2)
            
        for i in range(50, len(self.klines_1m)):
            k = self.klines_1m[i]
            prev_k = self.klines_1m[i-1]
            current_time = k['time']
            
            # 1. Check Exits
            self.check_exits(k['time'], k['close'])
            
            # 2. Check Stop Loss
            day_str = datetime.fromtimestamp(k['time']/1000).strftime('%Y-%m-%d')
            if day_str in self.stopped_days:
                continue
                
            if self.daily_pnl <= -45:
                self.stopped_days.add(day_str)
                self.daily_pnl = 0 
                continue
                
            # 3. Check Time (09-20)
            dt = datetime.fromtimestamp(k['time']/1000)
            if not (9 <= dt.hour < 20):
                continue
                
            # 4. Check Max Trades
            open_trades = [t for t in self.active_trades if t['status'] == 'OPEN']
            if len(open_trades) >= 5:
                continue
                
            # 5. Signal Logic
            signal = None
            
            if prev_k['rsi'] < rsi_long:
                signal = 'LONG'
            elif prev_k['rsi'] > rsi_short:
                signal = 'SHORT'
                
            if not signal:
                continue
                
            # === MTF FILTER ===
            if use_filter:
                trend = self.get_trend_10m(current_time)
                if trend == 'UP' and signal == 'SHORT':
                    continue # Don't short in uptrend
                if trend == 'DOWN' and signal == 'LONG':
                    continue # Don't long in downtrend
            # ==================
            
            # Execute
            entry_price = k['open'] # Simplified execution at open of next candle
            amount = 10 # Fixed amount for test
            
            self.active_trades.append({
                'id': len(self.trade_history),
                'type': signal,
                'entry_price': entry_price,
                'amount': amount,
                'entry_time': current_time,
                'expiry_time': current_time + 600000, # 10 mins
                'status': 'OPEN'
            })
            
        # Calculate stats
        wins = len([t for t in self.trade_history if t['pnl'] == 'WIN'])
        total = len(self.trade_history)
        win_rate = (wins/total*100) if total > 0 else 0
        total_profit = sum([t['pnl_amount'] for t in self.trade_history])
        
        return {
            'profit': total_profit,
            'win_rate': win_rate,
            'trades': total
        }

    def run_optimization(self):
        print(f"\n{'='*50}")
        print(f"Running MTF (10m Trend Filter) Optimization")
        print(f"{'='*50}")

        if not self.klines_1m:
            if not self.load_data():
                return

        # Generate 10m data
        self.resample_10m()
        
        # 1. Baseline (No Filter)
        print("Testing Baseline (No Filter)...")
        res_base = self.run_backtest_mtf(use_filter=False)
        
        # 2. MTF Filter
        print("Testing MTF Filter (Trend Following)...")
        res_mtf = self.run_backtest_mtf(use_filter=True)
        
        print(f"\n{'='*80}")
        print(f"{'SCENARIO':<30} | {'PROFIT':<10} | {'WIN RATE':<10} | {'TRADES':<8}")
        print(f"{'-'*80}")
        print(f"{'Baseline (Ignore Trend)':<30} | {res_base['profit']:<10.1f} | {res_base['win_rate']:<10.2f}% | {res_base['trades']:<8}")
        print(f"{'MTF Filter (Follow Trend)':<30} | {res_mtf['profit']:<10.1f} | {res_mtf['win_rate']:<10.2f}% | {res_mtf['trades']:<8}")
        print(f"{'='*80}")

if __name__ == "__main__":
    opt = MtfOptimizer()
    opt.run_optimization()
