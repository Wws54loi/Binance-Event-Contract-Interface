import requests
import json
import os
import time
from datetime import datetime, timedelta

class BinanceDataFetcher:
    def __init__(self, symbol='ETHUSDT', interval='1m', days=300):
        self.symbol = symbol
        self.interval = interval
        self.days = days
        self.filename = f'{symbol}_{interval}_klines.json'
        self.base_url = 'https://fapi.binance.com/fapi/v1/klines'

    def fetch(self):
        end_time = int(time.time() * 1000)
        start_time = int((datetime.now() - timedelta(days=self.days)).timestamp() * 1000)
        
        all_klines = []
        current_end = end_time
        
        print(f"开始获取 {self.days} 天的 {self.symbol} {self.interval} K线数据 (合约)...")
        
        while True:
            params = {
                'symbol': self.symbol,
                'interval': self.interval,
                'limit': 1500,
                'endTime': current_end
            }
            
            try:
                resp = requests.get(self.base_url, params=params, timeout=10)
                if resp.status_code != 200:
                    print(f"API Error: {resp.text}")
                    time.sleep(1)
                    continue
                    
                data = resp.json()
                if not data: 
                    break
                
                all_klines.extend(data)
                
                first_time = data[0][0]
                current_end = first_time - 1
                
                print(f"\r已收集 {len(all_klines)} 条K线...", end="")
                
                if first_time <= start_time:
                    break
                    
                time.sleep(0.05) # 避免频率限制
                
            except Exception as e:
                print(f"Error: {e}, retrying...")
                time.sleep(2)
                continue
                
        # 去重并排序
        kline_dict = {k[0]: k for k in all_klines}
        sorted_times = sorted(kline_dict.keys())
        final_klines = [kline_dict[t] for t in sorted_times if t >= start_time]
        
        print(f"\n整理完成，共 {len(final_klines)} 条数据")
        
        # 格式化
        formatted = []
        for k in final_klines:
            formatted.append({
                'datetime': datetime.fromtimestamp(k[0]/1000).strftime('%Y-%m-%d %H:%M:%S'),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5])
            })
            
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(formatted, f)
        print(f"数据已保存至 {self.filename}")

if __name__ == "__main__":
    fetcher = BinanceDataFetcher(days=300)
    fetcher.fetch()
