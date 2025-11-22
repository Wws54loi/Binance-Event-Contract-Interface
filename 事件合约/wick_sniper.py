import json
import os
from datetime import datetime
import math
import statistics

class WickSniperStrategy:
    """
    影线狙击策略 (Wick Sniping / Micro-Range Mean Reversion)
    
    核心逻辑:
    1. 宏观识别 (10m): 寻找"死鱼盘"，即K线实体极小(一字排开)。
    2. 微观操作 (1m): 在1分钟布林带上下轨进行反向阻击。
    3. 赢利点: 赌震荡回归均值。
    """
    
    def __init__(self, data_file='ETHUSDT_1m_klines.json'):
        self.data_file = data_file
        self.klines_1m = []
        self.klines_10m = []
        
    def load_data(self):
        """加载1分钟K线数据"""
        if os.path.exists(self.data_file):
            print(f"正在加载数据 {self.data_file}...")
            with open(self.data_file, 'r', encoding='utf-8') as f:
                self.klines_1m = json.load(f)
            print(f"成功加载 {len(self.klines_1m)} 条1分钟K线数据")
            
            # 转换数据格式为更易处理的字典列表 (如果需要)
            # 假设数据已经是 list of dicts
        else:
            print(f"数据文件 {self.data_file} 不存在!")
            return False
        return True

    def resample_to_10m(self):
        """将1m数据重采样为10m数据"""
        print("正在重采样生成10分钟K线...")
        self.klines_10m = []
        
        current_10m_kline = None
        
        for k in self.klines_1m:
            # 解析时间
            dt = datetime.strptime(k['datetime'], '%Y-%m-%d %H:%M:%S')
            # 找到所属的10分钟整点 (例如 08:03 -> 08:00)
            minute_floor = (dt.minute // 10) * 10
            k_10m_time = dt.replace(minute=minute_floor, second=0)
            k_10m_time_str = k_10m_time.strftime('%Y-%m-%d %H:%M:%S')
            
            if current_10m_kline is None or current_10m_kline['datetime'] != k_10m_time_str:
                # 保存上一个完成的10m K线
                if current_10m_kline:
                    self.klines_10m.append(current_10m_kline)
                
                # 开始新的10m K线
                current_10m_kline = {
                    'datetime': k_10m_time_str,
                    'open': k['open'],
                    'high': k['high'],
                    'low': k['low'],
                    'close': k['close'],
                    'volume': k['volume'],
                    'count': 1,
                    'start_idx': self.klines_1m.index(k) # 记录对应1m数据的起始索引
                }
            else:
                # 更新当前10m K线
                current_10m_kline['high'] = max(current_10m_kline['high'], k['high'])
                current_10m_kline['low'] = min(current_10m_kline['low'], k['low'])
                current_10m_kline['close'] = k['close']
                current_10m_kline['volume'] += k['volume']
                current_10m_kline['count'] += 1
        
        # 添加最后一个
        if current_10m_kline:
            self.klines_10m.append(current_10m_kline)
            
        print(f"生成了 {len(self.klines_10m)} 条10分钟K线")

    def calculate_bollinger_bands(self, period=20, std_dev=2):
        """计算1分钟数据的布林带"""
        print("正在计算1分钟布林带...")
        closes = [k['close'] for k in self.klines_1m]
        
        for i in range(len(self.klines_1m)):
            if i < period - 1:
                self.klines_1m[i]['bb_upper'] = None
                self.klines_1m[i]['bb_middle'] = None
                self.klines_1m[i]['bb_lower'] = None
                continue
            
            slice_closes = closes[i - period + 1 : i + 1]
            ma = sum(slice_closes) / period
            variance = sum([((x - ma) ** 2) for x in slice_closes]) / period
            std = math.sqrt(variance)
            
            self.klines_1m[i]['bb_middle'] = ma
            self.klines_1m[i]['bb_upper'] = ma + (std * std_dev)
            self.klines_1m[i]['bb_lower'] = ma - (std * std_dev)

    def is_dead_fish(self, kline_10m, body_threshold=0.0005, amplitude_threshold=0.002):
        """
        判断10m K线是否为'死鱼'(一字排开)
        1. 实体极小: Abs(Close - Open) / Open < body_threshold
        2. 振幅极小: (High - Low) / Open < amplitude_threshold
        """
        open_price = kline_10m['open']
        body_percent = abs(kline_10m['close'] - open_price) / open_price
        amplitude_percent = (kline_10m['high'] - kline_10m['low']) / open_price
        
        return body_percent < body_threshold and amplitude_percent < amplitude_threshold

    def backtest(self, flat_threshold=0.0005, amplitude_threshold=0.002, stop_loss_pct=0.002, take_profit_at_mean=True):
        """
        回测策略
        flat_threshold: 10mK线实体阈值
        amplitude_threshold: 10mK线振幅阈值
        stop_loss_pct: 止损百分比
        """
        # print(f"开始回测: 实体阈值={flat_threshold*100}%, 振幅阈值={amplitude_threshold*100}%, 止损={stop_loss_pct*100}%")
        
        trades = []
        active_trade = None
        
        # 预处理：标记每个10m K线是否为Flat
        flat_status = [self.is_dead_fish(k, flat_threshold, amplitude_threshold) for k in self.klines_10m]
        
        # 建立时间映射
        map_10m = {k['datetime']: idx for idx, k in enumerate(self.klines_10m)}
        
        for i in range(20, len(self.klines_1m)):
            k1m = self.klines_1m[i]
            dt = datetime.strptime(k1m['datetime'], '%Y-%m-%d %H:%M:%S')
            
            # 找到当前时间所属的10m区间的前一个区间索引
            # 例如当前 08:05, 所属 08:00-08:10, 我们需要看 07:50-08:00 的状态
            # 或者更严格：看最近N个10m是否都是Flat
            
            # 计算对应的10m索引
            # 这是一个近似计算，假设数据连续
            # 更准确的方法是二分查找时间，这里简化处理
            # 我们用 k1m 的时间去匹配 klines_10m
            
            # 简单逻辑：找到当前时刻之前最近的一个已完成的10m K线
            # 10m K线列表是按时间排序的
            # 我们可以维护一个指针
            
            # 实际上，策略逻辑是：宏观确认(10m图是横盘)。
            # 这意味着最近的一两个10m K线必须是Flat的。
            
            # 找到当前1m K线对应的10m K线索引
            # 10m K线的时间戳是该区间的开始时间
            minute_floor = (dt.minute // 10) * 10
            current_10m_start_time = dt.replace(minute=minute_floor, second=0)
            
            # 在 klines_10m 中找到这个时间
            # 为了效率，不每次都遍历。
            # 可以在外部维护一个 map: 10m_start_time_str -> index
            
            # 这里先用简单方法，假设数据量不大或者我们只做演示
            # 优化：预先建立映射
            pass 

        # 重新组织回测循环
        # 建立时间映射
        map_10m = {k['datetime']: idx for idx, k in enumerate(self.klines_10m)}
        
        for i in range(20, len(self.klines_1m)):
            k1m = self.klines_1m[i]
            
            # 如果有持仓，先处理平仓逻辑
            if active_trade:
                # 止损检查
                if active_trade['type'] == 'LONG':
                    if k1m['low'] < active_trade['stop_loss']:
                        # 止损触发
                        profit = active_trade['stop_loss'] - active_trade['entry_price']
                        active_trade['exit_price'] = active_trade['stop_loss']
                        active_trade['exit_time'] = k1m['datetime']
                        active_trade['profit'] = profit
                        active_trade['profit_pct'] = profit / active_trade['entry_price']
                        active_trade['reason'] = 'Stop Loss'
                        trades.append(active_trade)
                        active_trade = None
                        continue
                    # 止盈检查 (回归中轨)
                    elif take_profit_at_mean and k1m['high'] >= k1m['bb_middle']:
                        # 触碰中轨止盈
                        exit_price = k1m['bb_middle']
                        profit = exit_price - active_trade['entry_price']
                        active_trade['exit_price'] = exit_price
                        active_trade['exit_time'] = k1m['datetime']
                        active_trade['profit'] = profit
                        active_trade['profit_pct'] = profit / active_trade['entry_price']
                        active_trade['reason'] = 'Take Profit (Mean)'
                        trades.append(active_trade)
                        active_trade = None
                        continue
                        
                elif active_trade['type'] == 'SHORT':
                    if k1m['high'] > active_trade['stop_loss']:
                        # 止损触发
                        profit = active_trade['entry_price'] - active_trade['stop_loss']
                        active_trade['exit_price'] = active_trade['stop_loss']
                        active_trade['exit_time'] = k1m['datetime']
                        active_trade['profit'] = profit
                        active_trade['profit_pct'] = profit / active_trade['entry_price']
                        active_trade['reason'] = 'Stop Loss'
                        trades.append(active_trade)
                        active_trade = None
                        continue
                    # 止盈检查 (回归中轨)
                    elif take_profit_at_mean and k1m['low'] <= k1m['bb_middle']:
                        # 触碰中轨止盈
                        exit_price = k1m['bb_middle']
                        profit = active_trade['entry_price'] - exit_price
                        active_trade['exit_price'] = exit_price
                        active_trade['exit_time'] = k1m['datetime']
                        active_trade['profit'] = profit
                        active_trade['profit_pct'] = profit / active_trade['entry_price']
                        active_trade['reason'] = 'Take Profit (Mean)'
                        trades.append(active_trade)
                        active_trade = None
                        continue
                
                # 超时强制平仓 (例如持仓超过30分钟)
                # 这里暂不实现，遵循"回归均值"逻辑
            
            # 开仓逻辑
            if active_trade is None:
                # 1. 检查宏观环境 (10m)
                dt = datetime.strptime(k1m['datetime'], '%Y-%m-%d %H:%M:%S')
                minute_floor = (dt.minute // 10) * 10
                current_10m_start_time_str = dt.replace(minute=minute_floor, second=0).strftime('%Y-%m-%d %H:%M:%S')
                
                current_10m_idx = map_10m.get(current_10m_start_time_str)
                
                if current_10m_idx is not None and current_10m_idx > 0:
                    # 检查前一个10m K线是否为Flat
                    # 策略要求：看到"一字排开"，意味着至少前一个，或者前两个是Flat
                    prev_10m_idx = current_10m_idx - 1
                    
                    # 严格模式：前两个10m都是Flat
                    if prev_10m_idx >= 1:
                        is_flat_1 = flat_status[prev_10m_idx]
                        is_flat_2 = flat_status[prev_10m_idx - 1]
                        
                        if is_flat_1 and is_flat_2:
                            # 宏观环境符合：死鱼盘
                            
                            # 2. 微观触发 (1m BB)
                            bb_lower = k1m['bb_lower']
                            bb_upper = k1m['bb_upper']
                            
                            if bb_lower is None: continue
                            
                            # 做多信号：触碰下轨
                            if k1m['low'] <= bb_lower:
                                active_trade = {
                                    'type': 'LONG',
                                    'entry_time': k1m['datetime'],
                                    'entry_price': bb_lower, # 假设在下轨成交
                                    'stop_loss': bb_lower * (1 - stop_loss_pct),
                                    'bb_middle_at_entry': k1m['bb_middle']
                                }
                            
                            # 做空信号：触碰上轨
                            elif k1m['high'] >= bb_upper:
                                active_trade = {
                                    'type': 'SHORT',
                                    'entry_time': k1m['datetime'],
                                    'entry_price': bb_upper, # 假设在上轨成交
                                    'stop_loss': bb_upper * (1 + stop_loss_pct),
                                    'bb_middle_at_entry': k1m['bb_middle']
                                }

        return trades

    def print_stats(self, trades):
        if not trades:
            print("没有产生交易")
            return
            
        total_trades = len(trades)
        wins = [t for t in trades if t['profit'] > 0]
        losses = [t for t in trades if t['profit'] <= 0]
        
        win_rate = len(wins) / total_trades * 100
        total_profit = sum(t['profit'] for t in trades)
        
        print("-" * 50)
        print(f"交易统计结果")
        print("-" * 50)
        print(f"总交易数: {total_trades}")
        print(f"胜率: {win_rate:.2f}%")
        print(f"总盈亏: {total_profit:.2f} USDT")
        print(f"平均盈亏: {total_profit/total_trades:.4f} USDT")
        print("-" * 50)
        
        # 导出详情
        with open('wick_sniper_trades.txt', 'w', encoding='utf-8') as f:
            f.write("entry_time,type,entry_price,exit_price,profit,profit_pct,reason\n")
            for t in trades:
                f.write(f"{t['entry_time']},{t['type']},{t['entry_price']:.2f},{t['exit_price']:.2f},{t['profit']:.2f},{t['profit_pct']*100:.2f}%,{t['reason']}\n")
        print("详细交易记录已保存至 wick_sniper_trades.txt")

if __name__ == "__main__":
    strategy = WickSniperStrategy()
    if strategy.load_data():
        strategy.resample_to_10m()
        strategy.calculate_bollinger_bands()
        
        print("\n开始参数优化...")
        print("="*60)
        
        best_profit = -float('inf')
        best_params = {}
        
        # 参数范围
        amp_thresholds = [0.001, 0.0015, 0.002, 0.0025, 0.003] # 0.1% - 0.3%
        stop_losses = [0.001, 0.0015, 0.002, 0.0025, 0.003]    # 0.1% - 0.3%
        
        results = []
        
        for amp in amp_thresholds:
            for sl in stop_losses:
                trades = strategy.backtest(flat_threshold=0.0005, amplitude_threshold=amp, stop_loss_pct=sl)
                
                if not trades:
                    continue
                    
                total_profit = sum(t['profit'] for t in trades)
                win_rate = len([t for t in trades if t['profit'] > 0]) / len(trades) * 100
                
                results.append({
                    'amp': amp,
                    'sl': sl,
                    'profit': total_profit,
                    'win_rate': win_rate,
                    'trades': len(trades)
                })
                
                if total_profit > best_profit:
                    best_profit = total_profit
                    best_params = {'amp': amp, 'sl': sl, 'trades': len(trades), 'win_rate': win_rate}
                    
        # 按利润排序输出前10个
        results.sort(key=lambda x: x['profit'], reverse=True)
        
        print(f"{'振幅阈值':<10} {'止损':<10} {'总盈亏':<15} {'胜率':<10} {'交易数':<10}")
        print("-" * 60)
        for r in results[:10]:
            print(f"{r['amp']*100:.2f}%     {r['sl']*100:.2f}%     {r['profit']:<15.2f} {r['win_rate']:.2f}%     {r['trades']}")
            
        print("=" * 60)
        print(f"最佳参数: 振幅阈值={best_params['amp']*100:.2f}%, 止损={best_params['sl']*100:.2f}%")
        print(f"最佳结果: 盈亏={best_profit:.2f} USDT, 胜率={best_params['win_rate']:.2f}%, 交易数={best_params['trades']}")
        
        # 使用最佳参数运行一次并保存详情
        final_trades = strategy.backtest(flat_threshold=0.0005, amplitude_threshold=best_params['amp'], stop_loss_pct=best_params['sl'])
        strategy.print_stats(final_trades)
