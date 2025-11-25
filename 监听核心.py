import streamlit as st
import time
import json
import html
import pandas as pd
from datetime import datetime, timezone
from 彬彬监听逻辑 import BoxMonitorBot, BoxSession, BJ_TZ
from 手机控制 import PhoneController

# 设置页面配置
st.set_page_config(
    page_title="ETHUSDT 箱体震荡监控 (中介线版)",
    page_icon="📈",
    layout="wide"
)

# === Streamlit 界面逻辑 ===

@st.cache_resource
def get_session_bot():
    return BoxMonitorBot()

bot = get_session_bot()

# 强制更新实例的类定义
if bot.__class__ is not BoxMonitorBot:
    bot.__class__ = BoxMonitorBot

# 补丁: 确保新属性存在 (针对热重载)
if not hasattr(bot, 'recent_klines'):
    bot.recent_klines = []
if not hasattr(bot, 'real_trading'):
    bot.real_trading = False
if not hasattr(bot, 'amount'):
    bot.amount = "5"
if not hasattr(bot, 'phone'):
    bot.phone = None

# 侧边栏
with st.sidebar:
    st.header("⚙️ 箱体控制 (中介线版)")
    
    active_session = bot.get_active_session()
    defaults = active_session.levels if active_session else {"s_res": 0.0, "w_res": 0.0, "w_sup": 0.0, "s_sup": 0.0, "mid_line": 0.0}
    
    s_res = st.number_input("强压力位 (做空)", value=defaults.get("s_res", 0.0), format="%.2f")
    w_res = st.number_input("弱压力位 (做空)", value=defaults.get("w_res", 0.0), format="%.2f")
    
    st.markdown("---")
    mid_line = st.number_input("📏 中介线 (可选)", value=defaults.get("mid_line", 0.0), format="%.2f", help="填0代表不启用。若启用，将根据前4根K线位置判断是支撑还是压力。")
    st.markdown("---")
    
    w_sup = st.number_input("弱支撑位 (做多)", value=defaults.get("w_sup", 0.0), format="%.2f")
    s_sup = st.number_input("强支撑位 (做多)", value=defaults.get("s_sup", 0.0), format="%.2f")
    
    slippage_limit = st.number_input("最大允许滑点 (USDT)", value=1.0, min_value=0.0, step=0.5)
    box_name = st.text_input("箱体名称 (可选)", value=getattr(active_session, 'name', "") if active_session else "")

    st.markdown("---")
    st.subheader("📱 实盘控制")
    real_trading_on = st.checkbox("开启实盘 (Real Trading)", value=bot.real_trading)
    trade_amount = st.text_input("交易金额 (USDT)", value=bot.amount)
    
    if real_trading_on != bot.real_trading or trade_amount != bot.amount:
        bot.set_real_trading(real_trading_on, trade_amount)
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 启动新箱体", type="primary", use_container_width=True):
            bot.start_new_session(s_res, w_res, w_sup, s_sup, mid_line=mid_line, name=box_name, slippage=slippage_limit)
            st.rerun()
    with col2:
        if st.button("🔄 更新参数", disabled=(active_session is None), use_container_width=True):
            if bot.update_current_session(s_res, w_res, w_sup, s_sup, mid_line=mid_line, name=box_name, slippage=slippage_limit):
                st.success("已更新")
            else:
                st.error("无活动箱体")
    
    if st.button("🛑 停止当前箱体", disabled=(active_session is None), use_container_width=True):
        bot.stop_current_session()
        st.rerun()

    st.markdown("---")
    st.subheader("💾 数据管理")

    # 准备下载数据
    def safe_to_dict(s):
        if hasattr(s, 'to_dict'):
            return s.to_dict()
        else:
            return {
                "id": s.id,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
                "levels": s.levels,
                "active_trades": s.active_trades,
                "history": s.history,
                "logs": s.logs,
                "is_active": s.is_active,
                "stop_reason": getattr(s, 'stop_reason', None),
                "last_trade_time": getattr(s, 'last_trade_time', {"s_res": 0, "w_res": 0, "w_sup": 0, "s_sup": 0})
            }

    json_str = json.dumps([safe_to_dict(s) for s in bot.sessions], ensure_ascii=False, indent=2)
    
    st.download_button(
        label="⬇️ 下载备份",
        data=json_str,
        file_name=f"box_data_{datetime.now(BJ_TZ).strftime('%Y%m%d_%H%M')}.json",
        mime="application/json",
        use_container_width=True
    )
    
    uploaded_file = st.file_uploader("上传备份文件恢复", type=["json"], label_visibility="collapsed")
    if uploaded_file is not None:
        try:
            data = json.load(uploaded_file)
            with bot.lock:
                bot.sessions = [BoxSession.from_dict(d) for d in data]
            st.success(f"成功恢复 {len(bot.sessions)} 个箱体记录！")
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.error(f"文件格式错误: {e}")

    st.markdown("---")
    st.subheader("📱 手机控制")
    
    phone = PhoneController()
    
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        if st.button("⬆️ 上滑", use_container_width=True):
            phone.swipe_up()
    with col_p2:
        if st.button("⬇️ 下滑", use_container_width=True):
            phone.swipe_down()
            
    if st.button("⌨️ 关闭键盘", use_container_width=True):
        phone.close_keyboard()
        
    input_val = st.text_input("输入内容", key="phone_input", label_visibility="collapsed", placeholder="输入数字/文本")
    if st.button("发送输入", use_container_width=True):
        if input_val:
            phone.input_text(input_val)
            st.success(f"已发送: {input_val}")

    st.markdown("---")
    st.markdown("**系统状态:**")
    if bot.running:
        st.success("🟢 正在运行")
        try:
            start_time = bot.bot_start_time
        except AttributeError:
            start_time = datetime.now(BJ_TZ)
            bot.bot_start_time = start_time
            
        if start_time.tzinfo is None:
             uptime = datetime.now() - start_time
        else:
             uptime = datetime.now(BJ_TZ) - start_time

        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{hours}小时 {minutes}分"
        if days > 0:
            time_str = f"{days}天 {time_str}"
        st.caption(f"已连续运行: {time_str}")
        st.caption(f"最后刷新: {datetime.now(BJ_TZ).strftime('%H:%M:%S')}")
        
        # 显示最近K线状态
        if bot.recent_klines:
            last = bot.recent_klines[-1]
            st.caption(f"上一根K线: O={last['o']}, H={last['h']}, L={last['l']}, C={last['c']}")
            st.caption(f"缓存K线数: {len(bot.recent_klines)}")
        else:
            st.caption("等待K线数据...")
            
    else:
        st.error("🔴 已停止")
        stop_reason = getattr(bot, 'stop_reason', None)
        if stop_reason:
            st.warning(f"停止原因: {stop_reason}")

