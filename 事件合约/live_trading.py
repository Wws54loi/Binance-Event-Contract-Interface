import json
import asyncio
import websockets
import time
import requests
from datetime import datetime
from collections import deque


class LiveTradingBot:
    """实时交易机器人 - 基于压力位支撑位横盘策略"""
    
    def __init__(self, symbol='ethusdt', interval='1m', touch_threshold=0.003):
        self.symbol = symbol.lower()
        self.interval = interval
        self.touch_threshold = touch_threshold  # 0.3%的触碰阈值
        
        # WebSocket连接
        self.ws_url = f'wss://fstream.binance.com/ws/{self.symbol}@kline_{self.interval}'
        
        # K线数据存储 (保留最近200根K线用于分析)
        self.klines = deque(maxlen=200)
        
        # 当前识别的压力位和支撑位
        self.resistance = None  # 压力位
        self.support = None     # 支撑位
        self.resistance_touches = []  # 压力位触碰时间列表
        self.support_touches = []     # 支撑位触碰时间列表
        
        # 盘整状态
        self.in_consolidation = False  # 是否处于盘整状态
        self.consolidation_start_time = None
        self.current_zone_id = None  # 当前盘整区域ID（用于识别区域变化）
        
        # 交易状态（按区域跟踪）
        self.last_trade_type_per_zone = {}  # 每个区域的上一次交易类型 {zone_id: 'LONG' or 'SHORT'}
        self.current_position = None  # 当前持仓 {'type': 'LONG', 'entry_price': xxx, 'entry_time': xxx}
        
        # 交易记录
        self.trades = []
        
        # 持仓管理（支持多个持仓）
        self.open_positions = []  # [{'type': 'LONG', 'entry_price': xxx, 'entry_timestamp': xxx, 'entry_time': 'xxx'}, ...]
        self.hold_seconds = 600  # 持仓600秒（10分钟）
        
        # 运行状态
        self.is_running = False
        
        # 交易日志文件
        self.log_file = f'{self.symbol}_{self.interval}_live_trades.txt'
        self.init_log_file()
    
    def init_log_file(self):
        """初始化交易日志文件"""
        try:
            with open(self.log_file, 'w', encoding='utf-8') as f:
                f.write("="*80 + "\n")
                f.write(f"实时交易日志 - {self.symbol.upper()} {self.interval}\n")
                f.write(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*80 + "\n\n")
            print(f"✓ 交易日志文件已创建: {self.log_file}")
        except Exception as e:
            print(f"⚠ 创建日志文件失败: {e}")
    
    def log_trade_entry(self, trade_type, price, entry_time, zone_id):
        """记录开仓日志"""
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write("\n" + "="*80 + "\n")
                f.write(f"🔔 开仓 - {trade_type}\n")
                f.write("="*80 + "\n")
                f.write(f"入场时间: {entry_time}\n")
                f.write(f"入场价格: {price:.2f}\n")
                f.write(f"所属区域: {zone_id}\n")
                f.write(f"当前持仓数: {len(self.open_positions)}\n")
                f.write(f"历史交易: {len(self.trades)} 笔\n")
                f.write("="*80 + "\n")
        except Exception as e:
            print(f"⚠ 写入开仓日志失败: {e}")
    
    def log_trade_exit(self, position, exit_price, exit_time, is_win, time_diff_seconds):
        """记录平仓日志"""
        try:
            price_change = exit_price - position['entry_price']
            price_change_pct = (price_change / position['entry_price']) * 100
            
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write("\n" + "="*80 + "\n")
                f.write(f"{'✅ 胜' if is_win else '❌ 负'} - 平仓 {position['type']}\n")
                f.write("="*80 + "\n")
                f.write(f"所属区域: {position.get('zone_id', 'N/A')}\n")
                f.write(f"\n入场信息:\n")
                f.write(f"  时间: {position['entry_time']}\n")
                f.write(f"  价格: {position['entry_price']:.2f}\n")
                f.write(f"\n出场信息:\n")
                f.write(f"  时间: {exit_time}\n")
                f.write(f"  价格: {exit_price:.2f}\n")
                f.write(f"\n交易结果:\n")
                f.write(f"  持仓时长: {int(time_diff_seconds)} 秒 ({int(time_diff_seconds/60)} 分 {int(time_diff_seconds%60)} 秒)\n")
                f.write(f"  价格变动: {price_change:+.2f} ({price_change_pct:+.2f}%)\n")
                f.write(f"  预期方向: {'上涨' if position['type'] == 'LONG' else '下跌'}\n")
                f.write(f"  实际走势: {'上涨' if price_change > 0 else '下跌' if price_change < 0 else '不变'}\n")
                f.write(f"  判定结果: {'胜利 ✅' if is_win else '失败 ❌'}\n")
                
                # 统计当前胜率
                closed_trades = [t for t in self.trades if t['status'] == 'CLOSED']
                if closed_trades:
                    winning_trades = [t for t in closed_trades if t.get('is_win', False)]
                    win_rate = (len(winning_trades) / len(closed_trades)) * 100
                    f.write(f"\n当前统计:\n")
                    f.write(f"  已平仓: {len(closed_trades)} 笔\n")
                    f.write(f"  胜率: {win_rate:.2f}% ({len(winning_trades)}胜/{len(closed_trades)-len(winning_trades)}负)\n")
                
                f.write("="*80 + "\n")
        except Exception as e:
            print(f"⚠ 写入平仓日志失败: {e}")
    
    def fetch_historical_klines(self, limit=50):
        """获取历史K线数据"""
        try:
            url = 'https://fapi.binance.com/fapi/v1/klines'
            params = {
                'symbol': self.symbol.upper(),
                'interval': self.interval,
                'limit': limit
            }
            
            print(f"正在获取历史K线数据 (最近{limit}根)...")
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            klines_data = response.json()
            
            # 转换格式并添加到队列（排除最后一根未完成的K线）
            for kline in klines_data[:-1]:
                kline_data = {
                    'open_time': kline[0],
                    'open': float(kline[1]),
                    'high': float(kline[2]),
                    'low': float(kline[3]),
                    'close': float(kline[4]),
                    'volume': float(kline[5]),
                    'close_time': kline[6],
                    'datetime': datetime.fromtimestamp(kline[0]/1000).strftime('%Y-%m-%d %H:%M:%S')
                }
                self.klines.append(kline_data)
            
            print(f"✓ 成功加载 {len(self.klines)} 根历史K线")
            if self.klines:
                print(f"  时间范围: {self.klines[0]['datetime']} 至 {self.klines[-1]['datetime']}")
            
            return True
            
        except requests.exceptions.RequestException as e:
            print(f"⚠ 获取历史K线失败: {e}")
            print("  将继续等待实时K线...")
            return False
        except Exception as e:
            print(f"⚠ 处理历史K线出错: {e}")
            return False
    
    
    async def process_message(self, message):
        """处理WebSocket消息"""
        try:
            data = json.loads(message)
            
            # 检查是否是K线数据
            if 'e' not in data or data['e'] != 'kline':
                return
            
            kline = data['k']
            
            # 只处理已完成的K线
            if kline['x']:  # x=true表示K线已完成
                await self.process_completed_kline(kline)
            else:
                # 实时检查触碰（即使K线未完成）
                await self.check_realtime_touch(kline)
                
        except Exception as e:
            print(f"处理消息出错: {e}")
    
    async def process_completed_kline(self, kline):
        """处理完成的K线"""
        # 转换K线数据格式
        kline_data = {
            'open_time': kline['t'],
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': float(kline['v']),
            'close_time': kline['T'],
            'datetime': datetime.fromtimestamp(kline['t']/1000).strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 添加到K线队列
        self.klines.append(kline_data)
        
        print(f"\n[{kline_data['datetime']}] K线完成")
        print(f"  开: {kline_data['open']:.2f} | 高: {kline_data['high']:.2f} | 低: {kline_data['low']:.2f} | 收: {kline_data['close']:.2f}")
        
        # 检查是否有持仓需要平仓（每根K线完成时检查）
        self.check_close_positions(kline_data)
        
        # 需要至少10根K线才能开始分析
        if len(self.klines) < 10:
            print(f"  等待更多数据... ({len(self.klines)}/10)")
            return
        
        # 更新压力位和支撑位
        self.update_support_resistance()
        
        # 显示当前状态
        self.display_status()
    
    async def check_realtime_touch(self, kline):
        """实时检查是否触碰压力位或支撑位"""
        # 先检查持仓平仓（实时检查，精确到秒）
        current_time_ms = int(datetime.now().timestamp() * 1000)
        current_price = float(kline['c'])  # 使用当前价格
        await self.check_close_positions_realtime(current_time_ms, current_price)
        
        if not self.in_consolidation or self.resistance is None or self.support is None:
            return
        
        current_high = float(kline['h'])
        current_low = float(kline['l'])
        current_close = float(kline['c'])
        current_time = datetime.fromtimestamp(kline['t']/1000).strftime('%Y-%m-%d %H:%M:%S')
        
        # 检查是否触碰压力位 - 做空信号（使用实际触碰的高点）
        # 必须是从下往上触碰：需要有前一根K线且收盘价低于压力位
        if abs(current_high - self.resistance) / self.resistance <= self.touch_threshold:
            # 判断方向：如果有历史K线，检查前一根的收盘价是否低于压力位
            if len(self.klines) > 0:
                prev_close = self.klines[-1]['close']
                if prev_close < self.resistance:
                    # 检查交替买入限制：该区域的上一次交易不能是做空
                    last_type = self.last_trade_type_per_zone.get(self.current_zone_id)
                    if last_type != 'SHORT':
                        self.execute_trade('SHORT', current_high, current_time)  # 使用实际高点
        
        # 检查是否触碰支撑位 - 做多信号（使用实际触碰的低点）
        # 必须是从上往下触碰：需要有前一根K线且收盘价高于支撑位
        if abs(current_low - self.support) / self.support <= self.touch_threshold:
            # 判断方向：如果有历史K线，检查前一根的收盘价是否高于支撑位
            if len(self.klines) > 0:
                prev_close = self.klines[-1]['close']
                if prev_close > self.support:
                    # 检查交替买入限制：该区域的上一次交易不能是做多
                    last_type = self.last_trade_type_per_zone.get(self.current_zone_id)
                    if last_type != 'LONG':
                        self.execute_trade('LONG', current_low, current_time)  # 使用实际低点
    
    def update_support_resistance(self):
        """更新压力位和支撑位"""
        if len(self.klines) < 10:
            return
        
        klines_list = list(self.klines)
        
        # 查找最近的局部高点和低点
        recent_high = None
        recent_low = None
        
        # 从最近的K线往回查找（至少保留2根用于判断局部极值）
        for i in range(len(klines_list) - 3, 1, -1):
            current = klines_list[i]
            
            # 判断是否为局部高点
            if (recent_high is None and 
                current['high'] >= klines_list[i-1]['high'] and 
                current['high'] >= klines_list[i-2]['high'] and
                current['high'] >= klines_list[i+1]['high'] and
                current['high'] >= klines_list[i+2]['high']):
                recent_high = current['high']
            
            # 判断是否为局部低点
            if (recent_low is None and 
                current['low'] <= klines_list[i-1]['low'] and 
                current['low'] <= klines_list[i-2]['low'] and
                current['low'] <= klines_list[i+1]['low'] and
                current['low'] <= klines_list[i+2]['low']):
                recent_low = current['low']
            
            # 找到两个就停止
            if recent_high is not None and recent_low is not None:
                break
        
        # 如果找到新的压力位或支撑位，更新并重置触碰记录
        if recent_high is not None and (self.resistance is None or abs(recent_high - self.resistance) / self.resistance > 0.001):
            old_resistance = self.resistance
            self.resistance = recent_high
            self.resistance_touches = []
            # 压力位变化意味着可能进入新区域，重置盘整状态
            self.in_consolidation = False
            if old_resistance is not None:
                print(f"\n>>> 更新压力位: {old_resistance:.2f} -> {self.resistance:.2f}")
            
        if recent_low is not None and (self.support is None or abs(recent_low - self.support) / self.support > 0.001):
            old_support = self.support
            self.support = recent_low
            self.support_touches = []
            # 支撑位变化意味着可能进入新区域，重置盘整状态
            self.in_consolidation = False
            if old_support is not None:
                print(f">>> 更新支撑位: {old_support:.2f} -> {self.support:.2f}")
        
        # 检查最新K线是否触碰压力位或支撑位
        if self.resistance is not None and self.support is not None:
            latest = klines_list[-1]
            
            # 检查触碰压力位
            if abs(latest['high'] - self.resistance) / self.resistance <= self.touch_threshold:
                if not self.resistance_touches or self.resistance_touches[-1] != latest['datetime']:
                    self.resistance_touches.append(latest['datetime'])
                    print(f">>> 触碰压力位 {self.resistance:.2f} (第{len(self.resistance_touches)}次)")
            
            # 检查触碰支撑位
            if abs(latest['low'] - self.support) / self.support <= self.touch_threshold:
                if not self.support_touches or self.support_touches[-1] != latest['datetime']:
                    self.support_touches.append(latest['datetime'])
                    print(f">>> 触碰支撑位 {self.support:.2f} (第{len(self.support_touches)}次)")
        
        # 检查是否满足盘整条件（压力位和支撑位各触碰2次）
        if (not self.in_consolidation and 
            len(self.resistance_touches) >= 2 and 
            len(self.support_touches) >= 2):
            
            # 检查压力位和支撑位的振幅是否足够（避免区间太小）
            amplitude_percent = ((self.resistance - self.support) / self.support) * 100
            if amplitude_percent < 0.5:
                # 振幅太小，不开启盘整模式
                print(f"\n⚠ 振幅过小 ({amplitude_percent:.2f}%)，未开启盘整模式")
                print(f"   压力位: {self.resistance:.2f} | 支撑位: {self.support:.2f}")
                # 重置触碰记录，等待更合适的机会
                self.resistance_touches = []
                self.support_touches = []
                return
            
            self.in_consolidation = True
            self.consolidation_start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # 生成新的区域ID（基于压力位和支撑位）
            self.current_zone_id = f"{self.resistance:.2f}_{self.support:.2f}"
            print("\n" + "="*60)
            print("🎯 开启盘整模式!")
            print(f"  区域ID: {self.current_zone_id}")
            print(f"  压力位: {self.resistance:.2f} (触碰{len(self.resistance_touches)}次)")
            print(f"  支撑位: {self.support:.2f} (触碰{len(self.support_touches)}次)")
            print(f"  振幅: {(self.resistance - self.support):.2f} ({((self.resistance - self.support) / self.support * 100):.2f}%)")
            print("="*60)
    
    async def check_close_positions_realtime(self, current_time_ms, current_price):
        """实时检查是否有持仓需要平仓（精确到秒）"""
        if not self.open_positions:
            return
        
        positions_to_close = []
        
        for position in self.open_positions:
            # 计算已持仓的秒数
            time_diff_seconds = (current_time_ms - position['entry_timestamp']) / 1000
            
            # 持仓满600秒（10分钟）- 只在刚好满足时触发一次
            if time_diff_seconds >= self.hold_seconds and time_diff_seconds < self.hold_seconds + 1:
                # 事件合约：只判断价格方向
                if position['type'] == 'LONG':
                    # 做多：价格上涨 = 胜
                    is_win = current_price > position['entry_price']
                else:  # SHORT
                    # 做空：价格下跌 = 胜
                    is_win = current_price < position['entry_price']
                
                price_change = current_price - position['entry_price']
                price_change_pct = (price_change / position['entry_price']) * 100
                
                exit_time = datetime.fromtimestamp(current_time_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')
                
                print("\n" + "="*60)
                print(f"{'✅ 胜' if is_win else '❌ 负'} - 平仓 {position['type']}")
                print(f"  入场: {position['entry_price']:.2f} @ {position['entry_time']}")
                print(f"  出场: {current_price:.2f} @ {exit_time}")
                print(f"  持仓时长: {int(time_diff_seconds)} 秒 ({int(time_diff_seconds/60)} 分 {int(time_diff_seconds%60)} 秒)")
                print(f"  价格变动: {price_change:+.2f} ({price_change_pct:+.2f}%)")
                print(f"  预期方向: {'上涨' if position['type'] == 'LONG' else '下跌'} | 实际: {'上涨' if price_change > 0 else '下跌' if price_change < 0 else '不变'}")
                print("="*60)
                
                # 写入平仓日志
                self.log_trade_exit(position, current_price, exit_time, is_win, time_diff_seconds)
                
                # 更新交易记录
                for trade in self.trades:
                    if (trade['entry_price'] == position['entry_price'] and 
                        trade['entry_time'] == position['entry_time'] and
                        trade['status'] == 'OPEN'):
                        trade['status'] = 'CLOSED'
                        trade['exit_price'] = current_price
                        trade['exit_time'] = exit_time
                        trade['price_change'] = price_change
                        trade['price_change_pct'] = price_change_pct
                        trade['is_win'] = is_win
                        trade['hold_seconds'] = int(time_diff_seconds)
                        break
                
                positions_to_close.append(position)
        
        # 移除已平仓的持仓
        for position in positions_to_close:
            self.open_positions.remove(position)
            
            # 如果当前持仓被平掉，清空current_position
            if (self.current_position and 
                self.current_position.get('entry_price') == position['entry_price'] and
                self.current_position.get('entry_time') == position['entry_time']):
                self.current_position = None
    
    def check_close_positions(self, current_kline):
        """检查是否有持仓需要平仓（K线完成时的兜底检查）"""
        if not self.open_positions:
            return
        
        current_timestamp = current_kline['open_time']
        current_price = current_kline['close']
        positions_to_close = []
        
        for position in self.open_positions:
            # 计算已持仓的秒数
            time_diff_seconds = (current_timestamp - position['entry_timestamp']) / 1000
            
            # 持仓满600秒（10分钟）
            if time_diff_seconds >= self.hold_seconds:
                # 事件合约：只判断价格方向
                if position['type'] == 'LONG':
                    # 做多：价格上涨 = 胜
                    is_win = current_price > position['entry_price']
                else:  # SHORT
                    # 做空：价格下跌 = 胜
                    is_win = current_price < position['entry_price']
                
                price_change = current_price - position['entry_price']
                price_change_pct = (price_change / position['entry_price']) * 100
                
                print("\n" + "="*60)
                print(f"{'✅ 胜' if is_win else '❌ 负'} - 平仓 {position['type']}")
                print(f"  入场: {position['entry_price']:.2f} @ {position['entry_time']}")
                print(f"  出场: {current_price:.2f} @ {current_kline['datetime']}")
                print(f"  持仓时长: {int(time_diff_seconds)} 秒 ({int(time_diff_seconds/60)} 分 {int(time_diff_seconds%60)} 秒)")
                print(f"  价格变动: {price_change:+.2f} ({price_change_pct:+.2f}%)")
                print(f"  预期方向: {'上涨' if position['type'] == 'LONG' else '下跌'} | 实际: {'上涨' if price_change > 0 else '下跌' if price_change < 0 else '不变'}")
                print("="*60)
                
                # 写入平仓日志
                self.log_trade_exit(position, current_price, current_kline['datetime'], is_win, time_diff_seconds)
                
                # 更新交易记录
                for trade in self.trades:
                    if (trade['entry_price'] == position['entry_price'] and 
                        trade['entry_time'] == position['entry_time'] and
                        trade['status'] == 'OPEN'):
                        trade['status'] = 'CLOSED'
                        trade['exit_price'] = current_price
                        trade['exit_time'] = current_kline['datetime']
                        trade['price_change'] = price_change
                        trade['price_change_pct'] = price_change_pct
                        trade['is_win'] = is_win
                        trade['hold_seconds'] = int(time_diff_seconds)
                        break
                
                positions_to_close.append(position)
        
        # 移除已平仓的持仓
        for position in positions_to_close:
            self.open_positions.remove(position)
            
            # 如果当前持仓被平掉，清空current_position
            if (self.current_position and 
                self.current_position.get('entry_price') == position['entry_price'] and
                self.current_position.get('entry_time') == position['entry_time']):
                self.current_position = None
    
    def execute_trade(self, trade_type, price, entry_time):
        """执行交易"""
        # 检查是否已有5个持仓
        if len(self.open_positions) >= 5:
            print(f"\n⚠ 持仓数量已达上限(5个)，跳过本次交易")
            return
        
        print("\n" + "="*60)
        print(f"🔔 交易信号: {trade_type}")
        print(f"  入场价格: {price:.2f}")
        print(f"  入场时间: {entry_time}")
        
        # 使用当前系统时间作为入场时间戳（精确到毫秒）
        entry_timestamp = int(datetime.now().timestamp() * 1000)
        actual_entry_time = datetime.fromtimestamp(entry_timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
        
        trade = {
            'type': trade_type,
            'entry_price': price,
            'entry_time': actual_entry_time,  # 使用实际系统时间
            'entry_timestamp': entry_timestamp,
            'status': 'OPEN'
        }
        
        self.trades.append(trade)
        self.current_position = trade
        
        # 更新该区域的上一次交易类型（按区域独立跟踪）
        self.last_trade_type_per_zone[self.current_zone_id] = trade_type
        
        # 添加到持仓列表
        self.open_positions.append({
            'type': trade_type,
            'entry_price': price,
            'entry_time': actual_entry_time,
            'entry_timestamp': entry_timestamp,
            'zone_id': self.current_zone_id  # 记录所属区域
        })
        
        print(f"  实际入场时间: {actual_entry_time}")
        print(f"  所属区域: {self.current_zone_id}")
        print(f"  当前持仓数: {len(self.open_positions)}")
        print(f"  历史交易: {len(self.trades)} 笔")
        print("="*60)
        
        # 写入开仓日志
        self.log_trade_entry(trade_type, price, actual_entry_time, self.current_zone_id)
    
    def display_status(self):
        """显示当前状态"""
        print("\n当前状态:")
        if self.resistance:
            print(f"  压力位: {self.resistance:.2f} (触碰{len(self.resistance_touches)}次)")
        if self.support:
            print(f"  支撑位: {self.support:.2f} (触碰{len(self.support_touches)}次)")
        
        if self.in_consolidation:
            print(f"  盘整状态: ✓ 开启 (自{self.consolidation_start_time})")
            if self.open_positions:
                print(f"  当前持仓: {len(self.open_positions)} 个")
                for i, pos in enumerate(self.open_positions, 1):
                    zone_info = f" [区域:{pos.get('zone_id', 'N/A')}]" if 'zone_id' in pos else ""
                    print(f"    #{i} {pos['type']} @ {pos['entry_price']:.2f} - {pos['entry_time']}{zone_info}")
            else:
                print(f"  当前持仓: 无")
        else:
            print(f"  盘整状态: ✗ 未开启")
        
        print(f"  总交易数: {len(self.trades)} 笔")
        
        # 统计已完成交易的胜率
        closed_trades = [t for t in self.trades if t['status'] == 'CLOSED']
        if closed_trades:
            winning_trades = [t for t in closed_trades if t.get('is_win', False)]
            win_rate = (len(winning_trades) / len(closed_trades)) * 100
            print(f"  已平仓: {len(closed_trades)} 笔 | 胜率: {win_rate:.2f}% ({len(winning_trades)}胜/{len(closed_trades)-len(winning_trades)}负)")
    
    async def websocket_loop(self):
        """WebSocket主循环（带重连机制）"""
        retry_count = 0
        max_retries = 5
        
        # 首次启动时获取历史K线
        if len(self.klines) == 0:
            self.fetch_historical_klines(limit=50)
            
            # 如果成功加载历史数据，立即进行一次分析
            if len(self.klines) >= 10:
                print("\n开始分析历史数据...")
                self.update_support_resistance()
                self.display_status()
                print()
        
        while self.is_running:
            try:
                print(f"正在连接到 {self.ws_url}...")
                async with websockets.connect(self.ws_url) as ws:
                    print(f"✓ WebSocket连接成功: {self.symbol.upper()} {self.interval}")
                    print("="*60)
                    print("实时交易策略:")
                    print("  ① 监听1分钟K线数据")
                    print("  ② 识别局部高点(压力位)和低点(支撑位)")
                    print("  ③ 连续触碰2次压力位 + 2次支撑位 = 开启盘整")
                    print("  ④ 触碰压力位时做空")
                    print("  ⑤ 触碰支撑位时做多")
                    print("  ⑥ 交替买入(压力→支撑→压力)")
                    print("  ⑦ 持仓600秒(10分钟)后平仓")
                    print("  ⑧ 最小振幅: 压力位和支撑位差距 ≥ 0.5%")
                    print("="*60)
                    print()
                    
                    # 重置重试计数
                    retry_count = 0
                    
                    # 接收消息循环
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                            await self.process_message(message)
                        except asyncio.TimeoutError:
                            # 发送ping保持连接
                            try:
                                await ws.ping()
                            except:
                                print("⚠ Ping失败，连接可能已断开")
                                break
                        except websockets.exceptions.ConnectionClosed:
                            print("⚠ WebSocket连接已关闭")
                            break
                            
            except websockets.exceptions.WebSocketException as e:
                retry_count += 1
                print(f"⚠ WebSocket异常 (重试 {retry_count}/{max_retries}): {e}")
                if retry_count >= max_retries:
                    print("✗ 达到最大重试次数，停止连接")
                    self.is_running = False
                    break
                wait_time = min(retry_count * 2, 30)
                print(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                retry_count += 1
                print(f"⚠ 连接错误 (重试 {retry_count}/{max_retries}): {e}")
                if retry_count >= max_retries:
                    print("✗ 达到最大重试次数，停止连接")
                    self.is_running = False
                    break
                wait_time = min(retry_count * 2, 30)
                print(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
    
    async def start_async(self):
        """异步启动交易机器人"""
        print("="*60)
        print("实时交易机器人启动")
        print("="*60)
        print(f"交易对: {self.symbol.upper()}")
        print(f"时间周期: {self.interval}")
        print(f"触碰阈值: {self.touch_threshold*100}%")
        print("="*60)
        print()
        
        self.is_running = True
        
        try:
            await self.websocket_loop()
        except KeyboardInterrupt:
            print("\n收到停止信号，正在关闭...")
            self.is_running = False
            self.print_summary()
        except Exception as e:
            print(f"程序异常: {e}")
            self.is_running = False
    
    def start(self):
        """启动交易机器人（同步入口）"""
        try:
            asyncio.run(self.start_async())
        except KeyboardInterrupt:
            print("\n程序已停止")
            self.print_summary()
    
    def print_summary(self):
        """打印交易汇总"""
        print("\n" + "="*60)
        print("交易汇总")
        print("="*60)
        print(f"总交易次数: {len(self.trades)}")
        
        if self.trades:
            long_trades = [t for t in self.trades if t['type'] == 'LONG']
            short_trades = [t for t in self.trades if t['type'] == 'SHORT']
            print(f"  做多: {len(long_trades)} 笔")
            print(f"  做空: {len(short_trades)} 笔")
            
            # 统计已完成的交易
            closed_trades = [t for t in self.trades if t['status'] == 'CLOSED']
            if closed_trades:
                winning_trades = [t for t in closed_trades if t.get('is_win', False)]
                losing_trades = [t for t in closed_trades if not t.get('is_win', False)]
                win_rate = (len(winning_trades) / len(closed_trades)) * 100 if closed_trades else 0
                
                print(f"\n已完成交易统计:")
                print(f"  完成: {len(closed_trades)} 笔")
                print(f"  胜率: {win_rate:.2f}% ({len(winning_trades)}胜/{len(losing_trades)}负)")
            
            # 未平仓持仓
            open_trades = [t for t in self.trades if t['status'] == 'OPEN']
            if open_trades:
                print(f"\n未平仓: {len(open_trades)} 笔")
                for trade in open_trades:
                    print(f"  {trade['type']} @ {trade['entry_price']:.2f} - {trade['entry_time']}")
            
            print("\n最近10笔已完成交易:")
            recent_closed = [t for t in reversed(self.trades) if t['status'] == 'CLOSED'][:10]
            for i, trade in enumerate(recent_closed, 1):
                result = "✅胜" if trade.get('is_win') else "❌负"
                direction = "↑" if trade.get('price_change', 0) > 0 else "↓" if trade.get('price_change', 0) < 0 else "→"
                print(f"{i}. {result} {trade['type']} @ {trade['entry_price']:.2f} -> {trade.get('exit_price', 0):.2f} {direction} {trade.get('price_change', 0):+.2f}")
        
        print("="*60)


if __name__ == '__main__':
    print("启动 ETHUSDT K线监听程序 (Binance)...")
    print("监控 1分钟K线")
    print()
    
    while True:
        try:
            # 创建交易机器人
            bot = LiveTradingBot(symbol='ethusdt', interval='1m', touch_threshold=0.0022)
            
            # 启动机器人
            bot.start()
            break
        except KeyboardInterrupt:
            print("\n程序已停止")
            break
        except Exception as e:
            print(f"程序异常: {e}")
            print("3秒后重启...")
            time.sleep(3)
