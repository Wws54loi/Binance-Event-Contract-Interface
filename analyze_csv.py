import pandas as pd

try:
    df = pd.read_csv('backtest_details.csv')
    
    total_trades = len(df)
    wins = len(df[df['Result'] == 'WIN'])
    losses = len(df[df['Result'] == 'LOSS'])
    
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    print(f"Total Trades in CSV: {total_trades}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Calculated Win Rate: {win_rate:.2f}%")
    
    # Analyze streaks
    df['group'] = (df['Result'] != df['Result'].shift()).cumsum()
    streaks = df.groupby(['Result', 'group']).size()
    
    max_win_streak = streaks['WIN'].max() if 'WIN' in streaks else 0
    max_loss_streak = streaks['LOSS'].max() if 'LOSS' in streaks else 0
    
    print(f"Max Winning Streak: {max_win_streak}")
    print(f"Max Losing Streak: {max_loss_streak}")
    
    # Show top 10 days with most losses
    df['Date'] = df['Time'].apply(lambda x: x.split(' ')[0])
    daily_counts = df.groupby('Date')['Result'].value_counts().unstack().fillna(0)
    daily_counts['Total'] = daily_counts['WIN'] + daily_counts['LOSS']
    daily_counts['LossRate'] = daily_counts['LOSS'] / daily_counts['Total']
    
    print("\nTop 5 Days with Highest Loss Rate (min 5 trades):")
    print(daily_counts[daily_counts['Total'] >= 5].sort_values('LossRate', ascending=False).head(5))

    # --- New Analysis: Daily and Monthly Streaks ---
    print(f"\n{'='*30}")
    print("MACRO ANALYSIS (Daily & Monthly)")
    print(f"{'='*30}")
    
    # Calculate Daily PnL
    daily_pnl = df.groupby('Date')['Profit'].sum()
    daily_df = daily_pnl.to_frame(name='PnL')
    daily_df['Result'] = daily_df['PnL'].apply(lambda x: 'WIN' if x > 0 else ('LOSS' if x < 0 else 'BREAKEVEN'))
    
    # Daily Streaks
    daily_df['group'] = (daily_df['Result'] != daily_df['Result'].shift()).cumsum()
    daily_streaks = daily_df.groupby(['Result', 'group']).size()
    
    max_daily_win_streak = daily_streaks['WIN'].max() if 'WIN' in daily_streaks else 0
    max_daily_loss_streak = daily_streaks['LOSS'].max() if 'LOSS' in daily_streaks else 0
    
    print(f"Total Trading Days: {len(daily_df)}")
    print(f"Winning Days: {len(daily_df[daily_df['PnL'] > 0])}")
    print(f"Losing Days: {len(daily_df[daily_df['PnL'] < 0])}")
    print(f"Max Consecutive Winning Days: {max_daily_win_streak}")
    print(f"Max Consecutive Losing Days: {max_daily_loss_streak}")
    
    # Monthly Analysis
    df['Month'] = df['Time'].apply(lambda x: x[:7]) # YYYY-MM
    monthly_pnl = df.groupby('Month')['Profit'].sum()
    
    print("\n--- Monthly Performance ---")
    print(monthly_pnl)
    
    losing_months = len(monthly_pnl[monthly_pnl < 0])
    print(f"\nTotal Months: {len(monthly_pnl)}")
    print(f"Losing Months: {losing_months}")
    if losing_months > 0:
        print(f"Worst Month: {monthly_pnl.idxmin()} ({monthly_pnl.min():.2f} U)")
    print(f"Best Month: {monthly_pnl.idxmax()} ({monthly_pnl.max():.2f} U)")

except Exception as e:
    print(f"Error analyzing CSV: {e}")