# 主界面
st.title("📊 ETHUSDT 箱体震荡实盘监控 (中介线版)")

# 顶部状态栏
active_session = bot.get_active_session()
status_color = "green" if active_session else "red"
status_text = f"运行中 (箱体 #{active_session.id})" if active_session else "已停止"

last_update = getattr(bot, 'last_ws_update', 0)
latency = time.time() - last_update if last_update > 0 else 999
latency_color = "green" if latency < 2 else "red"
latency_text = f"{latency:.1f}s" if last_update > 0 else "无数据"

st.markdown(f"### 状态: :{status_color}[{status_text}] | 当前价格: **{bot.current_price:.2f}** | 延迟: :{latency_color}[{latency_text}]")

# 箱体列表展示
if not bot.sessions:
    st.info("暂无箱体记录，请在左侧启动新箱体。")
else:
    for session in reversed(bot.sessions):
        start_str = session.start_time.strftime('%Y-%m-%d %H:%M:%S')
        status_icon = "🟢" if session.is_active else "🔴"
        title = f"{status_icon} {start_str} | 箱体 #{session.id}"
        
        is_expanded = session.is_active or (session == bot.sessions[-1])
        
        with st.expander(title, expanded=is_expanded):
            total = len(session.history)
            wins = len([t for t in session.history if t["status"] == "WIN"])
            rate = (wins / total * 100) if total > 0 else 0.0
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("箱体状态", "活跃" if session.is_active else "已结束")
            c2.metric("总交易", total)
            c3.metric("胜率", f"{rate:.1f}%")
            c4.metric("停止原因", session.stop_reason if session.stop_reason else "-")

            if not session.is_active:
                session_json = json.dumps(safe_to_dict(session), ensure_ascii=False, indent=2)
                st.download_button(
                    label="⬇️ 下载该箱体记录",
                    data=session_json,
                    file_name=f"box_{session.id}_{session.start_time.strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                    key=f"dl_{session.id}"
                )

            tab_trades, tab_logs = st.tabs(["📜 交易记录", "📝 运行日志"])
            
            with tab_trades:
                all_display_data = []
                current_price = bot.current_price

                if session.active_trades:
                    for t in session.active_trades:
                        remaining = int(t['expiry_time'] - time.time())
                        if remaining < 0: remaining = 0
                        mins, secs = divmod(remaining, 60)
                        countdown_str = f"{mins:02d}:{secs:02d}"
                        
                        if t['direction'] == "LONG":
                            pnl = current_price - t['entry_price']
                        else:
                            pnl = t['entry_price'] - current_price
                        
                        if pnl > 0:
                            pnl_text = "🟢浮盈"
                        elif pnl < 0:
                            pnl_text = "🔴浮亏"
                        else:
                            pnl_text = "⚪持平"
                        
                        status_combined = f"持仓中 ({countdown_str}) ({pnl_text})"
                        
                        all_display_data.append({
                            "开仓时间": datetime.fromtimestamp(t['entry_time'], BJ_TZ).strftime('%H:%M:%S'),
                            "开仓价格": f"{t['entry_price']:.2f}",
                            "方向": "做多" if t['direction'] == "LONG" else "做空",
                            "状态": status_combined,
                            "原因": t['reason'],
                            "平仓/当前价": f"{current_price:.2f}",
                            "累计胜率": "-",
                            "失败原因": "-",
                            "sort_time": t['entry_time']
                        })

                if session.history:
                    df_hist = pd.DataFrame(session.history)
                    df_hist['is_win'] = df_hist['status'] == 'WIN'
                    df_hist['cumsum_win'] = df_hist['is_win'].cumsum()
                    df_hist['row_num'] = range(1, len(df_hist) + 1)
                    df_hist['cum_win_rate'] = (df_hist['cumsum_win'] / df_hist['row_num']) * 100
                    
                    for _, row in df_hist.iterrows():
                        fail_reason = "-"
                        if row['status'] == 'LOSS':
                            mapping = {
                                "s_res": "离开强压力位", "w_res": "离开弱压力位",
                                "s_sup": "离开强支撑位", "w_sup": "离开弱支撑位",
                                "mid_res": "离开中介线压力", "mid_sup": "离开中介线支撑"
                            }
                            fail_reason = mapping.get(row.get('level_key'), "未知")
                        
                        status_cn = "✅ 胜" if row['status'] == 'WIN' else "❌ 负"
                        prev_info = f" (前价: {row.get('prev_price', '-')})" if row.get('prev_price') else ""

                        entry_time_display = row.get('entry_time_str', '-')
                        if row.get('entry_time'):
                            try:
                                entry_time_display = datetime.fromtimestamp(row['entry_time'], BJ_TZ).strftime('%H:%M:%S')
                            except:
                                pass

                        all_display_data.append({
                            "开仓时间": entry_time_display,
                            "开仓价格": f"{row['entry_price']:.2f}",
                            "方向": "做多" if row['direction'] == "LONG" else "做空",
                            "状态": status_cn,
                            "原因": f"{row['reason']}{prev_info}",
                            "平仓/当前价": f"{row['exit_price']:.2f}",
                            "累计胜率": f"{row['cum_win_rate']:.1f}%",
                            "失败原因": fail_reason,
                            "sort_time": row['entry_time']
                        })

                if all_display_data:
                    df_display = pd.DataFrame(all_display_data)
                    df_display = df_display.sort_values('sort_time', ascending=False).drop(columns=['sort_time'])
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.info("暂无交易记录")
                    
            with tab_logs:
                log_text = "\n".join(session.logs)
                st.markdown(
                    f"""
                    <div style="
                        height: 300px;
                        overflow-y: auto;
                        background-color: rgba(0, 0, 0, 0.2);
                        color: inherit;
                        padding: 10px;
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        border-radius: 5px;
                        font-family: monospace;
                        font-size: 0.8em;
                        white-space: pre-wrap;
                        display: flex;
                        flex-direction: column-reverse; 
                    ">{html.escape(log_text)}</div>
                    """,
                    unsafe_allow_html=True
                )

if bot.running:
    time.sleep(0.5)
    st.rerun()
