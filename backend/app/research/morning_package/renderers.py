"""Artifact renderers for morning packages."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import MorningPackage


INTENT_FIELDS = [
    "symbol",
    "side",
    "decision",
    "manual_action",
    "thesis",
    "evidence_sources",
    "backtest_window",
    "max_drawdown",
    "position_cap",
    "failure_condition",
    "downgrade_reasons",
    "auxiliary_notes",
]


def render_markdown(package: MorningPackage) -> str:
    lines = [
        f"# A-share Morning Package - {package.package_date.isoformat()}",
        "",
        "## Audit Header",
        f"- date: {package.package_date.isoformat()}",
        f"- dataset version: {package.dataset_version or 'missing'}",
        f"- code commit: {package.code_commit or 'missing'}",
        f"- manifest hash: {package.manifest_hash or 'missing'}",
        f"- config hash: {package.config_hash or 'missing'}",
        f"- generated at: {package.generated_at.isoformat()}",
        "",
        "## Today's Decision",
        package.decision.value,
        "",
        "## Market State",
        f"- risks: {', '.join(package.risk.market_risks) or 'none recorded'}",
        f"- anomalies: {', '.join(package.risk.anomalies) or 'none recorded'}",
        f"- data integrity: {', '.join(package.risk.data_integrity) or 'not recorded'}",
        "",
        "## Portfolio Risk Before Return",
        f"- limits: {', '.join(package.risk.portfolio_limits) or 'position caps enforced per intent'}",
        "",
        "## Trading Opportunities",
    ]
    if not package.intents:
        lines.append("- No eligible opportunities.")
    for intent in package.intents:
        evidence = []
        if intent.evidence_chain:
            evidence = [
                f"{source.kind.value}:{source.source_id}"
                for source in intent.evidence_chain.authoritative_sources()
            ]
        lines.extend(
            [
                f"### {intent.symbol} {intent.side}",
                f"- decision: {intent.decision.value}",
                f"- manual action: {intent.manual_action.value}",
                f"- thesis: {intent.thesis}",
                f"- evidence chain: {', '.join(evidence) or 'insufficient'}",
                f"- backtest window: {intent.backtest.window if intent.backtest else 'missing'}",
                f"- max drawdown: {intent.backtest.max_drawdown if intent.backtest else 'missing'}",
                f"- position cap: {intent.position_cap if intent.position_cap is not None else 'missing'}",
                f"- failure condition: {intent.failure_condition or 'missing'}",
                f"- downgrade reasons: {', '.join(intent.downgrade_reasons) or 'none'}",
                "",
            ]
        )

    lines.extend(
        [
            "## Why We Do Not Trade Today",
            *[f"- {reason}" for reason in package.do_not_trade_reasons],
            "",
            "## Audit Appendix",
            "```json",
            json.dumps(package.audit, ensure_ascii=False, sort_keys=True, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_artifacts(package: MorningPackage, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(package)
    (out_dir / "morning_package.md").write_text(markdown, encoding="utf-8")
    (out_dir / "audit.json").write_text(
        json.dumps(package.to_audit_payload(), ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    rows = [intent.to_table_row() for intent in package.intents]
    (out_dir / "trading_intents.json").write_text(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    with (out_dir / "trading_intents.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INTENT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_pdf(markdown, out_dir / "morning_package.pdf")


def write_pdf(markdown: str, path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    x = 40
    y = height - 40
    pdf.setFont("Helvetica", 10)
    for raw_line in markdown.splitlines():
        line = raw_line.encode("latin-1", errors="replace").decode("latin-1")
        if y < 40:
            pdf.showPage()
            pdf.setFont("Helvetica", 10)
            y = height - 40
        pdf.drawString(x, y, line[:115])
        y -= 14
    pdf.save()
