import asyncio
import json
import time
import hmac
import hashlib
import base64
import requests
import logging
import os
from datetime import datetime
import pandas as pd

# ================= 配置区域 =================
# ⚠️ 警告: 实盘前请务必测试!
SIMULATION_MODE = True  # True=模拟模式(只打印不下单), False=实盘模式

# 账户配置 (仅实盘需要，模拟模式下可忽略)
API_KEY = "" 
PRIVATE_KEY_PATH = "" 

# 交易目标
SYMBOL = "ETHUSDT"
QUANTITY = 0.01  # 开单数量

# 自动箱体配置
# 策略: 使用 1m K线合成 10m K线，回看 100 根 10m K线确定箱体
BASE_TIMEFRAME = "1m"
RESAMPLE_MINUTES = 10     # 合成 10分钟线
BOX_LOOKBACK_10M = 100    # 回看 100 根 10分钟线 (即过去 1000 分钟)
INNER_BOX_RATIO = 0.2     # 弱支撑/压力位距离强支撑/压力的比例 (0.2 = 20% 深度)
ADX_THRESHOLD = 25        # ADX 阈值，超过此值视为单边趋势，停止交易
ADX_PERIOD = 14           # ADX 计算周期 (基于 10m 线)

# 风控配置
SLIPPAGE = 1.0        # 最大允许滑点
COOLDOWN = 60         # 开单冷却时间 (秒)

# ===========================================

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("AutoTrader")

class BinanceTradeClient:
    """
    负责处理 WebSocket 交易 API (下单/撤单)
    基于用户提供的 ws-fapi 文档
    """
    def __init__(self, api_key, private_key_path):
        self.api_key = api_key
        self.private_key_path = private_key_path
        self.ws = None
        self.ws_url = "wss://ws-fapi.binance.com/ws-fapi/v1"
        self.private_key = None
        
        if not SIMULATION_MODE:
            self.load_key()

    def load_key(self):
        if not os.path.exists(self.private_key_path):
            logger.error(f"私钥文件未找到: {self.private_key_path}")
            return
        
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            with open(self.private_key_path, 'rb') as f:
                self.private_key = load_pem_private_key(data=f.read(), password=None)
        except ImportError:
            logger.error("缺少 cryptography 库，无法加载私钥。请运行: pip install cryptography")
        except Exception as e:
            logger.error(f"加载私钥失败: {e}")

    def sign_params(self, params):
        """生成 Ed25519 签名"""
        # 1. 按字母顺序排序并拼接
        payload = '&'.join([f'{param}={value}' for param, value in sorted(params.items())])
        # 2. 签名
        signature = base64.b64encode(self.private_key.sign(payload.encode('ASCII')))
        return signature.decode('ASCII')

    async def connect(self):
        if SIMULATION_MODE: return
        import websockets
        try:
            self.ws = await websockets.connect(self.ws_url)
            logger.info("✅ 交易接口连接成功")
            # 可以在这里发送 session.logon 进行登录(如果需要保持长连接鉴权)
        except Exception as e:
            logger.error(f"❌ 交易接口连接失败: {e}")

    async def place_order(self, side, price):
        """
        发送下单请求
        """
        if SIMULATION_MODE:
            logger.info(f"🚀 [模拟下单] {side} {SYMBOL} @ {price} | 数量: {QUANTITY}")
            return

        if not self.ws:
            await self.connect()

        params = {
            'apiKey': self.api_key,
            'symbol': SYMBOL,
            'side': side, # BUY or SELL
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': str(QUANTITY),
            'price': f"{price:.2f}",
            'timestamp': int(time.time() * 1000)
        }
        
        # 添加签名
        params['signature'] = self.sign_params(params)

        req_id = f"order_{int(time.time())}"
        payload = {
            "id": req_id,
            "method": "order.place",
            "params": params
        }

        try:
            await self.ws.send(json.dumps(payload))
            logger.info(f"📡 发送下单请求: {side} {price}")
            # 实际场景中应该等待 recv() 获取响应
        except Exception as e:
            logger.error(f"下单发送失败: {e}")

