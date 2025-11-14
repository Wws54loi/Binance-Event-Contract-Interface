import asyncio
import websockets
import json
from datetime import datetime
from utils import format_kline, format_timestamp
import csv
import os
import uuid
import time
from 微信提醒 import send_wechat_notification, test_wechat_notification
from 下单模块 import BinanceTrader
from typing import Optional

# 币安API配置
API_KEY = "Xq2X0xMjmsbArOBmYIxgL0IOQvJZuMK7ec29w3HTogwA737i18cwmUkH81QzjDYu"
API_SECRET = "sfGu8nnBwdO6xCODFOCmkymwCWkfXCWBUsmADnPLEQcbqD47MO6qBEcljrOfrFxA"
LEVERAGE = 100  # 杠杆倍数
TAKE_PROFIT_PCT = 330  # 止盈百分比

def is_in_efficient_time(now: Optional[datetime] = None) -> bool:
	"""
	判断当前本地时间是否处于高效买入时段。
	时段列表（起始含，结束不含）：
	- 00:00 – 02:00
	- 03:00 – 09:00
	- 18:00 – 20:00
	- 21:00 – 22:00
	- 23:00 – 24:00（等价于 23:00 – 次日00:00）

	说明：使用本地系统时间；如需指定时区可在外层统一转换。
	"""
	now = now or datetime.now()
	h = now.hour
	m = now.minute
	# 统一转分钟判断，便于包含边界
	minutes = h * 60 + m
	ranges = [
		(0 * 60 + 0, 2 * 60 + 0),   # 00:00–02:00
		(3 * 60 + 0, 9 * 60 + 0),   # 03:00–09:00
		(18 * 60 + 0, 20 * 60 + 0), # 18:00–20:00
		(21 * 60 + 0, 22 * 60 + 0), # 21:00–22:00
		(23 * 60 + 0, 24 * 60 + 0), # 23:00–24:00
	]
	return any(start <= minutes < end for start, end in ranges)

def calculate_trade_amount(k1_strength_pct):
	"""
	根据K1柱体强度计算下注金额
	k1_strength_pct: K1的涨跌幅百分比
	返回: (本金, 手续费, 净本金, 下单金额, 保证金)

	定义说明:
	- 本金(principal): 初始投入
	- 手续费(fee): 本金的9.8%
	- 净本金(actual_margin): 扣除手续费后的实际可用本金
	- 下单金额(order_amount): 净本金的5.3倍（总持仓规模）
	- 保证金(guaranteed_margin): 下单金额减去原始本金 = (净本金*5.3) - principal
	"""
	# 确定本金
	principal = 0.5

	fee = principal * 0.098
	actual_margin = principal - fee
	order_amount = actual_margin * 5.3
	guaranteed_margin = order_amount - principal
	return principal, fee, actual_margin, order_amount, guaranteed_margin

def count_open_positions(csv_path: str = "trade_signals.csv") -> Optional[int]:
	"""
	统计 CSV 中“未平仓”的记录数。
	返回整数；若文件不存在或读取异常，返回 None。
	"""
	try:
		if not os.path.exists(csv_path):
			return 0
		with open(csv_path, 'r', newline='', encoding='utf-8') as f:
			reader = csv.reader(f)
			rows = list(reader)
		if not rows:
			return 0
		header = rows[0]
		if '是否平仓' not in header:
			return 0
		idx_closed = header.index('是否平仓')
		cnt = 0
		for row in rows[1:]:
			if idx_closed < len(row) and row[idx_closed] == '未平仓':
				cnt += 1
		return cnt
	except Exception:
		return None

