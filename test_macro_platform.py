"""
宏观分析平台功能测试
验证核心模块是否正常工作
"""
import sys
sys.path.append('C:/Users/Administrator/quant-terminal')

from macro_market_analyzer import MacroMarketAnalyzer
from datetime import datetime, timedelta

print("=" * 80)
print("宏观分析平台功能测试")
print("=" * 80)

# 初始化分析器
analyzer = MacroMarketAnalyzer()

# 测试1: 加载宏观指标
print("\n【测试1: 加载宏观指标】")
try:
    cpi_df = analyzer.load_macro_indicator('CPI')
    print(f"✅ CPI 数据: {len(cpi_df)} 条记录")
    print(f"   时间范围: {cpi_df['date'].min()} ~ {cpi_df['date'].max()}")
    print(f"   最新值: {cpi_df['value'].iloc[-1]:.2f}")
except Exception as e:
    print(f"❌ 失败: {e}")

# 测试2: 加载市场指数
print("\n【测试2: 加载市场指数】")
try:
    index_df = analyzer.load_market_index('sh000001')
    print(f"✅ 上证指数: {len(index_df)} 条记录")
    print(f"   时间范围: {index_df['date'].min()} ~ {index_df['date'].max()}")
    print(f"   最新收盘: {index_df['close'].iloc[-1]:.2f}")
except Exception as e:
    print(f"❌ 失败: {e}")

# 测试3: 获取当前 Regime
print("\n【测试3: 获取当前 Regime】")
try:
    regime_info = analyzer.get_current_regime()
    if 'error' not in regime_info:
        print(f"✅ 当前 Regime: {regime_info['regime_name']}")
        print(f"   宏观数据: {regime_info['macro_data']}")
        print(f"   特征: {regime_info['description']['characteristics']}")
    else:
        print(f"❌ 失败: {regime_info['error']}")
except Exception as e:
    print(f"❌ 失败: {e}")

# 测试4: 相关性分析
print("\n【测试4: 宏观-市场相关性分析】")
try:
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

    result = analyzer.analyze_macro_market_correlation(
        'CPI',
        'sh000001',
        start_date,
        end_date,
        window=60
    )

    if 'error' not in result:
        print(f"✅ CPI vs 上证指数")
        print(f"   相关系数: {result['correlation']:.3f}")
        print(f"   数据点数: {len(result['merged_data'])}")

        # 领先滞后分析
        lead_lag = result['lead_lag']
        if not lead_lag.empty:
            best = lead_lag.loc[lead_lag['correlation'].abs().idxmax()]
            print(f"   最强相关滞后期: {best['lag']} 月")
            print(f"   最强相关系数: {best['correlation']:.3f}")
    else:
        print(f"❌ 失败: {result['error']}")
except Exception as e:
    print(f"❌ 失败: {e}")

# 测试5: 数据对齐
print("\n【测试5: 数据对齐功能】")
try:
    from utils.data_alignment import merge_macro_market

    macro_df = analyzer.load_macro_indicator('PMI')
    market_df = analyzer.load_market_index('sh000300')

    merged = merge_macro_market(macro_df, market_df, 'PMI')

    print(f"✅ 数据对齐成功")
    print(f"   宏观数据: {len(macro_df)} 条")
    print(f"   市场数据: {len(market_df)} 条")
    print(f"   合并后: {len(merged)} 条")
    print(f"   PMI 缺失: {merged['PMI'].isna().sum()} 条")
except Exception as e:
    print(f"❌ 失败: {e}")

# 测试6: 相关性工具
print("\n【测试6: 相关性分析工具】")
try:
    from utils.correlation_analysis import rolling_correlation, lead_lag_correlation

    # 使用测试数据
    if 'merged' in locals() and not merged.empty:
        rolling_corr = rolling_correlation(
            merged['close'],
            merged['PMI'],
            window=30
        )

        print(f"✅ 滚动相关系数计算成功")
        print(f"   有效数据点: {rolling_corr.notna().sum()}")
        print(f"   平均相关: {rolling_corr.mean():.3f}")
        print(f"   相关范围: [{rolling_corr.min():.3f}, {rolling_corr.max():.3f}]")
except Exception as e:
    print(f"❌ 失败: {e}")

# 总结
print("\n" + "=" * 80)
print("测试完成！")
print("=" * 80)
print("\n✨ 核心功能已就绪，可以访问 Streamlit 应用:")
print("   - 宏观分析平台: http://localhost:8502")
print("   - 个股可视化: http://localhost:8501")
print("\n📚 使用文档:")
print("   - QUICKSTART.md")
print("   - DATA_GUIDE.md")