class AutoBoxTrader:
    def __init__(self):
        self.levels = {
            "s_res": 0.0, "w_res": 0.0, 
            "w_sup": 0.0, "s_sup": 0.0
        }
        self.current_price = 0.0
        self.prev_price = 0.0
        self.last_trade_time = 0
        self.is_trending = False # 是否处于单边趋势
        self.adx_value = 0.0     # ADX 值
        self.active_trade = None # 当前持仓: None 或 dict
        self.trade_client = BinanceTradeClient(API_KEY, PRIVATE_KEY_PATH)

    def calculate_adx(self, df, period=14):
        """
        计算 ADX 指标判断趋势强度
        """
        try:
            df = df.copy()
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            
            # 1. 计算 TR
            df['tr0'] = abs(df['high'] - df['low'])
            df['tr1'] = abs(df['high'] - df['close'].shift(1))
            df['tr2'] = abs(df['low'] - df['close'].shift(1))
            df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
            
            # 2. 计算 DM+ 和 DM-
            df['up_move'] = df['high'] - df['high'].shift(1)
            df['down_move'] = df['low'].shift(1) - df['low']
            
            df['plus_dm'] = 0.0
            df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), 'plus_dm'] = df['up_move']
            
            df['minus_dm'] = 0.0
            df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), 'minus_dm'] = df['down_move']
            
            # 3. 平滑 (Wilder's Smoothing)
            # 为了简单，这里使用 EMA 代替 Wilder's Smoothing，效果接近
            alpha = 1 / period
            df['tr_smooth'] = df['tr'].ewm(alpha=alpha, adjust=False).mean()
            df['plus_dm_smooth'] = df['plus_dm'].ewm(alpha=alpha, adjust=False).mean()
            df['minus_dm_smooth'] = df['minus_dm'].ewm(alpha=alpha, adjust=False).mean()
            
            # 4. 计算 DI+ 和 DI-
            df['plus_di'] = 100 * (df['plus_dm_smooth'] / df['tr_smooth'])
            df['minus_di'] = 100 * (df['minus_dm_smooth'] / df['tr_smooth'])
            
            # 5. 计算 DX 和 ADX
            df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
            df['adx'] = df['dx'].ewm(alpha=alpha, adjust=False).mean()
            
            return df['adx'].iloc[-1]
        except Exception as e:
            logger.error(f"ADX 计算错误: {e}")
            return 0

    def fetch_initial_box(self):
        """
        通过 REST API 获取 1m K 线，合成 10m K 线并计算箱体
        """
        # 需要获取足够多的 1m K 线来合成 10m 线
        # 目标: 100根 10m 线 + ADX预留
        required_10m_candles = BOX_LOOKBACK_10M + ADX_PERIOD * 3
        fetch_limit_1m = required_10m_candles * RESAMPLE_MINUTES
        
        # Binance 单次最多 1500
        if fetch_limit_1m > 1500: fetch_limit_1m = 1500
        
        logger.info(f"正在获取 {fetch_limit_1m} 根 1m K线，以合成 10m 周期数据...")
        
        try:
            url = "https://fapi.binance.com/fapi/v1/klines"
            params = {"symbol": SYMBOL, "interval": BASE_TIMEFRAME, "limit": fetch_limit_1m}
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            
            df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'x', 'y', 'z', 'a', 'b', 'c'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            
            # === 核心: 重采样为 10m K线 ===
            df.set_index('time', inplace=True)
            df_10m = df.resample(f'{RESAMPLE_MINUTES}min').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last'
            }).dropna()
            
            logger.info(f"✅ 已合成 {len(df_10m)} 根 {RESAMPLE_MINUTES}分钟 K线")

            # === 趋势检查 (基于 10m) ===
            current_adx = self.calculate_adx(df_10m, ADX_PERIOD)
            self.adx_value = current_adx
            logger.info(f"📊 当前 10m周期 ADX({ADX_PERIOD}): {current_adx:.2f}")
            
            if current_adx > ADX_THRESHOLD:
                self.is_trending = True
                logger.warning(f"⚠️ 检测到 10m级别 强趋势 (ADX > {ADX_THRESHOLD})，将暂停开单！")
            else:
                self.is_trending = False
                logger.info(f"✅ 10m级别 处于震荡状态 (ADX < {ADX_THRESHOLD})，允许交易")

            # 截取最后 BOX_LOOKBACK_10M 根 10m K 线用于计算箱体
            df_box = df_10m.iloc[-BOX_LOOKBACK_10M:]
            
            # 1. 强支撑/压力 = 周期内的最高/最低点
            max_h = df_box['high'].max()
            min_l = df_box['low'].min()
            
            # 2. 计算箱体高度
            box_height = max_h - min_l
            
            # 3. 弱支撑/压力 = 向内收缩一定比例 (例如 20%)
            # 弱压力 = 最高点 - 20% 高度
            # 弱支撑 = 最低点 + 20% 高度
            w_res = max_h - (box_height * INNER_BOX_RATIO)
            w_sup = min_l + (box_height * INNER_BOX_RATIO)
            
            self.levels = {
                "s_res": max_h,
                "w_res": w_res,
                "w_sup": w_sup,
                "s_sup": min_l
            }
            
            logger.info("✅ 箱体构建完成:")
            logger.info(f"   🟥 强压力 (S_RES): {max_h:.2f}")
            logger.info(f"   🟧 弱压力 (W_RES): {w_res:.2f}")
            logger.info(f"   🟦 弱支撑 (W_SUP): {w_sup:.2f}")
            logger.info(f"   🟩 强支撑 (S_SUP): {min_l:.2f}")
            logger.info(f"   📏 箱体高度: {box_height:.2f} ({box_height/min_l*100:.2f}%)")
            
        except Exception as e:
            logger.error(f"获取 K 线失败: {e}")

    async def run(self):
        # 1. 初始化箱体
        self.fetch_initial_box()
        
        # 2. 连接交易接口 (如果是实盘)
        if not SIMULATION_MODE:
            await self.trade_client.connect()
            
        # 3. 连接行情接口
        import websockets
        url = f"wss://fstream.binance.com/ws/{SYMBOL.lower()}@aggTrade"
        
        logger.info(f"正在连接行情: {url}")
        
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("🟢 行情连接成功，开始监控...")
                    
                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                            price = float(data['p'])
                            
                            if self.prev_price == 0:
                                self.prev_price = price
                                self.current_price = price
                                continue
                            
                            self.current_price = price
                            
                            # 打印心跳日志 (每 10 秒打印一次价格，证明程序活着)
                            if int(time.time()) % 10 == 0:
                                status = "🚫暂停" if self.is_trending else "✅运行"
                                logger.info(f"💓 监控中... 价格: {price:.2f} | ADX: {self.adx_value:.2f} [{status}]")

                            await self.check_signal(price)
                            self.prev_price = price
                        except Exception as e:
                            logger.error(f"处理消息错误: {e}")
                            
            except Exception as e:
                logger.error(f"❌ 连接断开: {e}")
                logger.info("🔄 3秒后尝试重连...")
                await asyncio.sleep(3)

    async def check_signal(self, price):
        now = time.time()
        
        # 1. 检查当前持仓是否需要平仓
        if self.active_trade:
            await self.check_exit(price)
            return # 有持仓时，不进行开仓检查

        if now - self.last_trade_time < COOLDOWN:
            return

        # 如果处于强趋势中，禁止开单
        if self.is_trending:
            # 可选: 在这里可以添加逻辑定期重新检查趋势 (例如每分钟检查一次)
            # 但为了简单，目前只在启动时检查一次。如果需要实时检查，需要维护 K 线队列。
            return

        levels = self.levels
        prev = self.prev_price
        
        # === 交易逻辑 (与 web_monitor.py 一致) ===
        
        # 1. 做空逻辑 (触碰压力位)
        # 强压力位
        if prev < levels["s_res"] and price >= levels["s_res"]:
            if abs(price - levels["s_res"]) <= SLIPPAGE:
                await self.execute("SELL", price, "触碰强压力位")
        
        # 弱压力位 (前提是没碰到强压力)
        elif prev < levels["w_res"] and price >= levels["w_res"]:
            if price < levels["s_res"]: # 确保还在箱体内
                if abs(price - levels["w_res"]) <= SLIPPAGE:
                    await self.execute("SELL", price, "触碰弱压力位")

        # 2. 做多逻辑 (触碰支撑位)
        # 强支撑位
        elif prev > levels["s_sup"] and price <= levels["s_sup"]:
            if abs(price - levels["s_sup"]) <= SLIPPAGE:
                await self.execute("BUY", price, "触碰强支撑位")
                
        # 弱支撑位
        elif prev > levels["w_sup"] and price <= levels["w_sup"]:
            if price > levels["s_sup"]:
                if abs(price - levels["w_sup"]) <= SLIPPAGE:
                    await self.execute("BUY", price, "触碰弱支撑位")

    async def execute(self, side, price, reason):
        logger.info(f"⚡ 信号触发: {side} | 价格: {price} | 原因: {reason}")
        self.last_trade_time = time.time()
        
        # 记录持仓
        self.active_trade = {
            "side": side,
            "entry_price": price,
            "entry_time": time.time(),
            "expiry_time": time.time() + 600 # 10分钟后强制平仓 (参考 web_monitor 逻辑)
        }
        
        # 调用交易客户端下单
        await self.trade_client.place_order(side, price)

    async def check_exit(self, current_price):
        """
        检查持仓是否需要平仓 (止盈/止损/时间到期)
        """
        if not self.active_trade:
            return

        trade = self.active_trade
        now = time.time()
        
        # 1. 时间到期平仓
        if now >= trade["expiry_time"]:
            await self.close_position("时间到期", current_price)
            return

        # 2. 止盈止损 (简单示例: 盈亏超过一定比例平仓，或者回到箱体中轴)
        # 这里暂时只实现时间平仓，您可以根据需要添加价格平仓逻辑
        
        # 计算浮动盈亏
        pnl = 0
        if trade["side"] == "LONG": # 之前是买入(做多)
            pnl = current_price - trade["entry_price"]
        else: # 之前是卖出(做空)
            pnl = trade["entry_price"] - current_price
            
        # 示例: 止损 50U
        # if pnl < -50:
        #    await self.close_position("止损触发", current_price)

    async def close_position(self, reason, price):
        if not self.active_trade: return
        
        side = "SELL" if self.active_trade["side"] == "LONG" else "BUY" # 平仓方向相反
        logger.info(f"🏁 平仓触发: {reason} | 方向: {side} | 价格: {price}")
        
        # 调用交易客户端平仓
        await self.trade_client.place_order(side, price)
        
        # 清除持仓状态
        self.active_trade = None

if __name__ == "__main__":
    bot = AutoBoxTrader()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("程序已停止")
