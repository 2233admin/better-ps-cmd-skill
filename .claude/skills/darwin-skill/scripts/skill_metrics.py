#!/usr/bin/env python3
"""
darwin-skill 量化打分工具
用 vectorbt.pro 的量化分析思路，对 skill 优化进行度量
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RoundMetrics:
    """单轮指标"""
    round_num: int
    old_score: float
    new_score: float
    skill_return: float       # 收益率（类比策略）
    is_improvement: bool      # 是否正向
    dimension: str             # 改了什么维度
    eval_mode: str            # full_test / dry_run


@dataclass
class SkillMetrics:
    """完整指标集"""
    skill_name: str
    total_rounds: int
    keeps: int
    reverts: int

    # === 收益类指标 (类比 vectorbt.portfolio returns) ===
    total_return: float       # 总收益率 (new_final / old_baseline - 1)
    momentum: float           # 动量（最后一轮 vs 第一轮）
    volatility: float        # 波动率（每轮变化的标准差）

    # === 风险调整指标 (类比 quantstats) ===
    optimization_efficiency: float  # 优化效率（类比夏普比率）
    downside_protection: float     # 防御效率（类比索提诺比率）
    marginal_gain: float            # 边际收益（类比卡玛比率）
    regression_depth: float        # 回退幅度（类比最大回撤）
    regression_duration: int        # 回退持续轮数

    # === 质量指标 ===
    retention_rate: float          # 留存率（类比胜率 keep / total）
    avg_improvement: float        # 平均每轮提升
    consistency: float             # 一致性（正向轮次 / 总轮次）
    avg_round_score: float         # 平均轮次得分
    best_round: int               # 最佳轮次
    worst_round: int              # 最差轮次

    # === 综合评分 ===
    overall_score: float     # 综合评分（0-100）
    grade: str              # 评级 A/B/C/D/F


def parse_tsv(tsv_path: str) -> list[dict]:
    """解析 results.tsv"""
    rows = []
    with open(tsv_path, encoding='utf-8') as f:
        header = f.readline().strip().split('\t')
        for line in f:
            fields = line.strip().split('\t')
            if len(fields) >= 9:
                rows.append(dict(zip(header, fields)))
    return rows


def calc_metrics(skill_name: str, rows: list[dict]) -> SkillMetrics:
    """计算完整指标集"""
    # 过滤该 skill 的记录
    skill_rows = [r for r in rows if r.get('skill', '').strip() == skill_name]
    if not skill_rows:
        raise ValueError(f"未找到 skill: {skill_name}")

    # 解析轮次
    rounds = []
    for i, r in enumerate(skill_rows):
        old = float(r.get('old_score', 0) or 0)
        new = float(r.get('new_score', 0) or 0)
        ret = (new / old - 1) if old > 0 else 0.0
        rounds.append(RoundMetrics(
            round_num=i + 1,
            old_score=old,
            new_score=new,
            skill_return=ret,
            is_improvement=ret > 0,
            dimension=r.get('dimension', 'N/A'),
            eval_mode=r.get('eval_mode', 'N/A')
        ))

    n = len(rounds)
    keeps = sum(1 for r in rounds if r.is_improvement)
    reverts = n - keeps

    # 提取分数序列
    scores = [r.new_score for r in rounds]
    returns = [r.skill_return for r in rounds]

    # 总收益率
    baseline = rounds[0].old_score
    final = rounds[-1].new_score
    total_return = (final / baseline - 1) if baseline > 0 else 0.0
    momentum = (final / baseline - 1) if baseline > 0 else 0.0

    # 波动率
    volatility = statistics.stdev(returns) if len(returns) > 1 else 0.0

    # 优化效率（类比夏普比率）
    mean_ret = statistics.mean(returns) if returns else 0.0
    optimization_efficiency = (mean_ret / volatility) if volatility > 1e-9 else 0.0

    # 防御效率（类比索提诺比率）
    neg_returns = [r for r in returns if r < 0]
    downside = statistics.stdev(neg_returns) if len(neg_returns) > 1 else 0.0
    downside_protection = (mean_ret / downside) if downside > 1e-9 else 0.0

    # 回退幅度
    peak = rounds[0].old_score
    regression_depth = 0.0
    regression_duration = 0
    current_dd_dur = 0

    for r in rounds:
        if r.new_score >= peak:
            peak = r.new_score
            current_dd_dur = 0
        else:
            dd = (peak - r.new_score) / peak
            current_dd_dur += 1
            if dd > regression_depth:
                regression_depth = dd
                regression_duration = current_dd_dur

    # 边际收益（类比卡玛比率）
    marginal_gain = (total_return / regression_depth) if regression_depth > 1e-9 else 0.0

    # 留存率
    retention_rate = keeps / n if n > 0 else 0.0

    # 平均每轮提升
    improvements = [r.skill_return for r in rounds if r.is_improvement]
    avg_improvement = statistics.mean(improvements) if improvements else 0.0

    # 一致性（正向占比）
    consistency = retention_rate

    # 平均轮次得分
    avg_round_score = statistics.mean(scores)

    # 最佳/最差轮次
    best_i = max(range(n), key=lambda i: rounds[i].skill_return)
    worst_i = min(range(n), key=lambda i: rounds[i].skill_return)

    # 综合评分
    overall_score = _calc_overall_score(
        total_return, optimization_efficiency, retention_rate, consistency, regression_depth
    )

    # 评级
    grade = _score_to_grade(overall_score)

    return SkillMetrics(
        skill_name=skill_name,
        total_rounds=n,
        keeps=keeps,
        reverts=reverts,
        total_return=total_return,
        momentum=momentum,
        volatility=volatility,
        optimization_efficiency=optimization_efficiency,
        downside_protection=downside_protection,
        marginal_gain=marginal_gain,
        regression_depth=regression_depth,
        regression_duration=regression_duration,
        retention_rate=retention_rate,
        avg_improvement=avg_improvement,
        consistency=consistency,
        avg_round_score=avg_round_score,
        best_round=rounds[best_i].round_num,
        worst_round=rounds[worst_i].round_num,
        overall_score=overall_score,
        grade=grade,
    )


def _calc_overall_score(
    total_return: float,
    optimization_efficiency: float,
    retention_rate: float,
    consistency: float,
    regression_depth: float,
) -> float:
    """综合评分"""
    RET_W = 0.30      # 总收益率
    EFF_W = 0.25      # 优化效率
    RETAIN_W = 0.20   # 留存率
    CONS_W = 0.15     # 一致性
    DD_W = 0.10       # 回退控制

    ret_score = min(total_return * 100, 100) if total_return > 0 else 0.0
    eff_score = min(optimization_efficiency * 50, 100)
    retain_score = retention_rate * 100
    cons_score = consistency * 100
    dd_score = max(0, (0.20 - regression_depth) / 0.20 * 100) if regression_depth < 0.20 else 0.0
    return (
        ret_score * RET_W +
        eff_score * EFF_W +
        retain_score * RETAIN_W +
        cons_score * CONS_W +
        dd_score * DD_W
    )


def _score_to_grade(score: float) -> str:
    if score >= 90: return 'A'
    if score >= 75: return 'B'
    if score >= 60: return 'C'
    if score >= 40: return 'D'
    return 'F'


def format_metrics(m: SkillMetrics) -> str:
    """格式化输出"""
    return f"""
