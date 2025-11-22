import json
import os
from datetime import datetime, timedelta
import sys
import numpy as np

# Add current directory to path to import wick_sniper_pro
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from wick_sniper_pro import WickSniperStrategyPro
except ImportError:
    # Fallback if running from root
    sys.path.append(os.path.join(os.getcwd(), '事件合约'))
    from wick_sniper_pro import WickSniperStrategyPro

class DynamicRSIOptimizer(WickSniperStrategyPro):
    def run_optimization(self):
        print(f"\n{'='*50}")
        print(f"Running DYNAMIC RSI Optimization")
        print(f"Stop Loss: -45U | Max Trades: 5 | Time: 09:00-20:00")
        print(f"{'='*50}")

        if not self.klines_1m:
            if not self.load_data():
                print("Failed to load data.")
                return

        if 'rsi' not in self.klines_1m[-1]:
            print("Calculating indicators...")
            self.calculate_rsi(14)
            self.calculate_bollinger_bands(20, 2)

        # 1. Analyze Volatility (Avg Amp) Distribution to set thresholds
        amps = []
        for i in range(20, len(self.klines_1m)):
            # Calculate avg_amp for this candle (same as strategy)
            sum_amp = 0
            for j in range(i-20, i):
                sum_amp += (self.klines_1m[j]['high'] - self.klines_1m[j]['low'])
            avg_amp = sum_amp / 20
            amps.append(avg_amp)
        
        p25 = np.percentile(amps, 25)
        p50 = np.percentile(amps, 50)
        p75 = np.percentile(amps, 75)
        print(f"Volatility Stats (Avg Amp): 25%={p25:.2f} | 50%={p50:.2f} | 75%={p75:.2f}")
        
        # Define Scenarios
        scenarios = [
            {
                'name': 'Baseline (Static)',
                'dynamic': False,
                'params': (25, 20, 75, 80)
            },
            {
                'name': 'Dynamic A (Conservative in Volatility)',
                'dynamic': True,
                'low_vol_threshold': p25,  # Below this is "Quiet"
                'high_vol_threshold': p75, # Above this is "Volatile"
                'quiet_params': (30, 25, 70, 75), # Aggressive in quiet
                'normal_params': (25, 20, 75, 80),
                'volatile_params': (20, 15, 80, 85) # Conservative in volatile
            },
            {
                'name': 'Dynamic B (Aggressive in Volatility)',
                'dynamic': True,
                'low_vol_threshold': p25,
                'high_vol_threshold': p75,
                'quiet_params': (25, 20, 75, 80), # Normal in quiet
                'normal_params': (25, 20, 75, 80),
                'volatile_params': (30, 25, 70, 75) # Aggressive in volatile (Catch knives?)
            },
             {
                'name': 'Dynamic C (Only Aggressive in Quiet)',
                'dynamic': True,
                'low_vol_threshold': p25,
                'high_vol_threshold': 999, # Never high
                'quiet_params': (30, 25, 70, 75), # Aggressive in quiet
                'normal_params': (25, 20, 75, 80),
                'volatile_params': (25, 20, 75, 80) 
            }
        ]
        
        results = []

        for scen in scenarios:
            print(f"Testing: {scen['name']} ...")
            res = self.run_backtest(scen)
            results.append({
                'name': scen['name'],
                'profit': res['profit'],
                'win_rate': res['win_rate'],
                'trades': res['trades'],
                'stopped_days': res['stopped_days']
            })

        # Print Comparison
        print(f"\n{'='*100}")
        print(f"{'SCENARIO':<35} | {'PROFIT':<10} | {'WIN RATE':<10} | {'TRADES':<8} | {'STOP DAYS':<10}")
        print(f"{'-'*100}")
        
        results.sort(key=lambda x: x['profit'], reverse=True)
        
        for r in results:
            print(f"{r['name']:<35} | {r['profit']:<10.2f} | {r['win_rate']:<9.2f}% | {r['trades']:<8} | {r['stopped_days']:<10}")

    def run_backtest(self, scenario):
        stop_loss_limit = -45
        max_active_trades = 5
        start_hour = 9
        
        daily_stats = {} 
        current_day_pnl = 0
        is_stopped_today = False
        active_trade_indices = []
        
        for i in range(100, len(self.klines_1m) - 10):
            k1m = self.klines_1m[i]
            prev_k1m = self.klines_1m[i-1]
            
            active_trade_indices = [idx for idx in active_trade_indices if idx > i]
            
            dt = datetime.strptime(k1m['datetime'], '%Y-%m-%d %H:%M:%S')
            
            if dt.hour < start_hour:
                trading_day_date = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                trading_day_date = dt.strftime('%Y-%m-%d')
            
            if not (9 <= dt.hour < 20):
                continue

            if trading_day_date not in daily_stats:
                daily_stats[trading_day_date] = {
                    'profit': 0, 'trades': 0, 'wins': 0, 'stopped': False
                }
                current_day_pnl = 0
                is_stopped_today = False
            
            if k1m.get('rsi') is None: continue
            if is_stopped_today: continue
            if len(active_trade_indices) >= max_active_trades: continue

            # Calculate Avg Amp
            avg_amp = 0
            if i > 20:
                sum_amp = 0
                for j in range(i-20, i):
                    sum_amp += (self.klines_1m[j]['high'] - self.klines_1m[j]['low'])
                avg_amp = sum_amp / 20
            
            # Giant Candle Check (Always active)
            prev_amp = prev_k1m['high'] - prev_k1m['low']
            if avg_amp > 0 and prev_amp > 3 * avg_amp and prev_amp > 15.0:
                continue 

            # Determine Thresholds
            rsi_long, rsi_long_ex, rsi_short, rsi_short_ex = (25, 20, 75, 80) # Default
            
            if not scenario['dynamic']:
                rsi_long, rsi_long_ex, rsi_short, rsi_short_ex = scenario['params']
            else:
                if avg_amp < scenario['low_vol_threshold']:
                    rsi_long, rsi_long_ex, rsi_short, rsi_short_ex = scenario['quiet_params']
                elif avg_amp > scenario['high_vol_threshold']:
                    rsi_long, rsi_long_ex, rsi_short, rsi_short_ex = scenario['volatile_params']
                else:
                    rsi_long, rsi_long_ex, rsi_short, rsi_short_ex = scenario['normal_params']

            prev_rsi = prev_k1m['rsi']
            trade = None
            bet_amount = 0
            
            if prev_rsi < rsi_long:
                if prev_rsi < rsi_long_ex: bet_amount = 15
                else: bet_amount = 10
                trade = {'type': 'LONG', 'entry_price': k1m['open'], 'amount': bet_amount}
                
            elif prev_rsi > rsi_short:
                if prev_rsi > rsi_short_ex: bet_amount = 15
                else: bet_amount = 10
                trade = {'type': 'SHORT', 'entry_price': k1m['open'], 'amount': bet_amount}
                
            if trade:
                active_trade_indices.append(i + 10)
                settlement_kline = self.klines_1m[i+10]
                settlement_price = settlement_kline['open']
                is_win = False
                profit = 0
                if trade['type'] == 'LONG': is_win = settlement_price > trade['entry_price']
                else: is_win = settlement_price < trade['entry_price']
                
                if is_win: profit = 0.8 * trade['amount']
                else: profit = -1.0 * trade['amount']
                    
                daily_stats[trading_day_date]['profit'] += profit
                daily_stats[trading_day_date]['trades'] += 1
                if is_win: daily_stats[trading_day_date]['wins'] += 1
                current_day_pnl += profit
                
                if current_day_pnl <= stop_loss_limit:
                    is_stopped_today = True
                    daily_stats[trading_day_date]['stopped'] = True

        total_profit = sum(d['profit'] for d in daily_stats.values())
        total_trades = sum(d['trades'] for d in daily_stats.values())
        total_wins = sum(d['wins'] for d in daily_stats.values())
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        stopped_days = len([d for d in daily_stats.values() if d['stopped']])
        
        return {'profit': total_profit, 'win_rate': win_rate, 'trades': total_trades, 'stopped_days': stopped_days}

if __name__ == "__main__":
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_path = os.path.join(script_dir, 'ETHUSDT_1m_klines.json')
        optimizer = DynamicRSIOptimizer(data_file=data_path)
        if optimizer.load_data():
            optimizer.run_optimization()
    except Exception as e:
        print(f"Error: {e}")
