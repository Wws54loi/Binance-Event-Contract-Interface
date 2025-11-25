import asyncio
import json
import logging
import websockets
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

async def listen_eth_index_price():
    # 使用 markPrice@1s 流，更新频率为 1秒
    uri = "wss://fstream.binance.com/ws/ethusdt@markPrice@1s"
    
    logging.info(f"正在连接到 {uri} ...")
    
    while True:
        try:
            async with websockets.connect(uri) as ws:
                logging.info("连接成功！正在获取 ETHUSDT 指数价格...")
                
                async for message in ws:
                    try:
                        data = json.loads(message)
                        # 提取指数价格 "i"
                        index_price = data.get("i")
                        event_time = data.get("E")
                        
                        if index_price:
                            # 格式化时间
                            ts = int(event_time) / 1000 if event_time else datetime.now().timestamp()
                            time_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S')
                            
                            print(f"[{time_str}] ETHUSDT 指数价格: {index_price}")
                    except json.JSONDecodeError:
                        logging.error("无法解析 JSON 消息")
                    except Exception as e:
                        logging.error(f"处理消息时出错: {e}")
                        
        except (websockets.exceptions.ConnectionClosedError, ConnectionRefusedError) as e:
            logging.warning(f"连接断开: {e}。3秒后重连...")
            await asyncio.sleep(3)
        except Exception as e:
            logging.error(f"发生错误: {e}。3秒后重连...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    try:
        asyncio.run(listen_eth_index_price())
    except KeyboardInterrupt:
        print("\n程序已停止")