╔══════════════════════════════════════════════════════╗
║  darwin-skill 量化评分报告: {m.skill_name:<28}║
╠══════════════════════════════════════════════════════╣
║  综合评分: {m.overall_score:.1f}/100  评级: {m.grade:<6}           ║
╠══════════════════════════════════════════════════════╣
║  轮次统计                                              ║
║    总轮次: {m.total_rounds:<3}  保留: {m.keeps:<3}  回滚: {m.reverts:<3}  留存率: {m.retention_rate:.0%}          ║
╠══════════════════════════════════════════════════════╣
║  收益类指标 (类比 vectorbt.portfolio returns)     ║
║    总收益率:  {m.total_return:+.2%}  动量: {m.momentum:+.2%}              ║
║    波动率:    {m.volatility:.4f}  平均提升: {m.avg_improvement:+.4f}      ║
╠══════════════════════════════════════════════════════╣
║  风险调整指标 (类比 quantstats)                     ║
║    优化效率:  {m.optimization_efficiency:+.2f}  防御效率: {m.downside_protection:+.2f}        ║
║    边际收益:  {m.marginal_gain:+.2f}  回退幅度: {m.regression_depth:.2%}         ║
╠══════════════════════════════════════════════════════╣
║  质量指标                                              ║
║    一致性:    {m.consistency:.0%}  平均分: {m.avg_round_score:.1f}               ║
║    最佳轮:    #{m.best_round}  最差轮: #{m.worst_round}                      ║
╚══════════════════════════════════════════════════════╝
"""


def export_json(m: SkillMetrics, path: str):
    """导出 JSON 供后续使用"""
    data = {
        "skill": m.skill_name,
        "overall_score": round(m.overall_score, 1),
        "grade": m.grade,
        "total_rounds": m.total_rounds,
        "keeps": m.keeps,
        "reverts": m.reverts,
        "win_rate": round(m.retention_rate, 4),
        "total_return": round(m.total_return, 6),
        "momentum": round(m.momentum, 6),
        "volatility": round(m.volatility, 6),
        "retention_rate": round(m.retention_rate, 4),
        "optimization_efficiency": round(m.optimization_efficiency, 4),
        "downside_protection": round(m.downside_protection, 4),
        "marginal_gain": round(m.marginal_gain, 4),
        "regression_depth": round(m.regression_depth, 6),
        "regression_duration": m.regression_duration,
        "consistency": round(m.consistency, 4),
        "avg_improvement": round(m.avg_improvement, 6),
        "avg_round_score": round(m.avg_round_score, 2),
        "best_round": m.best_round,
        "worst_round": m.worst_round,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='darwin-skill 量化打分工具')
    parser.add_argument('--results', '-r',
                        default='~/.claude/skills/darwin-skill/results.tsv',
                        help='results.tsv 路径')
    parser.add_argument('--skill', '-s',
                        default='darwin-skill',
                        help='目标 skill 名')
    parser.add_argument('--json', '-j',
                        help='导出 JSON 路径')
    parser.add_argument('--threshold', '-t',
                        type=float, default=0.5,
                        help='胜率阈值 (默认 0.5)')
    args = parser.parse_args()

    tsv_path = Path(args.results).expanduser()
    if not tsv_path.exists():
        print(f"[ERROR] 文件不存在: {tsv_path}")
        sys.exit(1)

    rows = parse_tsv(str(tsv_path))
    m = calc_metrics(args.skill, rows)

    print(format_metrics(m))

    # 优化建议
    suggestions = []
    if m.retention_rate < args.threshold:
        suggestions.append(f"[WARN] retention_rate {m.retention_rate:.0%} < threshold {args.threshold:.0%}")
    if m.regression_depth > 0.15:
        suggestions.append(f"[WARN] regression_depth {m.regression_depth:.1%} is large, consider more conservative changes")
    if m.optimization_efficiency < 0.5:
        suggestions.append(f"[WARN] optimization_efficiency {m.optimization_efficiency:.2f} is low")
    if m.volatility > 0.10:
        suggestions.append(f"[WARN] volatility {m.volatility:.2%} is high, scores are unstable across rounds")

    if suggestions:
        print("\n[OPTIMIZE] Suggestions:")
        for s in suggestions:
            print(f"  {s}")
    else:
        print("\n[PASS] All metrics within healthy range")

    if args.json:
        export_json(m, args.json)
        print(f"\n✓ JSON 导出: {args.json}")

    # 退出码：综合评分
    sys.exit(0 if m.overall_score >= 60 else 1)


if __name__ == '__main__':
    main()
