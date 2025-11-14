import json
import os
from datetime import datetime
import requests


class BinanceKlineAnalyzer:
    """币安K线数据获取与压力位支撑位横盘识别"""
    
    def __init__(self, symbol='ETHUSDT', interval='1m', limit=5000):
        self.symbol = symbol
        self.interval = interval
        self.limit = limit
        self.data_file = f'{symbol}_{interval}_klines.json'
        self.klines = []
        
    def fetch_klines_from_binance(self):
        """从币安获取K线数据"""
        print(f"正在从币安获取 {self.symbol} {self.interval} 的K线数据...")
        
        url = 'https://api.binance.com/api/v3/klines'
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
    
    def find_consolidation_by_support_resistance(self, touch_threshold=0.3, min_touches=2, max_klines_between=50, min_duration=20):
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
                
                # 向后查找支撑位和其他触碰
                j = i + 1
                support = None
                support_touches = []
                
                while j < len(self.klines) and j - i < max_klines_between * 3:
                    k = self.klines[j]
                    
                    # 检查是否触碰压力位
                    if abs(k['high'] - resistance) / resistance * 100 <= touch_threshold:
                        resistance_touches.append(j)
                    
                    # 寻找支撑位(局部低点)
                    if j >= 2 and j < len(self.klines) - 2:
                        is_local_low = (k['low'] <= self.klines[j-1]['low'] and 
                                       k['low'] <= self.klines[j-2]['low'] and
                                       k['low'] <= self.klines[j+1]['low'] and
                                       k['low'] <= self.klines[j+2]['low'] if j+2 < len(self.klines) else True)
                        
                        if is_local_low and support is None:
                            support = k['low']
                            support_touches = [j]
                        elif support is not None and abs(k['low'] - support) / support * 100 <= touch_threshold:
                            support_touches.append(j)
                    
                    j += 1
                
                # 判断是否形成横盘区域
                if (support is not None and 
                    len(resistance_touches) >= min_touches and 
                    len(support_touches) >= min_touches):
                    
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
                        
                        consolidation_zones.append({
                            'start_index': start_idx,
                            'end_index': end_idx,
                            'start_time': self.klines[start_idx]['datetime'],
                            'end_time': self.klines[end_idx]['datetime'],
                            'duration': duration,
                            'resistance': round(resistance, 2),
                            'support': round(support, 2),
                            'resistance_touches': len(resistance_touches),
                            'support_touches': len(support_touches),
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
        
        consolidation_zones = self.find_consolidation_by_support_resistance(
            touch_threshold=0.3,      # 0.3%的触碰阈值
            min_touches=2,            # 至少触碰2次
            max_klines_between=50,    # 触碰间隔不超过50根K线
            min_duration=20           # 至少持续20根K线
        )
        
        print(f"\n识别到 {len(consolidation_zones)} 个横盘盘整区域")
        
        # 3. 输出横盘区域详情
        if consolidation_zones:
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
            'consolidation_zones': consolidation_zones
        }


if __name__ == '__main__':
    # 创建分析器并执行分析
    analyzer = BinanceKlineAnalyzer(symbol='ETHUSDT', interval='1m', limit=5000)
    result = analyzer.analyze()
