"""WebSocket 实时推送"""

import asyncio
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # channel -> list of connections
        self.channels: dict[str, list[WebSocket]] = {
            "quotes": [],
            "trades": [],
            "strategy": [],
            "alerts": [],
        }

    async def connect(self, websocket: WebSocket, channel: str):
        await websocket.accept()
        if channel not in self.channels:
            self.channels[channel] = []
        self.channels[channel].append(websocket)
        logger.info(f"WS connected: {channel} (total: {len(self.channels[channel])})")

    def disconnect(self, websocket: WebSocket, channel: str):
        if channel in self.channels:
            self.channels[channel] = [
                ws for ws in self.channels[channel] if ws != websocket
            ]
            logger.info(f"WS disconnected: {channel}")

    async def broadcast(self, channel: str, data: Any):
        """向指定频道的所有连接广播消息"""
        if channel not in self.channels:
            return

        dead = []
        message = json.dumps(data, default=str, ensure_ascii=False)

        for ws in self.channels[channel]:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        # 清理断开的连接
        for ws in dead:
            self.channels[channel] = [
                w for w in self.channels[channel] if w != ws
            ]

    def get_subscriber_count(self, channel: str) -> int:
        return len(self.channels.get(channel, []))


# 全局连接管理器
ws_manager = ConnectionManager()


async def ws_quotes_handler(websocket: WebSocket):
    """行情 WebSocket 处理器"""
    await ws_manager.connect(websocket, "quotes")
    try:
        while True:
            # 接收客户端消息（如订阅/取消订阅）
            data = await websocket.receive_text()
            # 可以处理客户端的订阅请求
            logger.debug(f"WS quotes received: {data}")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "quotes")


async def ws_trades_handler(websocket: WebSocket):
    """成交 WebSocket 处理器"""
    await ws_manager.connect(websocket, "trades")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "trades")


async def ws_strategy_handler(websocket: WebSocket):
    """策略信号 WebSocket 处理器"""
    await ws_manager.connect(websocket, "strategy")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "strategy")


async def ws_alerts_handler(websocket: WebSocket):
    """告警 WebSocket 处理器"""
    await ws_manager.connect(websocket, "alerts")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "alerts")
