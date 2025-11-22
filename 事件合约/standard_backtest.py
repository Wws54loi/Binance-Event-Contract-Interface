import json
import os
from datetime import datetime, timedelta
import sys
import numpy as np
import pandas as pd

# Add current directory to path to import wick_sniper_pro
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from wick_sniper_pro import WickSniperStrategyPro
except ImportError:
    # Fallback if running from root
    sys.path.append(os.path.join(os.getcwd(), '事件合约'))
    from wick_sniper_pro import WickSniperStrategyPro

class StandardBacktest(WickSniperStrategyPro):
    def run_standard_test(self):
        """
        Run the Standard Backtest using 'Dynamic C' Strategy.
        Generates 'backtest_details.csv' and prints Macro Analysis.
        """
        print(f"\n{'='*60}")
        print(f"🚀 STANDARD BACKTEST: Dynamic C Strategy")
        print(f"🛡️ Risk: Stop Loss -45U | Max Trades 5 | Time 09:00-20:00")
        print(f"{'='*60}")

        # 1. Load Data
        if not self.klines_1m:
            if not self.load_data():
                print("❌ Failed to load data.")
                return

        # 2. Calculate Indicators
        if 'rsi' not in self.klines_1m[-1]:
            print("⏳ Calculating indicators (RSI, BB, AvgAmp)...")
            self.calculate_rsi(14)
            self.calculate_bollinger_bands(20, 2)
            
            # Calculate Avg Amp (Volatility)
            for i in range(20, len(self.klines_1m)):
                sum_amp = 0
                for j in range(i-20, i):
                    sum_amp += (self.klines_1m[j]['high'] - self.klines_1m[j]['low'])
                self.klines_1m[i]['avg_amp'] = sum_amp / 20

        # 3. Calculate Volatility Thresholds (P25)
        print("⏳ Analyzing Volatility Distribution...")
        amps = [k['avg_amp'] for k in self.klines_1m if 'avg_amp' in k]
        p25 = np.percentile(amps, 25)
        print(f"📊 Volatility P25 Threshold: {p25:.4f} (Below this = Quiet Market 🐟)")

        # 4. Prepare Logging
        log_filename = "backtest_details.csv"
        log_file = open(log_filename, 'w', encoding='utf-8')
        log_file.write("Time,Type,Price,Amount,RSI,Volatility,MarketState,Result,Profit,DayPnL\n")
        
        # 5. Backtest Loop
        trades = []
        daily_stats = {} 
        current_day_pnl = 0
        is_stopped_today = False
        active_trade_indices = []
        
        # Strategy Params (Dynamic C)
        # Quiet: Long < 30 (10U)/25 (15U), Short > 70 (10U)/75 (15U)
        # Normal: Long < 25 (10U)/20 (15U), Short > 75 (10U)/80 (15U)
        
        for i in range(100, len(self.klines_1m) - 10):
            k = self.klines_1m[i]
            prev_k = self.klines_1m[i-1]
            
            # Update Active Trades
            active_trade_indices = [idx for idx in active_trade_indices if idx > i]
            
            # Date & Time Check
            dt = datetime.strptime(k['datetime'], '%Y-%m-%d %H:%M:%S')
            
            # Trading Day Logic (Starts at 09:00)
            if dt.hour < 9:
                trading_day = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                trading_day = dt.strftime('%Y-%m-%d')
                
            if trading_day not in daily_stats:
                daily_stats[trading_day] = {'profit': 0, 'trades': 0, 'wins': 0, 'stopped': False}
                current_day_pnl = 0
                is_stopped_today = False
            
            # Time Filter (09:00 - 20:00)
            if not (9 <= dt.hour < 20):
                continue
                
            # Stop Loss Check
            if is_stopped_today:
                continue
                
            # Max Trades Check
            if len(active_trade_indices) >= 5:
                continue
                
            # === STRATEGY LOGIC (Dynamic C) ===
            if 'rsi' not in prev_k or 'avg_amp' not in prev_k: continue
            
            rsi = prev_k['rsi']
            avg_amp = prev_k['avg_amp']
            
            # Giant Candle Check
            prev_amp = prev_k['high'] - prev_k['low']
            is_giant = False
            if prev_amp > 3 * avg_amp and prev_amp > 15.0:
                is_giant = True
            
            if is_giant: continue
            
            # Market State
            is_quiet = avg_amp < p25
            market_state = "Quiet" if is_quiet else "Normal"
            
            # Thresholds
            if is_quiet:
                long_th = 30; long_strong = 25
                short_th = 70; short_strong = 75
            else:
                long_th = 25; long_strong = 20
                short_th = 75; short_strong = 80
                
            signal = None
            amount = 0
            
            if rsi < long_th:
                signal = 'LONG'
                amount = 15 if rsi < long_strong else 10
            elif rsi > short_th:
                signal = 'SHORT'
                amount = 15 if rsi > short_strong else 10
                
            if signal:
                # Execute Trade
                active_trade_indices.append(i + 10)
                
                # Settlement (Simplified: Open of i+10)
                settlement_k = self.klines_1m[i+10]
                entry_price = k['open']
                exit_price = settlement_k['open']
                
                is_win = False
                if signal == 'LONG': is_win = exit_price > entry_price
                else: is_win = exit_price < entry_price
                
                payout = amount * 0.8 if is_win else -amount
                
                # Update Stats
                current_day_pnl += payout
                daily_stats[trading_day]['profit'] += payout
                daily_stats[trading_day]['trades'] += 1
                if is_win: daily_stats[trading_day]['wins'] += 1
                
                # Log
                res_str = "WIN" if is_win else "LOSS"
                log_file.write(f"{k['datetime']},{signal},{entry_price},{amount},{rsi:.1f},{avg_amp:.2f},{market_state},{res_str},{payout:.1f},{current_day_pnl:.1f}\n")
                
                # Check Stop Loss
                if current_day_pnl <= -45:
                    is_stopped_today = True
                    daily_stats[trading_day]['stopped'] = True
                    log_file.write(f"{k['datetime']},STOP,0,0,0,0,STOPPED,STOP,0,{current_day_pnl:.1f}\n")

        log_file.close()
        print(f"✅ Detailed logs saved to: {log_filename}")
        
        # 6. Macro Analysis
        self.print_macro_stats(daily_stats)

    def print_macro_stats(self, daily_stats):
        print(f"\n{'='*60}")
        print(f"📊 MACRO ANALYSIS REPORT")
        print(f"{'='*60}")
        
        total_days = len(daily_stats)
        total_profit = sum(d['profit'] for d in daily_stats.values())
        stopped_days = len([d for d in daily_stats.values() if d['stopped']])
        
        print(f"Total Profit: {total_profit:.2f} U")
        print(f"Total Days: {total_days}")
        print(f"Days Stopped Out: {stopped_days} ({(stopped_days/total_days)*100:.1f}%)")
        
        # Monthly Stats
        monthly_stats = {}
        for day, stats in daily_stats.items():
            month = day[:7] # YYYY-MM
            if month not in monthly_stats:
                monthly_stats[month] = {'profit': 0, 'days': 0}
            monthly_stats[month]['profit'] += stats['profit']
            monthly_stats[month]['days'] += 1
            
        print(f"\n📅 Monthly Performance:")
        print(f"{'Month':<10} | {'Profit':<10} | {'Avg/Day':<10}")
        print("-" * 36)
        for month in sorted(monthly_stats.keys()):
            p = monthly_stats[month]['profit']
            d = monthly_stats[month]['days']
            avg = p / d if d > 0 else 0
            print(f"{month:<10} | {p:<10.1f} | {avg:<10.1f}")
            
        # Streak Analysis
        print(f"\n📉 Losing Streak Analysis (Consecutive Losing Days):")
        streaks = []
        current_streak = 0
        for day in sorted(daily_stats.keys()):
            if daily_stats[day]['profit'] < 0:
                current_streak += 1
            else:
                if current_streak > 0:
                    streaks.append(current_streak)
                current_streak = 0
        if current_streak > 0: streaks.append(current_streak)
        
        if streaks:
            print(f"Max Consecutive Losing Days: {max(streaks)}")
            print(f"Average Losing Streak: {sum(streaks)/len(streaks):.1f} days")
        else:
            print("No losing streaks found.")

if __name__ == "__main__":
    # Fix path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(script_dir, 'ETHUSDT_1m_klines.json')
    
    bt = StandardBacktest(data_file=data_path)
    bt.run_standard_test()