def get_open_position_info(csv_path: str = "trade_signals.csv"):
	"""
	获取当前未平仓的持仓信息
	返回: 
		- dict: {'direction': '做多'/'做空', 'trade_id': 主仓位ID, 'add_count': 已加仓次数} - 有持仓
		- {}: 空字典 - 无持仓（正常情况）
		- None: 读取失败（异常情况）
	"""
	try:
		# 文件不存在或为空，说明没有持仓记录
		if not os.path.exists(csv_path):
			return {}
		
		with open(csv_path, 'r', newline='', encoding='utf-8') as f:
			reader = csv.reader(f)
			rows = list(reader)
		
		if not rows:
			return {}
		
		header = rows[0]
		if '是否平仓' not in header or '方向' not in header or '仓位ID' not in header or '备注' not in header:
			return {}
		
		idx_closed = header.index('是否平仓')
		idx_direction = header.index('方向')
		idx_trade_id = header.index('仓位ID')
		idx_remark = header.index('备注')
		
		# 查找未平仓记录
		open_positions = []
		for row in rows[1:]:
			if idx_closed < len(row) and row[idx_closed] == '未平仓':
				open_positions.append(row)
		
		# 没有未平仓记录，返回空字典（正常情况）
		if not open_positions:
			return {}
		
		# 找到主仓位（第一个未平仓且不是加仓的记录）
		main_position = None
		for row in open_positions:
			remark = row[idx_remark] if idx_remark < len(row) else ""
			if "加仓" not in remark:
				main_position = row
				break
		
		if not main_position:
			# 如果所有未平仓都是加仓记录，取第一个
			main_position = open_positions[0]
		
		# 统计加仓次数
		main_id = main_position[idx_trade_id]
		add_count = 0
		for row in open_positions:
			remark = row[idx_remark] if idx_remark < len(row) else ""
			if f"加仓#{main_id}" in remark or (row != main_position and "加仓" in remark):
				add_count += 1
		
		return {
			'direction': main_position[idx_direction],
			'trade_id': main_id,
			'add_count': add_count
		}
	except Exception as e:
		print(f"⚠ 读取持仓信息失败: {e}")
		return None  # 返回None表示读取失败

def write_trade_log(direction, entry_price,
					k1_open, k1_high, k1_low, k1_close,
					k2_open, k2_high, k2_low, k2_close,
					breakout_direction, k1_strength_pct, timestamp, 
					trade_id=None, actual_qty=None, actual_price=None, is_add_position=False, main_trade_id=None, add_count=0):
	"""写入交易日志到CSV，并返回唯一仓位ID

	备注：新增预留字段与K1/K2四价位字段。
	参数:
		trade_id: 可选的仓位ID(实盘下单后使用订单ID)
		actual_qty: 实际成交数量
		actual_price: 实际成交价格
		is_add_position: 是否为加仓
		main_trade_id: 主仓位ID（加仓时使用）
		add_count: 加仓次数（第几次加仓）
	"""
	log_file = "trade_signals.csv"
	file_exists = os.path.exists(log_file)
	principal, fee, actual_margin, order_amount, guaranteed_margin = calculate_trade_amount(k1_strength_pct)
	
	# 如果没有提供trade_id,生成默认ID
	if trade_id is None:
		trade_id = f"{int(timestamp)}-{uuid.uuid4().hex[:8]}"
	
	# 使用实际成交价或预估价
	final_entry_price = actual_price if actual_price else entry_price
	
	with open(log_file, 'a', newline='', encoding='utf-8') as f:
		writer = csv.writer(f)
		if not file_exists:
			writer.writerow([
				'仓位ID','时间','方向','入场价',
				'K1开盘','K1最高','K1最高','K1收盘',
				'K2开盘','K2最高','K2最低','K2收盘',
				'突破方向','K1强度(%)',
				'本金(U)','手续费(U)','净本金(U)','下单金额(U)','保证金(U)',
				'是否平仓','出场时间','出场价格','持仓K线数','持仓时长','价格变动%','合约收益%','盈亏USDC',
				'备注'
			])
		
		remark = f"基于K1区间的{'向上' if breakout_direction == 'up' else '向下'}突破回归信号"
		if is_add_position:
			remark = f"加仓#{main_trade_id} (第{add_count}次加仓)" + " | " + remark
		if actual_qty:
			remark += f" | 实际数量: {actual_qty}"
		
		writer.writerow([
			trade_id,
			datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S'),
			direction,
			f"{final_entry_price:.2f}",
			f"{k1_open:.2f}",
			f"{k1_high:.2f}",
			f"{k1_low:.2f}",
			f"{k1_close:.2f}",
			f"{k2_open:.2f}",
			f"{k2_high:.2f}",
			f"{k2_low:.2f}",
			f"{k2_close:.2f}",
			breakout_direction,
			f"{k1_strength_pct:.4f}",
			f"{principal:.2f}",
			f"{fee:.4f}",
			f"{actual_margin:.4f}",
			f"{order_amount:.4f}",
			f"{guaranteed_margin:.4f}",
			'未平仓','', '', '', '', '', '', '',
			remark
		])
	print(f"📝 交易信号已记录到 {log_file}")
	if is_add_position:
		print(f"   📈 加仓#{main_trade_id} - 第{add_count}次加仓")
	print(f"   🆔 仓位ID: {trade_id}")
	print(f"   💵 本金: {principal:.2f}U | 手续费: {fee:.4f}U | 净本金: {actual_margin:.4f}U | 下单金额: {order_amount:.4f}U | 保证金: {guaranteed_margin:.4f}U")
	return trade_id

