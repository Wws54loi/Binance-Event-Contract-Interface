import json
import os
from datetime import datetime
import requests


class BinanceKlineAnalyzer:
    """币安K线数据获取与压力位支撑位横盘识别"""
    
    def __init__(self, symbol='ETHUSDT', interval='1m', limit=50000):
        self.symbol = symbol
        self.interval = interval
        self.limit = limit
        self.data_file = f'{symbol}_{interval}_klines.json'
        self.klines = []
        
    def fetch_klines_from_binance(self):
        """从币安获取K线数据"""
        print(f"正在从币安合约获取 {self.symbol} {self.interval} 的K线数据...")
        
        url = 'https://fapi.binance.com/fapi/v1/klines'
        all_klines = []
        
        # 币安API单次最多返回1000条,需要分批获取
        batch_size = 1000
        batches = (self.limit + batch_size - 1) // batch_size
        
        for i in range(batches):
            current_limit = min(batch_size, self.limit - len(all_klines))
            params = {
                'symbol': self.symbol,
                'interval': self.interval,
                'limit': current_limit
            }
            
            # 如果不是第一批,设置endTime为上一批的最早时间
            if all_klines:
                params['endTime'] = all_klines[0][0] - 1
            
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                batch_data = response.json()
                
                if not batch_data:
                    break
                    
                # 将新数据插入到开头(因为我们是向历史回溯)
                all_klines = batch_data + all_klines
                print(f"已获取 {len(all_klines)}/{self.limit} 条数据")
                
                if len(all_klines) >= self.limit:
                    break
                    
            except Exception as e:
                print(f"获取数据出错: {e}")
                break
        
        # 格式化数据
        formatted_klines = []
        for k in all_klines[:self.limit]:
            formatted_klines.append({
                'open_time': k[0],
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
                'close_time': k[6],
                'quote_volume': float(k[7]),
                'trades': k[8],
                'datetime': datetime.fromtimestamp(k[0]/1000).strftime('%Y-%m-%d %H:%M:%S')
            })
        
        return formatted_klines
    
    def save_to_file(self, data):
        """保存数据到本地文件"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"数据已保存到 {self.data_file}")
    
    def load_from_file(self):
        """从本地文件加载数据"""
        if os.path.exists(self.data_file):
            print(f"从本地文件 {self.data_file} 加载数据...")
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def get_klines(self):
        """获取K线数据(优先从本地加载)"""
        # 先尝试从本地加载
        self.klines = self.load_from_file()
        
        # 如果本地没有,则从币安获取并保存
        if self.klines is None:
            self.klines = self.fetch_klines_from_binance()
            self.save_to_file(self.klines)
        else:
            print(f"成功加载 {len(self.klines)} 条K线数据")
        
        return self.klines
    
    def find_consolidation_by_support_resistance(self, touch_threshold=0.3, min_touches=2, max_klines_between=50, min_duration=20, min_amplitude_percent=0.5):
        """
        通过压力位和支撑位的反复触碰识别横盘区域
        
        原理:
        1. 寻找局部高点作为压力位
        2. 寻找局部低点作为支撑位
        3. 如果价格在一定区间内多次触碰压力位和支撑位，则认为是横盘
        
        参数:
            touch_threshold: 触碰阈值(百分比)，价格在此范围内认为触碰到位
            min_touches: 最小触碰次数，每个位至少要触碰这么多次
            max_klines_between: 两次触碰之间的最大K线数
            min_duration: 横盘区域的最小持续时间
            min_amplitude_percent: 压力位和支撑位之间的最小振幅百分比（避免区间太小）
        """
        if len(self.klines) < 10:
            return []
        
        consolidation_zones = []
        i = 0
        
        while i < len(self.klines) - min_duration:
            # 寻找局部高点(可能的压力位)
            if i < 2 or i >= len(self.klines) - 2:
                i += 1
                continue
            
            current = self.klines[i]
            
            # 判断是否为局部高点
            is_local_high = (current['high'] >= self.klines[i-1]['high'] and 
                            current['high'] >= self.klines[i-2]['high'] and
                            current['high'] >= self.klines[i+1]['high'] and
                            current['high'] >= self.klines[i+2]['high'] if i+2 < len(self.klines) else True)
            
            if is_local_high:
                resistance = current['high']
                resistance_touches = [i]  # 记录触碰压力位的位置
                
                # 向后查找支撑位和其他触碰（必须交替触碰）
                j = i + 1
                support = None
                support_touches = []
                touch_sequence = []  # 记录触碰顺序: ('resistance', index) 或 ('support', index)
                
                while j < len(self.klines) and j - i < max_klines_between * 3:
                    k = self.klines[j]
                    
                    # 检查是否触碰压力位
                    is_touch_resistance = abs(k['high'] - resistance) / resistance * 100 <= touch_threshold
                    
                    # 寻找支撑位(局部低点)
                    is_local_low = False
                    if j >= 2 and j < len(self.klines) - 2:
                        is_local_low = (k['low'] <= self.klines[j-1]['low'] and 
                                       k['low'] <= self.klines[j-2]['low'] and
                                       k['low'] <= self.klines[j+1]['low'] and
                                       k['low'] <= self.klines[j+2]['low'] if j+2 < len(self.klines) else True)
                        
                        if is_local_low and support is None:
                            support = k['low']
                    
                    # 检查是否触碰支撑位
                    is_touch_support = False
                    if support is not None:
                        is_touch_support = abs(k['low'] - support) / support * 100 <= touch_threshold
                    
                    # 记录触碰（必须与上一次触碰类型不同）
                    if is_touch_resistance:
                        if not touch_sequence or touch_sequence[-1][0] != 'resistance':
                            resistance_touches.append(j)
                            touch_sequence.append(('resistance', j))
                    elif is_touch_support:
                        if not touch_sequence or touch_sequence[-1][0] != 'support':
                            support_touches.append(j)
                            touch_sequence.append(('support', j))
                    
                    j += 1
                
                # 判断是否形成横盘区域
                if (support is not None and 
                    len(resistance_touches) >= min_touches and 
                    len(support_touches) >= min_touches):
                    
                    # 检查压力位和支撑位的振幅是否足够（避免区间太小）
                    amplitude_percent = ((resistance - support) / support) * 100
                    if amplitude_percent < min_amplitude_percent:
                        # 振幅太小，跳过这个区域
                        i += 1
                        continue
                    
                    # 确定横盘区域的起止位置
                    all_touches = sorted(resistance_touches + support_touches)
                    start_idx = all_touches[0]
                    end_idx = all_touches[-1]
                    
                    duration = end_idx - start_idx + 1
                    
                    # 检查持续时间是否足够
                    if duration >= min_duration:
                        # 计算区域内的详细信息
                        zone_klines = self.klines[start_idx:end_idx + 1]
                        zone_high = max(k['high'] for k in zone_klines)
                        zone_low = min(k['low'] for k in zone_klines)
                        zone_center = (zone_high + zone_low) / 2
                        zone_amplitude = zone_high - zone_low
                        zone_amplitude_percent = (zone_amplitude / zone_center) * 100
                        
                        # 找到可以开始交易的时间点：前3次触碰完成后，第4次触碰就可以交易
                        # 取前3次触碰中最晚的那个作为起点，这样第4次触碰时就能进行交易
                        # 计算前3次触碰的索引
                        touch_sequence_for_start = []
                        if len(resistance_touches) >= 1:
                            touch_sequence_for_start.append(('resistance', resistance_touches[0]))
                        if len(support_touches) >= 1:
                            touch_sequence_for_start.append(('support', support_touches[0]))
                        if len(resistance_touches) >= 2:
                            touch_sequence_for_start.append(('resistance', resistance_touches[1]))
                        if len(support_touches) >= 2:
                            touch_sequence_for_start.append(('support', support_touches[1]))
                        
                        # 按时间排序，取前3次中最晚的
                        touch_sequence_for_start.sort(key=lambda x: x[1])
                        trade_start_index = touch_sequence_for_start[2][1] if len(touch_sequence_for_start) >= 3 else start_idx
                        
                        consolidation_zones.append({
                            'start_index': start_idx,
                            'end_index': end_idx,
                            'trade_start_index': trade_start_index,  # 新增：可以开始交易的索引
                            'start_time': self.klines[start_idx]['datetime'],
                            'end_time': self.klines[end_idx]['datetime'],
                            'duration': duration,
                            'resistance': round(resistance, 2),
                            'support': round(support, 2),
                            'resistance_touches': len(resistance_touches),
                            'support_touches': len(support_touches),
                            'resistance_touch_indices': resistance_touches,  # 新增：记录所有触碰位置
                            'support_touch_indices': support_touches,  # 新增：记录所有触碰位置
                            'high': round(zone_high, 2),
                            'low': round(zone_low, 2),
                            'center': round(zone_center, 2),
                            'amplitude': round(zone_amplitude, 2),
                            'amplitude_percent': round(zone_amplitude_percent, 2)
                        })
                        
                        # 跳过已识别的区域
                        i = end_idx + 1
                        continue
            
            i += 1
        
        return consolidation_zones
    
    def backtest_strategy(self, consolidation_zones, hold_periods=10, max_positions=5, touch_threshold=0.001):
        """
        回测交易策略
        
        策略:
        1. 识别到横盘区域后(压力位和支撑位各触碰2次)
        2. 每次触碰压力位时做空，触碰支撑位时做多
        3. 持有10根K线后判断盈亏
        4. 同时最多持仓5笔
        
        参数:
            consolidation_zones: 横盘区域列表
            hold_periods: 持仓周期(K线数量)
            max_positions: 最大同时持仓数量
            touch_threshold: 触碰阈值(默认0.003即0.3%)
        """
        all_trades = []
        active_positions = []  # 当前持仓列表: [(entry_index, exit_index, trade_data), ...]
        
        # 创建所有潜在交易机会的时间序列
        potential_trades = []
        
        for zone in consolidation_zones:
            resistance = zone['resistance']
            support = zone['support']
            start_idx = zone['start_index']
            end_idx = zone['end_index']
            trade_start_idx = zone['trade_start_index']  # 横盘确认后才能开始交易
            
            # 遍历该横盘区域内的K线（从trade_start_idx的下一根K线开始，这样第4次触碰就能交易）
            for i in range(trade_start_idx + 1, end_idx + 1):
                if i + hold_periods >= len(self.klines):
                    break
                
                current_k = self.klines[i]
                
                # 检查是否触碰压力位 - 做空（使用实际触碰的高点作为入场价）
                # 必须是从下往上触碰：前一根K线的收盘价低于压力位
                if abs(current_k['high'] - resistance) / resistance <= touch_threshold:
                    if i > 0 and self.klines[i-1]['close'] < resistance:
                        potential_trades.append({
                            'entry_index': i,
                            'exit_index': i + hold_periods,
                            'zone_id': consolidation_zones.index(zone) + 1,
                            'type': 'SHORT',
                            'entry_price': current_k['high'],  # 使用实际触碰的高点
                            'resistance': resistance,
                            'support': support
                        })
                
                # 检查是否触碰支撑位 - 做多（使用实际触碰的低点作为入场价）
                # 必须是从上往下触碰：前一根K线的收盘价高于支撑位
                if abs(current_k['low'] - support) / support <= touch_threshold:
                    if i > 0 and self.klines[i-1]['close'] > support:
                        potential_trades.append({
                            'entry_index': i,
                            'exit_index': i + hold_periods,
                            'zone_id': consolidation_zones.index(zone) + 1,
                            'type': 'LONG',
                            'entry_price': current_k['low'],  # 使用实际触碰的低点
                            'resistance': resistance,
                            'support': support
                        })
        
        # 按入场时间排序
        potential_trades.sort(key=lambda x: x['entry_index'])
        
        # 遍历所有潜在交易，应用持仓限制和交替买入限制
        last_trade_type_per_zone = {}  # 记录每个区域的上一次交易类型 {zone_id: 'SHORT' or 'LONG'}
        
        for trade_opportunity in potential_trades:
            entry_idx = trade_opportunity['entry_index']
            exit_idx = trade_opportunity['exit_index']
            zone_id = trade_opportunity['zone_id']
            trade_type = trade_opportunity['type']
            
            # 清理已经平仓的持仓
            active_positions = [pos for pos in active_positions if pos['exit_index'] > entry_idx]
            
            # 检查交替买入限制：同一个区域内，不能连续两次做同样的交易
            if zone_id in last_trade_type_per_zone:
                if last_trade_type_per_zone[zone_id] == trade_type:
                    # 跳过：上一次已经做过相同类型的交易，必须等待相反类型
                    continue
            
            # 检查是否可以开新仓
            if len(active_positions) < max_positions:
                # 可以开仓
                entry_k = self.klines[entry_idx]
                exit_k = self.klines[exit_idx]
                
                entry_price = trade_opportunity['entry_price']
                entry_time = entry_k['datetime']
                exit_price = exit_k['close']
                exit_time = exit_k['datetime']
                
                if trade_type == 'SHORT':
                    # 做空: 入场价格 > 出场价格 = 盈利
                    profit = entry_price - exit_price
                    profit_percent = (profit / entry_price) * 100
                    is_win = profit > 0
                    reason = f"触碰压力位 {trade_opportunity['resistance']}"
                else:  # LONG
                    # 做多: 出场价格 > 入场价格 = 盈利
                    profit = exit_price - entry_price
                    profit_percent = (profit / entry_price) * 100
                    is_win = profit > 0
                    reason = f"触碰支撑位 {trade_opportunity['support']}"
                
                trade_data = {
                    'zone_id': zone_id,
                    'type': trade_type,
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_price': round(entry_price, 2),
                    'exit_price': round(exit_price, 2),
                    'profit': round(profit, 2),
                    'profit_percent': round(profit_percent, 3),
                    'is_win': is_win,
                    'reason': reason,
                    'entry_index': entry_idx,
                    'exit_index': exit_idx
                }
                
                all_trades.append(trade_data)
                active_positions.append({
                    'exit_index': exit_idx,
                    'trade': trade_data
                })
                
                # 更新该区域的上一次交易类型
                last_trade_type_per_zone[zone_id] = trade_type
        
        return all_trades
    
    def export_trades_to_file(self, trades, consolidation_zones):
        """导出详细交易记录到文件，便于复验"""
        export_file = f'{self.symbol}_{self.interval}_trades_detail.txt'
        
        try:
            with open(export_file, 'w', encoding='utf-8') as f:
                f.write("="*80 + "\n")
                f.write(f"交易详细记录 - {self.symbol} {self.interval}\n")
                f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*80 + "\n\n")
                
                # 横盘区域信息
                f.write("横盘区域信息:\n")
                f.write("-"*80 + "\n")
                for idx, zone in enumerate(consolidation_zones, 1):
                    f.write(f"\n区域 #{idx}:\n")
                    f.write(f"  时间范围: {zone['start_time']} 至 {zone['end_time']}\n")
                    f.write(f"  压力位: {zone['resistance']} (触碰 {zone['resistance_touches']} 次)\n")
                    f.write(f"  支撑位: {zone['support']} (触碰 {zone['support_touches']} 次)\n")
                    f.write(f"  振幅: {zone['amplitude']} ({zone['amplitude_percent']}%)\n")
                    f.write(f"  持续: {zone['duration']} 根K线\n")
                    
                    # 显示形成横盘的关键触碰时间线(前4次:压力2次+支撑2次)
                    f.write(f"\n  形成横盘的关键触碰时间线:\n")
                    resistance_indices = zone['resistance_touch_indices'][:2]  # 前2次压力位触碰
                    support_indices = zone['support_touch_indices'][:2]  # 前2次支撑位触碰
                    
                    for i, r_idx in enumerate(resistance_indices, 1):
                        r_k = self.klines[r_idx]
                        f.write(f"    压力位第{i}次触碰: {r_k['datetime']} (索引:{r_idx}, 高:{r_k['high']:.2f})\n")
                    
                    for i, s_idx in enumerate(support_indices, 1):
                        s_k = self.klines[s_idx]
                        f.write(f"    支撑位第{i}次触碰: {s_k['datetime']} (索引:{s_idx}, 低:{s_k['low']:.2f})\n")
                
                # 所有交易详情
                f.write("\n\n" + "="*80 + "\n")
                f.write(f"所有交易记录 (共 {len(trades)} 笔)\n")
                f.write("="*80 + "\n\n")
                
                for i, trade in enumerate(trades, 1):
                    result = "✅ 胜" if trade['is_win'] else "❌ 负"
                    f.write(f"{'='*80}\n")
                    f.write(f"交易 #{i} - {trade['type']} {result}\n")
                    f.write(f"{'='*80}\n")
                    f.write(f"所属区域: #{trade['zone_id']}\n")
                    f.write(f"触发原因: {trade['reason']}\n")
                    f.write(f"\n入场信息:\n")
                    f.write(f"  时间: {trade['entry_time']}\n")
                    f.write(f"  价格: {trade['entry_price']}\n")
                    f.write(f"  K线索引: {trade['entry_index']}\n")
                    f.write(f"\n出场信息:\n")
                    f.write(f"  时间: {trade['exit_time']}\n")
                    f.write(f"  价格: {trade['exit_price']}\n")
                    f.write(f"  K线索引: {trade['exit_index']}\n")
                    f.write(f"\n交易结果:\n")
                    f.write(f"  持仓: {trade['exit_index'] - trade['entry_index']} 根K线 (10分钟)\n")
                    f.write(f"  盈亏: {trade['profit']:+.2f} USDT ({trade['profit_percent']:+.3f}%)\n")
                    f.write(f"  预期方向: {'价格下跌' if trade['type'] == 'SHORT' else '价格上涨'}\n")
                    f.write(f"  实际走势: {'下跌' if trade['profit'] > 0 and trade['type'] == 'SHORT' else '上涨' if trade['profit'] > 0 and trade['type'] == 'LONG' else '反向'}\n")
                    f.write(f"\n复验数据:\n")
                    f.write(f"  入场K线时间戳: {self.klines[trade['entry_index']]['open_time']}\n")
                    f.write(f"  出场K线时间戳: {self.klines[trade['exit_index']]['open_time']}\n")
                    
                    # 显示入场和出场K线的详细信息
                    entry_k = self.klines[trade['entry_index']]
                    exit_k = self.klines[trade['exit_index']]
                    f.write(f"\n  入场K线: 开{entry_k['open']:.2f} 高{entry_k['high']:.2f} 低{entry_k['low']:.2f} 收{entry_k['close']:.2f}\n")
                    f.write(f"  出场K线: 开{exit_k['open']:.2f} 高{exit_k['high']:.2f} 低{exit_k['low']:.2f} 收{exit_k['close']:.2f}\n")
                    f.write("\n")
                
                # 统计摘要
                stats = self.calculate_statistics(trades)
                if stats:
                    f.write("\n" + "="*80 + "\n")
                    f.write("统计摘要\n")
                    f.write("="*80 + "\n")
                    f.write(f"总交易次数: {stats['total_trades']}\n")
                    f.write(f"  做多: {stats['long_trades']} 次 (胜率 {stats['long_win_rate']}%)\n")
                    f.write(f"  做空: {stats['short_trades']} 次 (胜率 {stats['short_win_rate']}%)\n")
                    f.write(f"\n胜率: {stats['win_rate']}% ({stats['win_count']}胜 / {stats['loss_count']}负)\n")
                    f.write(f"总盈亏: {stats['total_profit']:+.2f} USDT\n")
                    f.write(f"平均盈亏: {stats['avg_profit']:+.2f} USDT\n")
                    f.write(f"平均盈利: {stats['avg_win']:+.2f} USDT\n")
                    f.write(f"平均亏损: {stats['avg_loss']:+.2f} USDT\n")
                
            print(f"\n✓ 详细交易记录已导出到: {export_file}")
            print(f"  包含 {len(trades)} 笔交易的完整信息，可用于人工复验")
            
        except Exception as e:
            print(f"\n⚠ 导出交易记录失败: {e}")
    
    def calculate_statistics(self, trades):
        """计算交易统计数据"""
        if not trades:
            return None
        
        total_trades = len(trades)
        winning_trades = [t for t in trades if t['is_win']]
        losing_trades = [t for t in trades if not t['is_win']]
        
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0
        
        # 按交易类型统计
        long_trades = [t for t in trades if t['type'] == 'LONG']
        short_trades = [t for t in trades if t['type'] == 'SHORT']
        
        long_wins = len([t for t in long_trades if t['is_win']])
        short_wins = len([t for t in short_trades if t['is_win']])
        
        long_win_rate = (long_wins / len(long_trades)) * 100 if long_trades else 0
        short_win_rate = (short_wins / len(short_trades)) * 100 if short_trades else 0
        
        # 计算盈亏
        total_profit = sum(t['profit'] for t in trades)
        avg_profit = total_profit / total_trades if total_trades > 0 else 0
        avg_win = sum(t['profit'] for t in winning_trades) / win_count if win_count > 0 else 0
        avg_loss = sum(t['profit'] for t in losing_trades) / loss_count if loss_count > 0 else 0
        
        # 统计连续亏损次数
        consecutive_losses = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        current_loss_streak = 0
        
        for trade in trades:
            if not trade['is_win']:
                current_loss_streak += 1
            else:
                # 遇到盈利，记录之前的连续亏损
                if current_loss_streak > 0:
                    if current_loss_streak <= 5:
                        consecutive_losses[current_loss_streak] += 1
                    else:
                        # 超过5次的也计入5次
                        consecutive_losses[5] += 1
                current_loss_streak = 0
        
        # 处理最后如果还有未记录的连续亏损
        if current_loss_streak > 0:
            if current_loss_streak <= 5:
                consecutive_losses[current_loss_streak] += 1
            else:
                consecutive_losses[5] += 1
        
        return {
            'total_trades': total_trades,
            'win_count': win_count,
            'loss_count': loss_count,
            'win_rate': round(win_rate, 2),
            'long_trades': len(long_trades),
            'long_wins': long_wins,
            'long_win_rate': round(long_win_rate, 2),
            'short_trades': len(short_trades),
            'short_wins': short_wins,
            'short_win_rate': round(short_win_rate, 2),
            'total_profit': round(total_profit, 2),
            'avg_profit': round(avg_profit, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'consecutive_losses': consecutive_losses
        }
    
    def analyze(self):
        """执行完整的分析流程"""
        print("="*60)
        print("币安K线数据与压力位支撑位横盘识别")
        print("="*60)
        
        # 1. 获取K线数据
        self.get_klines()
        print(f"\n总共获取 {len(self.klines)} 条K线数据")
        print(f"时间范围: {self.klines[0]['datetime']} 至 {self.klines[-1]['datetime']}")
        
        # 2. 使用压力位支撑位识别横盘区域
        print("\n正在识别横盘区域...")
        print("识别原理:")
        print("  ① 寻找局部高点作为压力位")
        print("  ② 寻找局部低点作为支撑位")
        print("  ③ 价格在压力位和支撑位之间反复触碰 = 横盘")
        print("\n参数设置:")
        print("  - 触碰阈值: 价格偏离 < 0.3%")
        print("  - 最小触碰次数: 每个位至少 2 次")
        print("  - 最小持续时间: 20 根K线")
        print("  - 最小振幅: 压力位和支撑位差距 ≥ 0.5%")
        
        consolidation_zones = self.find_consolidation_by_support_resistance(
            touch_threshold=0.1,      # 0.3%的触碰阈值
            min_touches=2,            # 至少触碰2次
            max_klines_between=50,    # 触碰间隔不超过50根K线
            min_duration=20,          # 至少持续20根K线
            min_amplitude_percent=0.5 # 最小振幅0.5%
        )
        
        print(f"\n识别到 {len(consolidation_zones)} 个横盘盘整区域")
        
        # 3. 回测交易策略
        print("\n" + "="*60)
        print("开始回测交易策略...")
        print("="*60)
        print("策略规则:")
        print("  ① 在横盘区域内交易")
        print("  ② 触碰压力位时做空")
        print("  ③ 触碰支撑位时做多")
        print("  ④ 持仓10根K线后平仓")
        print("  ⑤ 同时最多持仓5笔")
        print("  ⑥ 同一区域必须交替买入(压力→支撑→压力)")
        print("  ⑦ 做空盈利条件: 10根K线后价格 < 入场价")
        print("  ⑧ 做多盈利条件: 10根K线后价格 > 入场价")
        
        trades = self.backtest_strategy(consolidation_zones, hold_periods=10, max_positions=5, touch_threshold=0.0005)
        stats = self.calculate_statistics(trades)
        
        if stats:
            print("\n" + "="*60)
            print("回测统计结果:")
            print("="*60)
            print(f"\n总交易次数: {stats['total_trades']}")
            print(f"  做多交易: {stats['long_trades']} 次")
            print(f"  做空交易: {stats['short_trades']} 次")
            print(f"\n胜率统计:")
            print(f"  总胜率: {stats['win_rate']}% ({stats['win_count']}胜 / {stats['loss_count']}败)")
            print(f"  做多胜率: {stats['long_win_rate']}% ({stats['long_wins']}胜 / {stats['long_trades'] - stats['long_wins']}败)")
            print(f"  做空胜率: {stats['short_win_rate']}% ({stats['short_wins']}胜 / {stats['short_trades'] - stats['short_wins']}败)")
            print(f"\n盈亏统计:")
            print(f"  总盈亏: {stats['total_profit']} USDT")
            print(f"  平均盈亏: {stats['avg_profit']} USDT")
            print(f"  平均盈利: {stats['avg_win']} USDT")
            print(f"  平均亏损: {stats['avg_loss']} USDT")
            
            print(f"\n连续亏损统计:")
            print(f"  连续亏损1次: {stats['consecutive_losses'][1]} 次")
            print(f"  连续亏损2次: {stats['consecutive_losses'][2]} 次")
            print(f"  连续亏损3次: {stats['consecutive_losses'][3]} 次")
            print(f"  连续亏损4次: {stats['consecutive_losses'][4]} 次")
            print(f"  连续亏损5次及以上: {stats['consecutive_losses'][5]} 次")
            
            # 显示前10笔交易示例
            print("\n" + "="*60)
            print("前10笔交易详情:")
            print("="*60)
            for i, trade in enumerate(trades[:10], 1):
                result = "✓ 盈利" if trade['is_win'] else "✗ 亏损"
                print(f"\n交易 #{i} - {trade['type']} {result}")
                print(f"  盘整区域: #{trade['zone_id']}")
                print(f"  入场时间: {trade['entry_time']}")
                print(f"  出场时间: {trade['exit_time']}")
                print(f"  入场价格: {trade['entry_price']}")
                print(f"  出场价格: {trade['exit_price']}")
                print(f"  盈亏: {trade['profit']} ({trade['profit_percent']}%)")
            
            # 导出详细交易记录到文件
            self.export_trades_to_file(trades, consolidation_zones)
        else:
            print("\n未产生任何交易")
        
        # 4. 输出横盘区域详情(可选，简化显示)
        if False:  # 设置为False不显示详细区域信息
            print("\n" + "="*60)
            print("横盘盘整区域详细信息:")
            print("="*60)
            for idx, zone in enumerate(consolidation_zones, 1):
                print(f"\n盘整区域 #{idx}:")
                print(f"  时间范围: {zone['start_time']} 至 {zone['end_time']}")
                print(f"  持续时间: {zone['duration']} 根K线 (约 {zone['duration']} 分钟)")
                print(f"  压力位: {zone['resistance']} (触碰 {zone['resistance_touches']} 次)")
                print(f"  支撑位: {zone['support']} (触碰 {zone['support_touches']} 次)")
                print(f"  价格区间: {zone['low']} - {zone['high']}")
                print(f"  中心价格: {zone['center']}")
                print(f"  振幅: {zone['amplitude']} ({zone['amplitude_percent']}%)")
        else:
            print("\n未识别到横盘区域，可能需要调整参数")
        
        print("\n" + "="*60)
        print("分析完成!")
        print("="*60)
        
        return {
            'klines_count': len(self.klines),
            'consolidation_count': len(consolidation_zones),
            'consolidation_zones': consolidation_zones,
            'trades': trades if 'trades' in locals() else [],
            'statistics': stats if 'stats' in locals() else None
        }


if __name__ == '__main__':
    # 创建分析器并执行分析
    analyzer = BinanceKlineAnalyzer(symbol='ETHUSDT', interval='1m', limit=50000)
    result = analyzer.analyze()
