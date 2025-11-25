#!/usr/bin/env python3
"""
异步连接 Binance `compositeIndex` 流并打印价格与成分信息。

用法示例:
    py composite_index_ws.py --symbol DEFIUSDT --stream compositeIndex

依赖: `websockets` (已在 `requirements.txt` 中列出)
"""
import asyncio
import json
import logging
import argparse
import sys

import websockets


async def listen(
    symbol: str,
    stream: str = "compositeIndex",
    reconnect_delay: float = 5.0,
    max_conn_seconds: int = 23 * 3600,
    ping_every: int = 9 * 60,
    raw: bool = False,
    expect_interval_ms: int | None = None,
):
    """连接并监听指定的 `<symbol>@<stream>` 流，打印价格与成分信息。

    - `stream`：例如 `compositeIndex`、`trade`、`markPrice` 等。
    - `max_conn_seconds`：最大连接时长（秒），达到后主动断开并重连（小于 24 小时）。
    - `ping_every`：客户端主动发送 ping 的间隔（秒），用于保持连接（可选）。
    - `raw`：如果为 True，则打印原始消息字符串。
    """
    # 支持多个 stream（逗号分隔），以及根据 stream 类型选择正确的 Base URL
    streams = [s.strip() for s in stream.split(",") if s.strip()]
    stream_names = [f"{symbol.lower()}@{s.lower()}" for s in streams]

    # 默认使用 fstream (Futures)。
    # 注意：之前的逻辑强制 compositeIndex 使用 spot，但这对于 DEFIUSDT 等合约指数是不正确的。
    # 如果确实需要连接 Spot 接口，建议后续增加参数控制。
    use_spot_base = False

    if len(stream_names) == 1:
        # 对于 spot 的 host 使用 :9443（与历史示例一致）
        base = "wss://stream.binance.com:9443/ws" if use_spot_base else "wss://fstream.binance.com/ws"
        uri = f"{base}/{stream_names[0]}"
    else:
        # 组合 streams 使用 /stream?streams=... 格式；选择合适的 host（不能跨域）
        base = "wss://stream.binance.com:9443/stream?streams=" if use_spot_base else "wss://fstream.binance.com/stream?streams="
        uri = base + "/".join(stream_names)

    while True:
        try:
            logging.info(f"Connecting to {uri}")
            # 使用 None 禁用库的周期性 ping（我们会自己发送 ping）
            async with websockets.connect(uri, ping_interval=None, ping_timeout=None) as ws:
                logging.info("Connected")

                async def periodic_ping():
                    try:
                        while True:
                            await asyncio.sleep(ping_every)
                            logging.debug("发送客户端 ping 以保持连接")
                            try:
                                await ws.ping()
                            except Exception:
                                logging.exception("发送 ping 失败，停止 ping 任务")
                                return
                    except asyncio.CancelledError:
                        return

                ping_task = asyncio.create_task(periodic_ping())

                async def schedule_close():
                    await asyncio.sleep(max_conn_seconds)
                    logging.info("达到最大连接时长，主动关闭连接以触发重连")
                    try:
                        await ws.close()
                    except Exception:
                        pass

                closer_task = asyncio.create_task(schedule_close())

                try:
                    last_recv = None
                    async for message in ws:
                        recv_time = asyncio.get_event_loop().time()
                        if raw:
                            print(message)
                        try:
                            payload = json.loads(message)
                        except Exception:
                            logging.exception("无法解析消息为 JSON")
                            continue

                        # 组合 streams 的 payload 结构为 {"stream":"<name>","data":{...}}
                        if isinstance(payload, dict) and "stream" in payload and "data" in payload:
                            stream_from = payload.get("stream")
                            data = payload.get("data")
                        else:
                            stream_from = None
                            data = payload

                        # 兼容不同 stream 的字段；尽量从 payload 中抽取常见字段
                        event_type = data.get("e") if isinstance(data, dict) else None
                        event_time = data.get("E") if isinstance(data, dict) else None
                        symbol_resp = data.get("s") if isinstance(data, dict) else None
                        price = data.get("p") if isinstance(data, dict) else None
                        index_price = data.get("i") if isinstance(data, dict) else None
                        components = data.get("c") if isinstance(data, dict) else None

                        if last_recv is not None:
                            delta_ms = int((recv_time - last_recv) * 1000)
                        else:
                            delta_ms = None
                        last_recv = recv_time

                        header = f"[{stream_from}]" if stream_from else f"[{symbol_resp}]"
                        
                        # 构建输出信息
                        output_parts = [f"{header}", f"event={event_type}", f"time={event_time}"]
                        if price:
                            output_parts.append(f"price={price}")
                        if index_price:
                            output_parts.append(f"index_price={index_price}")
                        
                        if delta_ms is None:
                            output_parts.append("recv=now")
                        else:
                            output_parts.append(f"delta_ms={delta_ms}")
                            
                        print(" ".join(output_parts))

                        # 若设置了预期消息间隔阈值，则在超过阈值时打印警告
                        if expect_interval_ms is not None and delta_ms is not None and delta_ms > expect_interval_ms:
                            logging.warning(f"消息间隔 {delta_ms}ms 超过期望 {expect_interval_ms}ms")

                        if components and isinstance(components, list):
                            for comp in components:
                                b = comp.get("b")
                                q = comp.get("q")
                                w = comp.get("w")
                                W = comp.get("W")
                                i = comp.get("i")
                                print(f"  - {b}/{q} weight={w} weight_ratio={W} index_price={i}")
                finally:
                    ping_task.cancel()
                    closer_task.cancel()
                    try:
                        await asyncio.gather(ping_task, closer_task, return_exceptions=True)
                    except Exception:
                        pass
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.InvalidURI, ConnectionRefusedError) as e:
            logging.warning(f"连接关闭或被拒绝: {e}。{reconnect_delay}s后重连...")
            await asyncio.sleep(reconnect_delay)
        except KeyboardInterrupt:
            logging.info("用户中断，退出")
            return
        except Exception:
            logging.exception("发生未处理的异常，稍后重连")
            await asyncio.sleep(reconnect_delay)


def main():
    parser = argparse.ArgumentParser(description="Binance compositeIndex websocket 客户端")
    parser.add_argument("--symbol", "-s", default="DEFIUSDT", help="交易对，例如 DEFIUSDT")
    parser.add_argument("--reconnect-delay", "-r", type=float, default=5.0, help="重连等待秒数")
    parser.add_argument("--stream", "-t", default="compositeIndex", help="stream 类型，例如 compositeIndex, trade, markPrice")
    parser.add_argument("--raw", action="store_true", help="打印原始消息字符串（同时仍尝试解析 JSON 并打印解析后的摘要）")
    parser.add_argument("--max-conn-seconds", type=int, default=23 * 3600, help="最大连接时长（秒），到期将主动重连，建议 < 24h）")
    parser.add_argument("--ping-every", type=int, default=9 * 60, help="客户端主动发送 ping 的间隔（秒），用于保持连接）")
    parser.add_argument("--debug", action="store_true", help="启用 websockets 调试日志")
    parser.add_argument("--expect-interval-ms", type=int, default=None, help="可选：期望的消息间隔（毫秒），超过则打印警告）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.debug:
        logging.getLogger("websockets").setLevel(logging.DEBUG)

    try:
        asyncio.run(
            listen(
                args.symbol,
                stream=args.stream,
                reconnect_delay=args.reconnect_delay,
                max_conn_seconds=args.max_conn_seconds,
                ping_every=args.ping_every,
                raw=args.raw,
                expect_interval_ms=args.expect_interval_ms,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
