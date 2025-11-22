import json
import os
import time
import math
from datetime import datetime, timedelta

class AsianSniperStrategy:
    def __init__(self, data_file='ETHUSDT_1m_klines.json'):
        self.data_file = data_file
        self.klines = []
        self.trades = []
        
    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r', encoding='utf-8') as f:
                self.klines = json.load(f)
            print(f"✅ 成功加载 {len(self.klines)} 条K线数据")
            return True
        else:
            print(f"❌ 数据文件 {self.data_file} 不存在，请先运行 wick_sniper_pro.py 获取数据")
            return False

    def calculate_indicators(self):
        print("正在计算指标 (BB, RSI, ATR)...")
        closes = [k['close'] for k in self.klines]
        highs = [k['high'] for k in self.klines]
        lows = [k['low'] for k in self.klines]
        
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

        # 3. ATR / 振幅均值 (用于过滤巨型K线)
        # 简化版：计算过去20根K线的平均振幅
        for i in range(20, len(self.klines)):
            avg_amp = sum([self.klines[j]['high'] - self.klines[j]['low'] for j in range(i-20, i)]) / 20
            self.klines[i]['avg_amp'] = avg_amp

    def run_simulation(self):
        print("\n🚀 开始亚盘狙击实盘模拟...")
        print("策略配置: UTC 0-8点 | RSI < 25 / > 75 | 触碰布林带 | 避开巨型K线")
        print("="*80)
        
        # 模拟参数
        balance = 1000.0
        bet_size = 10.0 # 每次下注10U
        win_payout = 0.8 # 赢赔率
        loss_payout = -1.0 # 输赔率
        
        total_trades = 0
        wins = 0
        trade_results = [] # 记录每一笔的输赢 (True/False)
        
        # 从第100根开始模拟
        # 为了演示效果，我们只打印交易发生的时刻，或者每隔一定时间打印心跳
        for i in range(100, len(self.klines) - 10):
            curr_k = self.klines[i]
            prev_k = self.klines[i-1]
            
            # --- 1. 时间检查 (UTC 0-8) ---
            dt = datetime.strptime(curr_k['datetime'], '%Y-%m-%d %H:%M:%S')
            if not (0 <= dt.hour < 8):
                # 如果不是亚盘时间，跳过 (为了模拟效果，可以不打印，或者快速跳过)
                continue
                
            # --- 2. 数据完整性检查 ---
            if prev_k.get('bb_lower') is None or prev_k.get('rsi') is None:
                continue
                
            # --- 3. 巨型K线过滤 (新闻/瀑布) ---
            # 如果前一根K线振幅 > 3倍平均振幅，暂停交易
            prev_amp = prev_k['high'] - prev_k['low']
            avg_amp = prev_k.get('avg_amp', 0)
            is_giant_candle = avg_amp > 0 and prev_amp > 3 * avg_amp
            
            if is_giant_candle:
                # print(f"[{curr_k['datetime']}] ⚠️ 警告：检测到剧烈波动，暂停交易")
                continue

            # --- 4. 信号检测 ---
            signal = None
            entry_price = 0
            
            # 做多信号: RSI < 25 + 触碰下轨
            if prev_k['rsi'] < 25:
                if curr_k['low'] <= prev_k['bb_lower']:
                    signal = 'LONG'
                    entry_price = min(curr_k['open'], prev_k['bb_lower'])
            
            # 做空信号: RSI > 75 + 触碰上轨
            elif prev_k['rsi'] > 75:
                if curr_k['high'] >= prev_k['bb_upper']:
                    signal = 'SHORT'
                    entry_price = max(curr_k['open'], prev_k['bb_upper'])
            
            # --- 5. 执行交易 ---
            if signal:
                # 结算 (10分钟后)
                settle_k = self.klines[i+10]
                settle_price = settle_k['open']
                
                is_win = False
                if signal == 'LONG':
                    is_win = settle_price > entry_price
                else:
                    is_win = settle_price < entry_price
                
                pnl = bet_size * win_payout if is_win else bet_size * loss_payout
                balance += pnl
                
                total_trades += 1
                if is_win: wins += 1
                trade_results.append(is_win)
                
                icon = "🟢 赢" if is_win else "🔴 输"
                print(f"[{curr_k['datetime']}] ⚡ 触发 {signal} | RSI:{prev_k['rsi']:.1f} | 入场:{entry_price:.2f} -> 结算:{settle_price:.2f} | {icon} ({pnl:+.1f}U)")
                
                # 模拟一点延迟，让人看清 (如果不想等待可以注释掉)
                # time.sleep(0.05) 

        print("="*80)
        print(f"🏁 模拟结束")
        print(f"总交易: {total_trades} 笔")
        if total_trades > 0:
            print(f"胜率: {wins/total_trades*100:.2f}%")
            print(f"最终余额 (固定注码): {balance:.2f} U")
            
            # --- 统计连败概率 ---
            print("\n📊 连败统计分析:")
            loss_streaks = []
            current_streak = 0
            for res in trade_results:
                if not res:
                    current_streak += 1
                else:
                    if current_streak > 0:
                        loss_streaks.append(current_streak)
                    current_streak = 0
            if current_streak > 0: loss_streaks.append(current_streak)
            
            total_loss_sequences = len(loss_streaks)
            if total_loss_sequences > 0:
                for n in range(1, 8):
                    count = sum(1 for x in loss_streaks if x >= n)
                    prob = count / total_loss_sequences * 100
                    print(f"连败 >= {n} 笔: {count} 次 ({prob:.2f}%)")
                print(f"最大连败: {max(loss_streaks) if loss_streaks else 0} 笔")

            # --- 马丁策略模拟 ---
            print("\n🎲 马丁策略模拟 (自定义: 5U起步, 目标赚4U, 5连败止损):")
            martingale_balance = 1000.0
            base_bet = 5.0
            target_profit = 4.0
            max_steps = 5
            bet_cap = 250.0
            
            current_step = 0
            cumulative_loss_in_round = 0.0
            
            max_drawdown = 0
            peak_balance = 1000.0
            
            for res in trade_results:
                # 1. 计算注码
                if current_step == 0:
                    current_bet = base_bet
                else:
                    # 目标: 赢回之前的亏损 + 目标利润
                    # 0.8 * bet = cumulative_loss_in_round + target_profit
                    required_profit = cumulative_loss_in_round + target_profit
                    current_bet = required_profit / 0.8
                    current_bet = round(current_bet, 2)
                    if current_bet > bet_cap:
                        current_bet = bet_cap

                # 2. 执行交易
                if res: # 赢
                    pnl = current_bet * 0.8
                    martingale_balance += pnl
                    
                    # 赢了重置
                    current_step = 0
                    cumulative_loss_in_round = 0.0
                else: # 输
                    pnl = -current_bet
                    martingale_balance += pnl
                    
                    cumulative_loss_in_round += current_bet
                    current_step += 1
                    
                    # 检查止损
                    if current_step >= max_steps:
                        # 达到5连败，接受亏损，重置
                        current_step = 0
                        cumulative_loss_in_round = 0.0
                
                # 3. 统计回撤
                if martingale_balance > peak_balance:
                    peak_balance = martingale_balance
                drawdown = peak_balance - martingale_balance
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                    
                if martingale_balance <= 0:
                    print("💀 账户爆仓！")
                    break
            
            print(f"马丁最终余额: {martingale_balance:.2f} U")
            print(f"马丁最大回撤: {max_drawdown:.2f} U")
            print(f"马丁净利润: {martingale_balance - 1000:.2f} U")

            print(f"净利润: {balance - 1000:.2f} U")
        else:
            print("无交易发生")

if __name__ == "__main__":
    sim = AsianSniperStrategy()
    if sim.load_data():
        sim.calculate_indicators()
        sim.run_simulation()
