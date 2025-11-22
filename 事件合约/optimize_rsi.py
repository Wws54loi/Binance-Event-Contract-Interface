import json
import os
from datetime import datetime, timedelta
import sys

# Add current directory to path to import wick_sniper_pro
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from wick_sniper_pro import WickSniperStrategyPro
except ImportError:
    # Fallback if running from root
    sys.path.append(os.path.join(os.getcwd(), '事件合约'))
    from wick_sniper_pro import WickSniperStrategyPro

class RSIOptimizer(WickSniperStrategyPro):
    def run_optimization(self):
        print(f"\n{'='*50}")
        print(f"Running RSI Optimization (Stop Loss: -45U, Max Trades: 5)")
        print(f"{'='*50}")

        # Ensure indicators are calculated
        if not self.klines_1m:
            if not self.load_data():
                print("Failed to load data.")
                return

        # Check if indicators exist, if not calculate them
        if 'rsi' not in self.klines_1m[-1] or 'bb_upper' not in self.klines_1m[-1]:
            print("Calculating indicators...")
            self.calculate_rsi(14)
            self.calculate_bollinger_bands(20, 2)

        # Define parameter grid
        # Format: (Long_Entry, Long_Extreme, Short_Entry, Short_Extreme)
        # Current: (25, 20, 75, 80)
        params_grid = [
            (30, 25, 70, 75), # Looser
            (25, 20, 75, 80), # Baseline (Current)
            (22, 18, 78, 82), # Tighter
            (20, 15, 80, 85), # Very Tight
            (25, 20, 70, 75), # Asymmetric (Easier to Short)
            (30, 25, 75, 80), # Asymmetric (Easier to Long)
        ]
        
        results = []

        for params in params_grid:
            rsi_long, rsi_long_ex, rsi_short, rsi_short_ex = params
            print(f"Testing RSI: Long<{rsi_long}/{rsi_long_ex} | Short>{rsi_short}/{rsi_short_ex} ...")
            
            res = self.run_single_backtest(rsi_long, rsi_long_ex, rsi_short, rsi_short_ex)
            results.append({
                'params': params,
                'profit': res['profit'],
                'win_rate': res['win_rate'],
                'trades': res['trades'],
                'stopped_days': res['stopped_days']
            })

        # Print Comparison
        print(f"\n{'='*80}")
        print(f"{'PARAMS (L/Lex/S/Sex)':<25} | {'PROFIT':<10} | {'WIN RATE':<10} | {'TRADES':<8} | {'STOP DAYS':<10}")
        print(f"{'-'*80}")
        
        # Sort by Profit
        results.sort(key=lambda x: x['profit'], reverse=True)
        
        for r in results:
            p_str = f"{r['params'][0]}/{r['params'][1]}/{r['params'][2]}/{r['params'][3]}"
            print(f"{p_str:<25} | {r['profit']:<10.2f} | {r['win_rate']:<9.2f}% | {r['trades']:<8} | {r['stopped_days']:<10}")

    def run_single_backtest(self, rsi_long, rsi_long_ex, rsi_short, rsi_short_ex):
        stop_loss_limit = -45
        max_active_trades = 5
        start_hour = 9
        
        daily_stats = {} 
        current_day_pnl = 0
        is_stopped_today = False
        
        active_trade_indices = [] # List of expiry indices
        
        for i in range(100, len(self.klines_1m) - 10):
            k1m = self.klines_1m[i]
            prev_k1m = self.klines_1m[i-1]
            
            # Update active trades
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
            
            # Stop Loss Check
            if is_stopped_today: continue
            
            # Max Trades Check
            if len(active_trade_indices) >= max_active_trades: continue

            prev_rsi = prev_k1m['rsi']
            trade = None
            bet_amount = 0
            
            # Giant Candle Check
            avg_amp = 0
            if i > 20:
                sum_amp = 0
                for j in range(i-20, i):
                    sum_amp += (self.klines_1m[j]['high'] - self.klines_1m[j]['low'])
                avg_amp = sum_amp / 20
            
            prev_amp = prev_k1m['high'] - prev_k1m['low']
            if avg_amp > 0 and prev_amp > 3 * avg_amp and prev_amp > 15.0:
                continue 

            # Dynamic RSI Logic
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
                
                if trade['type'] == 'LONG':
                    is_win = settlement_price > trade['entry_price']
                else:
                    is_win = settlement_price < trade['entry_price']
                
                if is_win:
                    profit = 0.8 * trade['amount']
                else:
                    profit = -1.0 * trade['amount']
                    
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
        
        optimizer = RSIOptimizer(data_file=data_path)
        if optimizer.load_data():
            optimizer.run_optimization()
        else:
            print("Data load failed.")
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
