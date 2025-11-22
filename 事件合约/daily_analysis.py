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

class DailyAnalysis(WickSniperStrategyPro):
    def run_analysis(self, stop_loss_limit=None, start_hour=9):
        """
        Run backtest with daily analysis and optional stop-loss.
        
        :param stop_loss_limit: Daily loss limit (negative number, e.g., -50). If None, no limit.
        :param start_hour: Hour to start the "trading day" (0-23). Default 9.
        """
        print(f"\n{'='*50}")
        print(f"Running Analysis with Stop Loss: {stop_loss_limit if stop_loss_limit else 'None'}")
        print(f"{'='*50}")

        # Create a log file for detailed trade history
        log_filename = "backtest_details.csv"
        log_file = open(log_filename, 'w', encoding='utf-8')
        log_file.write("Time,Type,Price,Amount,RSI,ActiveTrades,Result,Profit,DayPnL\n")
        print(f"📝 Detailed log will be saved to: {log_filename}")

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

        trades = []
        daily_stats = {} # { 'YYYY-MM-DD': {'profit': 0, 'trades': 0, 'wins': 0, 'losses': 0, 'stopped': False} }
        
        current_day_pnl = 0
        is_stopped_today = False
        
        # We need to track the "trading day". 
        # A trading day starts at start_hour:00:00 and ends at start_hour:00:00 next day.
        # We'll use the date of the start of the trading day as the key.
        
        rsi_low = 20
        rsi_high = 80
        
        # Concurrent Trades Analysis
        active_trade_indices = [] # List of expiry indices of active trades
        concurrent_counts = {} # { count: frequency }
        max_concurrent = 0
        
        # Iterate through klines
        # Start from 100 to have enough history for indicators
        for i in range(100, len(self.klines_1m) - 10):
            k1m = self.klines_1m[i]
            prev_k1m = self.klines_1m[i-1]
            
            # Update active trades (remove expired ones)
            # A trade expires at index 'expiry_index'. If current index 'i' >= expiry_index, it's closed.
            # Actually, if expiry is i+10, it is closed AT i+10. So at i+10 it is NOT active?
            # Let's say trade at i=100 expires at 110.
            # At i=101, it is active. At i=109, active. At i=110, it closes.
            # So we keep indices > i.
            active_trade_indices = [idx for idx in active_trade_indices if idx > i]
            
            current_concurrent = len(active_trade_indices)
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent
            
            concurrent_counts[current_concurrent] = concurrent_counts.get(current_concurrent, 0) + 1
            
            dt = datetime.strptime(k1m['datetime'], '%Y-%m-%d %H:%M:%S')
            
            # Determine current trading day
            # If hour < start_hour, it belongs to the previous calendar day's trading session
            if dt.hour < start_hour:
                trading_day_date = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                trading_day_date = dt.strftime('%Y-%m-%d')
            
            # Time Filter: Only trade between 09:00 and 20:00
            # Note: start_hour is used for "Trading Day" definition, but we also need to filter trades
            if not (9 <= dt.hour < 20):
                continue

            if trading_day_date not in daily_stats:
                daily_stats[trading_day_date] = {
                    'profit': 0, 'trades': 0, 'wins': 0, 'losses': 0, 
                    'stopped': False,
                    'capped_profit': 0, 'capped_trades': 0, 'capped_wins': 0,
                    'unlimited_pnl': 0, 'capped_pnl': 0
                }
                # New day, reset PnL and stop flag
                current_day_pnl = 0
                is_stopped_today = False
            
            # Strategy Logic (Mean Reversion)
            if k1m.get('rsi') is None: continue
            
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
            is_giant_candle = False
            if avg_amp > 0 and prev_amp > 3 * avg_amp and prev_amp > 15.0:
                is_giant_candle = True

            if is_giant_candle:
                continue 

            # Tiered Betting Logic
            if prev_rsi < 25:
                if prev_rsi < 20: bet_amount = 15
                else: bet_amount = 10
                trade = {'type': 'LONG', 'entry_price': k1m['open'], 'time': k1m['datetime'], 'amount': bet_amount}
                
            elif prev_rsi > 75:
                if prev_rsi > 80: bet_amount = 15
                else: bet_amount = 10
                trade = {'type': 'SHORT', 'entry_price': k1m['open'], 'time': k1m['datetime'], 'amount': bet_amount}
                
            if trade:
                # Record this trade as active
                active_trade_indices.append(i + 10)
                
                # Settlement
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
                    
                # Update Unlimited Stats
                daily_stats[trading_day_date]['profit'] += profit
                daily_stats[trading_day_date]['unlimited_pnl'] = daily_stats[trading_day_date]['profit']
                daily_stats[trading_day_date]['trades'] += 1
                if is_win: daily_stats[trading_day_date]['wins'] += 1
                else: daily_stats[trading_day_date]['losses'] += 1
                
                # Update Capped Stats
                if not is_stopped_today:
                    daily_stats[trading_day_date]['capped_profit'] += profit
                    daily_stats[trading_day_date]['capped_pnl'] = daily_stats[trading_day_date]['capped_profit']
                    daily_stats[trading_day_date]['capped_trades'] += 1
                    if is_win: daily_stats[trading_day_date]['capped_wins'] += 1
                    
                    current_day_pnl += profit
                    
                    # Log to CSV (Only if not stopped)
                    # Time,Type,Price,Amount,RSI,ActiveTrades,Result,Profit,DayPnL
                    log_result = "WIN" if is_win else "LOSS"
                    log_line = f"{trade['time']},{trade['type']},{trade['entry_price']},{trade['amount']},{prev_rsi:.2f},{len(active_trade_indices)},{log_result},{profit:.2f},{current_day_pnl:.2f}\n"
                    log_file.write(log_line)
                    
                    if stop_loss_limit is not None and current_day_pnl <= stop_loss_limit:
                        is_stopped_today = True
                        daily_stats[trading_day_date]['stopped'] = True
                        # Log STOP event
                        log_file.write(f"{trade['time']},STOP,0,0,0,0,STOPPED,0,{current_day_pnl:.2f}\n")
                else:
                    # If stopped, capped_pnl stays same
                    pass
                
                trades.append(trade)
                
                # Log to CSV (MOVED INSIDE CAPPED LOGIC)


        # Summarize Results
        log_file.close()
        
        total_profit_unlimited = sum(d['profit'] for d in daily_stats.values())
        total_profit_capped = sum(d['capped_profit'] for d in daily_stats.values())
        
        total_trades_unlimited = sum(d['trades'] for d in daily_stats.values())
        total_wins_unlimited = sum(d['wins'] for d in daily_stats.values())
        win_rate_unlimited = (total_wins_unlimited / total_trades_unlimited * 100) if total_trades_unlimited > 0 else 0
        
        total_trades_capped = sum(d['capped_trades'] for d in daily_stats.values())
        total_wins_capped = sum(d['capped_wins'] for d in daily_stats.values())
        win_rate_capped = (total_wins_capped / total_trades_capped * 100) if total_trades_capped > 0 else 0
        
        # Trade Frequency Stats
        trades_per_day = [d['trades'] for d in daily_stats.values()]
        min_trades = min(trades_per_day) if trades_per_day else 0
        max_trades = max(trades_per_day) if trades_per_day else 0
        avg_trades = sum(trades_per_day) / len(trades_per_day) if trades_per_day else 0
        days_with_zero_trades = trades_per_day.count(0)

        total_days = len(daily_stats)
        stopped_days = [d for d in daily_stats.values() if d['stopped']]
        count_stopped = len(stopped_days)
        
        print(f"\n{'='*30}")
        print(f"RESULTS (Stop Loss: {stop_loss_limit} U)")
        print(f"{'='*30}")
        print(f"Total Days: {total_days}")
        print(f"Days Stopped: {count_stopped} ({(count_stopped/total_days)*100:.1f}%)")
        print(f"Trade Frequency: Min {min_trades} | Max {max_trades} | Avg {avg_trades:.1f}")
        print(f"Days with 0 trades: {days_with_zero_trades}")
        print(f"\n--- UNLIMITED (No Stop Loss) ---")
        print(f"Total Profit: {total_profit_unlimited:.2f} U")
        print(f"Win Rate: {win_rate_unlimited:.2f}% ({total_wins_unlimited}/{total_trades_unlimited})")
        
        print(f"\n--- CAPPED (With Stop Loss) ---")
        print(f"Total Profit: {total_profit_capped:.2f} U")
        print(f"Win Rate: {win_rate_capped:.2f}% ({total_wins_capped}/{total_trades_capped})")
        
        print(f"\n{'='*30}")
        print(f"CONCURRENT TRADES ANALYSIS")
        print(f"{'='*30}")
        print(f"Max Concurrent Trades: {max_concurrent}")
        print("Distribution (Number of Active Trades -> Frequency):")
        # Sort by number of concurrent trades
        total_minutes = sum(concurrent_counts.values())
        for count in sorted(concurrent_counts.keys()):
            freq = concurrent_counts[count]
            pct = (freq / total_minutes) * 100
            print(f"  {count} trades: {freq} minutes ({pct:.2f}%)")
            
        return daily_stats
        print(f"Total Trading Days: {total_days}")
        if stop_loss_limit:
            print(f"Days Stopped Out: {stopped_days} ({(stopped_days/total_days)*100:.1f}%)")
            
        print("\n--- Top 3 Best Days ---")
        for day, stats in sorted_days[:3]:
            print(f"{day}: Profit {stats['profit']:.2f} U | Wins: {stats['wins']}/{stats['trades']}")

        print("\n--- Top 3 Worst Days ---")
        for day, stats in sorted_days[-3:]:
            print(f"{day}: Profit {stats['profit']:.2f} U | Wins: {stats['wins']}/{stats['trades']} | Stopped: {stats['stopped']}")

        return daily_stats

if __name__ == "__main__":
    print("Starting analysis script...")
    try:
        # Construct path to data file relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_path = os.path.join(script_dir, 'ETHUSDT_1m_klines.json')
        
        analyzer = DailyAnalysis(data_file=data_path)
        print(f"Analyzer initialized with data file: {data_path}")
        if analyzer.load_data():
            print("\n>>> RUNNING ANALYSIS FOR ASIAN SESSION (09:00 - 20:00) WITH STOP LOSS -45U...")
            analyzer.run_analysis(stop_loss_limit=-45)
            
        else:
            print("Data load failed.")
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