async def main():
	url = "wss://fstream.binance.com/ws/ethusdc@kline_15m/ethusdc@kline_1m"
	
	# 初始化交易客户端
	trader = BinanceTrader(API_KEY, API_SECRET)
	
	# 首次设置杠杆和逐仓(只需一次)
	try:
		trader.set_leverage("ETHUSDC", LEVERAGE)
		print(f"✓ 杠杆已设置为 {LEVERAGE}x")
	except Exception as e:
		print(f"设置杠杆: {e}")
	
	try:
		trader.set_margin_mode("ETHUSDC", "ISOLATED")
		print("✓ 已切换为逐仓模式")
	except Exception as e:
		if "No need to change" in str(e):
			print("✓ 已经是逐仓模式")
		else:
			print(f"设置逐仓: {e}")
	
	# 状态变量
	monitoring_state = "waiting_15m"  # waiting_15m, monitoring_1m, key_focus
	k15m_reference = None  # 参考的15分钟K线数据 {high, low, open, close, timestamp}
	k1_strength_pct = 0  # K1的柱体强度(涨跌幅百分比)
	has_breakout = False  # 是否发生突破
	breakout_direction = None  # 突破方向: 'up' 或 'down'
	one_min_count = 0  # 当前15分钟内的1分钟K线计数
	k2_last_check_done = False  # K2最后一根1分钟K线是否已检查
	signal_recorded = False  # 交易信号是否已记录(避免重复记录)
	# 本周期执行信息与原因跟踪
	trade_executed = False  # 是否真正写入了交易
	cycle_flags = {}        # 记录各阶段布尔标记，用于失败原因归纳
	# 去重控制：仅在每个15m周期内首次突破时提示（使用 has_breakout 控制），无需额外变量
	
	try:
		# 启动前通知自检
		print("🔍 正在进行微信通知自检...")
		# wechat_ok = test_wechat_notification()
		# print("✅ 微信通知连通正常" if wechat_ok else "❌ 微信通知自检失败，后续发送可能不成功")
		async with websockets.connect(url) as ws:
			print("=" * 80)
			print("WebSocket 已连接到 Binance")
			print("已订阅 ETHUSDC 的 15分钟 和 1分钟 K线")
			print("=" * 80)
			print()
			print("📡 状态: 等待满足条件的15分钟K线...")
			print()
			
			while True:
				try:
					msg = await ws.recv()
					data = json.loads(msg)
					
					# 币安K线数据格式
					if 'e' in data and data['e'] == 'kline':
						kline = data['k']
						interval = kline['i']
						# ==================== 处理15分钟K线和状态转换 ====================
						if interval == '15m':
							# 只处理已完结的15m K线
							if not kline['x']:
								continue
							# 解析数据
							o = float(kline['o']); 
							h = float(kline['h']); 
							l = float(kline['l']); 
							c = float(kline['c']);
							change_pct = abs((c - o) / o * 100) if o != 0 else 0
							meets_threshold = change_pct >= 0.21
							candle = [int(kline['t']), kline['o'], kline['h'], kline['l'], kline['c'], kline['v'], '', '', '1']
							print(format_kline(candle, '15分钟', meets_threshold))
							print("-" * 80)
							# 每个15m收盘：如上一周期在监控阶段且未成交，打印未执行原因
							if monitoring_state in ["monitoring_1m", "key_focus"]:
								print(); print("🔄 15分钟周期结束，重置状态，等待下一个信号...")
								if cycle_flags.get('monitoring_started') and not trade_executed:
									reasons = []
									# 分阶段原因判定
									if not cycle_flags.get('breakout_occurred'):
										reasons.append("未出现突破 (K2全程未越过K1高/低)")
									else:
										# 突破出现后的判定
										if cycle_flags.get('final_in_range') is False:
											reasons.append("K2最后一根最后5秒脱离区间")
										elif cycle_flags.get('final_in_range') is True:
											if cycle_flags.get('body_ratio_ok') is False:
												reasons.append("实体比未落入 0.5~1.6")
											elif cycle_flags.get('body_ratio_ok') is True:
												# 条件都满足但仍未执行说明在风控层被拦截
												if not cycle_flags.get('csv_read_ok', True):
													reasons.append("读取CSV失败(文件占用)")
												if not cycle_flags.get('open_position_allowed', True):
													reasons.append("已存在未平仓(仅允许一笔)")
												if not cycle_flags.get('efficient_time_ok', True):
													reasons.append("非高效时间段")
									if not reasons:
										reasons.append("未识别到具体原因(可能逻辑遗漏)")
									print("❌ 本周期未触发成交原因: ")
									for idx, r in enumerate(reasons, 1):
										print(f"   {idx}. {r}")
								print()  # 空行分隔
							# 重置周期状态
							monitoring_state = "waiting_15m"; k15m_reference = None; has_breakout = False; breakout_direction = None
							one_min_count = 0; k2_last_check_done = False; signal_recorded = False
							trade_executed = False; cycle_flags = {}
							# 用当前收盘的15m K线作为新的K1,若满足阈值则立即启动1m监控
							if meets_threshold:
								monitoring_state = "monitoring_1m"
								k15m_reference = {'high': h, 'low': l, 'open': o, 'close': c, 'timestamp': int(kline['t'])}
								k1_strength_pct = change_pct
								has_breakout = False; breakout_direction = None; one_min_count = 0; signal_recorded = False
								cycle_flags = {
									'monitoring_started': True,
									'breakout_occurred': False,
									'final_in_range': None,
									'body_ratio_ok': None,
									'csv_read_ok': True,
									'open_position_allowed': True,
									'efficient_time_ok': True,
								}
								principal, fee, actual_margin, order_amount, guaranteed_margin = calculate_trade_amount(change_pct)
								print()
								print("🎯 " + "=" * 70)
								print("   触发监听！开始监控1分钟K线")
								print(f"   参考区间: 高 {h:.2f} | 低 {l:.2f}")
								print(f"   K1强度: {change_pct:.4f}% | 本金: {principal:.2f}U | 净本金: {actual_margin:.4f}U | 下单金额: {order_amount:.4f}U | 保证金: {guaranteed_margin:.4f}U")
								print("=" * 70)
								print()
						# ==================== 处理1分钟K线 ====================
						elif interval == '1m':
							# 只在监控状态下处理
							if monitoring_state not in ["monitoring_1m", "key_focus"]:
								continue
							
							# 获取K线数据
							h = float(kline['h'])
							l = float(kline['l'])
							o = float(kline['o'])
							c = float(kline['c'])
							ts = int(kline['t'])
							is_closed = kline['x']
							
							# 【关键修复】只处理时间戳晚于触发K1的1分钟K线
							# 15分钟K线时间戳是开盘时间,收盘时间 = 时间戳 + 15*60*1000
							# 下一个周期的1分钟K线时间戳应该 >= 15分钟K线收盘时间
							k15m_close_time = k15m_reference['timestamp'] + 15 * 60 * 1000
							if ts < k15m_close_time:
								continue  # 跳过属于上一个15分钟周期的1分钟K线
							
							# 已完结的K线才计数
							if is_closed:
								one_min_count += 1
								# 打印当前1分钟K线时间与开盘价（以及OHLC）
								kline_time_str = datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M:%S')
								print(f"🕒 1m第{one_min_count:02d}根 | 时间 {kline_time_str} | 开 {o:.2f} 高 {h:.2f} 低 {l:.2f} 收 {c:.2f}")
								
								# 检测突破（向上/向下）
								breakout_up = h > k15m_reference['high']
								breakout_down = l < k15m_reference['low']
								# 仅在当前15m周期内首次发生突破时打印（去重）
								if (breakout_up or breakout_down) and not has_breakout:
									# 同时上下突破，打印两次并优先记录向下方向
									if breakout_up and breakout_down:
										print(f"⚡ 第{one_min_count}根1分钟K线发生突破！向上突破")
										print(f"   当前价: 高 {h:.2f} | 低 {l:.2f}")
										print(f"   参考区间: 高 {k15m_reference['high']:.2f} | 低 {k15m_reference['low']:.2f}")
										print("-" * 80)
										print(f"⚡ 第{one_min_count}根1分钟K线发生突破！向下突破（优先）")
										print(f"   当前价: 高 {h:.2f} | 低 {l:.2f}")
										print(f"   参考区间: 高 {k15m_reference['high']:.2f} | 低 {k15m_reference['low']:.2f}")
										print("-" * 80)
										breakout_direction = 'down'
									else:
										print(f"⚡ 第{one_min_count}根1分钟K线发生突破！")
										print(f"   方向: {'向上突破' if breakout_up else '向下突破（优先）'}")
										print(f"   当前价: 高 {h:.2f} | 低 {l:.2f}")
										print(f"   参考区间: 高 {k15m_reference['high']:.2f} | 低 {k15m_reference['low']:.2f}")
										print("-" * 80)
										breakout_direction = 'down' if breakout_down else 'up'
									has_breakout = True
									if cycle_flags.get('monitoring_started'):
										cycle_flags['breakout_occurred'] = True
								
								# 突破发生后，在第14根K线时切换到关键关注状态
								if monitoring_state == "monitoring_1m" and has_breakout and one_min_count == 14:
									monitoring_state = "key_focus"
									k2_last_check_done = False
									print()
									print("🔥 " + "=" * 70)
									print(f"   ⭐⭐⭐ 重点关注信号！⭐⭐⭐")
									print(f"   进入最后一根K线监控阶段")
									print(f"   突破方向: {'向上' if breakout_direction == 'up' else '向下'}")
									print(f"   当前价: 高 {h:.2f} | 低 {l:.2f}")
									print(f"   区间: {k15m_reference['low']:.2f} - {k15m_reference['high']:.2f}")
									print(f"   等待K2最后一根1分钟K线的最后5秒检查...")
									print("=" * 70)
									print()
							
							# K2最后一根1分钟K线的最后5秒检查（进行中的K线）
							if monitoring_state == "key_focus" and one_min_count == 14 and not signal_recorded and not is_closed:
								# 计算K线剩余时间
								current_time = datetime.now().timestamp() * 1000
								kline_end_time = ts + 60000  # 1分钟 = 60000ms
								time_remaining = (kline_end_time - current_time) / 1000
								
								# 最后5秒内持续检查
								if time_remaining <= 5:
									# 检查是否仍在K1区间内
									still_in_range = (l >= k15m_reference['low'] and h <= k15m_reference['high'])
									
									if still_in_range:
										# 计算K2实体柱与K1实体柱的比值
										k1_body = abs(k15m_reference['close'] - k15m_reference['open'])
										k2_body = abs(c - o)  # K2的实体：当前价格 - K2开盘价
										
										# 避免除零错误
										if k1_body == 0:
											body_ratio = 0
										else:
											body_ratio = k2_body / k1_body
										
										# 检查实体柱比值是否在0.5-1.6之间
										body_ratio_valid = True or (0.5 <= body_ratio <= 1.6)
										
										if cycle_flags.get('monitoring_started'):
											cycle_flags['final_in_range'] = True
										if body_ratio_valid:
											# 确定交易方向（反向逻辑）
											if breakout_direction == 'up':
												trade_direction = "做空"  # 向上突破后回归，做空
											else:
												trade_direction = "做多"  # 向下突破后回归，做多
											
											entry_price = c  # 使用当前收盘价作为入场价
											
											# 风险控制：检查持仓情况，判断是开仓还是加仓
											position_info = get_open_position_info()
											
											# None表示读取失败，{}表示无持仓，dict表示有持仓
											if position_info is None:
												print("⚠ 无法读取持仓信息（文件占用或异常），为安全起见跳过本次操作")
												signal_recorded = True
												if cycle_flags.get('monitoring_started'):
													cycle_flags['csv_read_ok'] = False
												continue
											
											# 判断是否为加仓操作
											is_add_position = False
											main_trade_id = None
											add_count = 0
											
											# position_info 为空字典{}表示无持仓，可以正常开仓
											if position_info:  # 有持仓
												# 已有持仓，检查方向是否一致
												if position_info['direction'] == trade_direction:
													# 方向一致，检查加仓次数
													current_add_count = position_info['add_count']
													if current_add_count >= 5:
														print("⛔ 跳过加仓：已达到最大加仓次数 (5次)")
														print(f"   拟交易方向: {trade_direction} | 拟入场价: {entry_price:.2f}")
														signal_recorded = True
														if cycle_flags.get('monitoring_started'):
															cycle_flags['open_position_allowed'] = False
														continue
													else:
														# 可以加仓
														is_add_position = True
														main_trade_id = position_info['trade_id']
														add_count = current_add_count + 1
														print(f"📈 检测到同方向持仓，准备第{add_count}次加仓")
												else:
													# 方向不一致，不允许开新仓
													print("⛔ 跳过买入：当前有反向持仓，不允许开新仓")
													print(f"   当前持仓: {position_info['direction']} | 拟开仓方向: {trade_direction}")
													signal_recorded = True
													if cycle_flags.get('monitoring_started'):
														cycle_flags['open_position_allowed'] = False
													continue
											else:
												# 无持仓，正常开仓
												print(f"💼 当前无持仓，准备开仓")
											
											# 限定高效时间段（仅对首次开仓限制，加仓不限制）
											if not is_add_position and not is_in_efficient_time():
												print("⏱ 非高效时间段，跳过本次买入")
												print(f"   拟交易方向: {trade_direction} | 拟入场价: {entry_price:.2f}")
												signal_recorded = True
												if cycle_flags.get('monitoring_started'):
													cycle_flags['efficient_time_ok'] = False
												# continue

											print()
											print("🎯 " + "=" * 70)
											if is_add_position:
												print(f"   📈 加仓信号确认！(第{add_count}次加仓)")
											else:
												print(f"   💰 交易信号确认！")
											print(f"   方向: {trade_direction}")
											print(f"   入场价: {entry_price:.2f}")
											print(f"   理由: K2最后一根1分钟K线在最后{time_remaining:.1f}秒时仍在K1区间内")
											print(f"   K1实体: {k1_body:.2f} | K2实体: {k2_body:.2f} | 比值: {body_ratio:.2f}")
											print(f"   K1区间: {k15m_reference['low']:.2f} - {k15m_reference['high']:.2f}")
											print(f"   当前价位: 高 {h:.2f} | 低 {l:.2f} | 收 {c:.2f}")
											print("=" * 70)
											print()
											
											# 计算金额信息
											principal, fee, actual_margin, order_amount, guaranteed_margin = calculate_trade_amount(k1_strength_pct)
											
											# 🚀 实盘下单
											try:
												# 转换方向
												direction_cn = "多" if trade_direction == "做多" else "空"
												
												# 调用一键开仓（或加仓）
												order_info = trader.open_position(
													symbol="ETHUSDC",
													direction=direction_cn,
													usdc_amount=principal,
													leverage=LEVERAGE
												)
												
												# 获取订单详情
												order_id = str(order_info['order'].get('orderId'))
												actual_qty = float(order_info['order'].get('executedQty', 0))
												avg_price = float(order_info['order'].get('avgPrice', entry_price))
												
												print(f"✅ 实盘下单成功!")
												print(f"   订单ID: {order_id}")
												print(f"   成交数量: {actual_qty}")
												print(f"   成交均价: {avg_price:.2f}")
												
												# 查询实际持仓信息
												positions = trader.get_position("ETHUSDC")
												position_side = "LONG" if trade_direction == "做多" else "SHORT"
												actual_position = None
												for pos in positions:
													if pos.get('positionSide') == position_side:
														actual_position = pos
														break
												
												if actual_position:
													actual_entry = float(actual_position.get('entryPrice', avg_price))
													actual_amount = float(actual_position.get('positionAmt', actual_qty))
													print(f"   持仓入场价: {actual_entry:.2f}")
													print(f"   持仓数量: {abs(actual_amount)}")
													
													# 设置止盈订单(330%)
													try:
														trader.set_take_profit(
															symbol="ETHUSDC",
															position_side=position_side,
															quantity=abs(actual_amount),
															take_profit_pct=TAKE_PROFIT_PCT
														)
													except Exception as e:
														print(f"⚠ 止盈设置失败: {e}")
												
												# 写入交易日志(使用实际成交信息)
												trade_id = write_trade_log(
													trade_direction,
													entry_price,
													k15m_reference['open'], k15m_reference['high'], k15m_reference['low'], k15m_reference['close'],
													o, h, l, c,
													breakout_direction,
													k1_strength_pct,
													int(current_time),
													trade_id=order_id,
													actual_qty=actual_qty,
													actual_price=avg_price,
													is_add_position=is_add_position,
													main_trade_id=main_trade_id,
													add_count=add_count
												)
												
												# 构造通知
												if is_add_position:
													title = f"ETH-{trade_direction}-加仓{add_count}-投入{principal:.2f}U"
												else:
													title = f"ETH-{trade_direction}-开仓-投入{principal:.2f}U"
												content_lines = [
													f"订单ID: {order_id}",
													f"时间: {datetime.fromtimestamp(int(current_time)/1000).strftime('%Y-%m-%d %H:%M:%S')}",
													f"操作: {'第'+str(add_count)+'次加仓' if is_add_position else '开仓'}",
													f"方向: {trade_direction}",
													f"成交价: {avg_price:.2f}",
													f"成交量: {actual_qty}",
													f"K1强度: {k1_strength_pct:.4f}%", 
													f"K1区间: {k15m_reference['low']:.2f} - {k15m_reference['high']:.2f}",
													f"突破方向: {'向上' if breakout_direction=='up' else '向下'} -> 反向 {trade_direction}",
													f"K2/K1实体比: {body_ratio:.2f}",
													f"本金: {principal:.2f}U  手续费: {fee:.4f}U", 
													f"净本金: {actual_margin:.4f}U  下单金额: {order_amount:.4f}U", 
													f"保证金: {guaranteed_margin:.4f}U",
													f"止盈设置: {TAKE_PROFIT_PCT}%",
												]
												if is_add_position:
													content_lines.insert(2, f"主仓位ID: {main_trade_id}")
												content = "\n".join(content_lines)
												# 发送微信通知
												send_wechat_notification(title, content)
												
												signal_recorded = True
												trade_executed = True
												
											except Exception as e:
												print(f"❌ 下单失败: {e}")
												print("   跳过本次交易")
												signal_recorded = True
										else:
											# 实体柱比值不满足条件
											if not signal_recorded:
												print(f"⚠ K2实体柱比值不满足条件: {body_ratio:.2f} (要求: 0.5-1.6)")
												print(f"   K1实体: {k1_body:.2f} | K2实体: {k2_body:.2f}")
												signal_recorded = True  # 标记避免重复打印
												if cycle_flags.get('monitoring_started'):
													cycle_flags['body_ratio_ok'] = False
									elif time_remaining <= 1 and not signal_recorded:
										# 如果最后1秒仍未满足条件，记录未触发信息
										print(f"⚠ K2最后5秒检查: 价格已脱离K1区间，不生成交易信号")
										print(f"   当前: 高 {h:.2f} | 低 {l:.2f}")
										print(f"   K1区间: {k15m_reference['low']:.2f} - {k15m_reference['high']:.2f}")
										print("-" * 80)
										signal_recorded = True  # 避免重复打印
										if cycle_flags.get('monitoring_started'):
											cycle_flags['final_in_range'] = False
				
				except websockets.exceptions.ConnectionClosed:
					print("⚠ WebSocket 连接已断开，尝试重连...")
					await asyncio.sleep(3)
					break
				except Exception as e:
					print(f"⚠ 发生异常: {e}")
					await asyncio.sleep(1)
	
	except Exception as e:
		print(f"✗ 连接失败: {e}")

if __name__ == "__main__":
	print("启动 ETHUSDC K线监听程序 (Binance)...")
	print("监控所有 15分钟K线")
	print()
	
	while True:
		try:
			asyncio.run(main())
		except KeyboardInterrupt:
			print("\n程序已停止")
			break
		except Exception as e:
			print(f"程序异常: {e}")
			print("3秒后重启...")
			time.sleep(3)
