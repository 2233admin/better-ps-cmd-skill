"""Export manually confirmed morning-package intents to the EasyXT bridge contract."""

from __future__ import annotations

from quant_terminal.trade.intent import make_trade_intent

from app.research.morning_package.models import ManualAction, MorningPackage, PackageDecision


def to_easyxt_bridge_payloads(
    package: MorningPackage,
    *,
    account: str,
    qty_by_symbol: dict[str, int],
    strategy: str = "morning_package",
    dry_run: bool = True,
) -> list[dict]:
    """Export confirmed trade rows without letting research call a broker SDK."""

    payloads: list[dict] = []
    if package.decision != PackageDecision.TRADE:
        return payloads

    for intent in package.intents:
        if intent.decision != PackageDecision.TRADE:
            continue
        if intent.manual_action != ManualAction.CONFIRM:
            continue
        qty = qty_by_symbol.get(intent.symbol)
        if qty is None:
            continue
        payloads.append(
            make_trade_intent(
                request_id=f"{strategy}-{package.package_date.isoformat()}-{intent.symbol}",
                strategy=strategy,
                account=account,
                symbol=intent.symbol,
                side=intent.side,
                qty=qty,
                dry_run=dry_run,
                note=intent.thesis,
            ).to_bridge_payload()
        )
    return payloads
