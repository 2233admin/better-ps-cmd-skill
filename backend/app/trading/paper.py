"""Paper execution helpers."""

from __future__ import annotations


class PaperExecution:
    """In-memory execution that never calls a broker adapter."""

    def fill(self, order) -> None:
        order.status = order.status.FILLED
        order.filled_price = order.price
        order.filled_volume = order.volume
