"""
关联分析页面
分析宏观指标与市场指数的相关性、领先滞后关系
"""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime, timedelta
import sys
sys.path.append('C:/Users/Administrator/quant-terminal')

from macro_market_analyzer import MacroMarketAnalyzer

st.set_page_config(page_title="关联分析", page_icon="🔗", layout="wide")

st.title("🔗 宏观-市场关联分析")

# 初始化
@st.cache_resource
def get_analyzer():
    return MacroMarketAnalyzer()

analyzer = get_analyzer()

# 侧边栏设置
st.sidebar.header("分析参数")

# 选择宏观指标
macro_indicators = ['CPI', 'PPI', 'PMI', 'M2', 'GDP']
selected_macro = st.sidebar.selectbox("宏观指标", macro_indicators, index=0)

# 选择市场指数
market_indices = {
    'sh000001': '上证指数',
    'sh000300': '沪深300',
    'sz399001': '深证成指',
    'sz399006': '创业板指'
}
selected_market = st.sidebar.selectbox(
    "市场指数",
    list(market_indices.keys()),
    format_func=lambda x: market_indices[x]
)

# 日期范围
end_date = datetime.now()
start_date = end_date - timedelta(days=730)  # 默认2年

date_range = st.sidebar.date_input(
    "日期范围",
    value=(start_date, end_date),
    max_value=end_date
)

if len(date_range) == 2:
    start_date, end_date = date_range
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')
else:
    start_date_str = None
    end_date_str = None

# 滚动窗口
rolling_window = st.sidebar.slider("滚动窗口（天）", 30, 250, 60)

# 执行分析
if st.sidebar.button("开始分析", type="primary"):
    with st.spinner("分析中..."):
        result = analyzer.analyze_macro_market_correlation(
            selected_macro,
            selected_market,
            start_date_str,
            end_date_str,
            rolling_window
        )

        if 'error' in result:
            st.error(f"分析失败: {result['error']}")
        else:
            # 显示整体相关性
            st.subheader("📊 整体相关性")

            col1, col2, col3 = st.columns(3)

            with col1:
                corr_val = result['correlation']
                st.metric(
                    "相关系数",
                    f"{corr_val:.3f}",
                    help="皮尔逊相关系数，范围 [-1, 1]"
                )

            with col2:
                corr_strength = "强" if abs(corr_val) > 0.7 else "中" if abs(corr_val) > 0.4 else "弱"
                st.metric("相关强度", corr_strength)

            with col3:
                corr_direction = "正相关" if corr_val > 0 else "负相关"
                st.metric("相关方向", corr_direction)

            # 时间序列图（双轴）
            st.markdown("---")
            st.subheader("📈 时间序列对比")

            merged_data = result['merged_data']

            fig = make_subplots(specs=[[{"secondary_y": True}]])

            # 市场指数（左轴）
            fig.add_trace(
                go.Scatter(
                    x=merged_data['date'],
                    y=merged_data['close'],
                    name=market_indices[selected_market],
                    line=dict(color='blue', width=2)
                ),
                secondary_y=False
            )

            # 宏观指标（右轴）
            fig.add_trace(
                go.Scatter(
                    x=merged_data['date'],
                    y=merged_data[selected_macro],
                    name=selected_macro,
                    line=dict(color='red', width=2)
                ),
                secondary_y=True
            )

            fig.update_xaxes(title_text="日期")
            fig.update_yaxes(title_text=market_indices[selected_market], secondary_y=False)
            fig.update_yaxes(title_text=selected_macro, secondary_y=True)

            fig.update_layout(
                height=500,
                hovermode='x unified',
                title_text=f"{selected_macro} vs {market_indices[selected_market]}"
            )

            st.plotly_chart(fig, use_container_width=True)

            # 滚动相关性
            st.markdown("---")
            st.subheader("📉 滚动相关系数")

            rolling_corr = result['rolling_correlation']

            fig2 = go.Figure()

            fig2.add_trace(go.Scatter(
                x=merged_data['date'],
                y=rolling_corr,
                name=f'{rolling_window}日滚动相关',
                line=dict(color='purple', width=2),
                fill='tozeroy',
                fillcolor='rgba(128, 0, 128, 0.1)'
            ))

            fig2.add_hline(y=0, line_dash="dash", line_color="gray")
            fig2.add_hline(y=0.5, line_dash="dot", line_color="green", annotation_text="强正相关")
            fig2.add_hline(y=-0.5, line_dash="dot", line_color="red", annotation_text="强负相关")

            fig2.update_layout(
                height=400,
                title_text=f"{rolling_window}日滚动相关系数",
                xaxis_title="日期",
                yaxis_title="相关系数",
                yaxis_range=[-1, 1]
            )

            st.plotly_chart(fig2, use_container_width=True)

            # 领先滞后分析
            st.markdown("---")
            st.subheader("⏱️ 领先滞后分析")

            lead_lag = result['lead_lag']

            if not lead_lag.empty:
                fig3 = go.Figure()

                # 相关系数柱状图
                colors = ['green' if x > 0 else 'red' for x in lead_lag['correlation']]

                fig3.add_trace(go.Bar(
                    x=lead_lag['lag'],
                    y=lead_lag['correlation'],
                    marker_color=colors,
                    name='相关系数',
                    text=lead_lag['correlation'].round(3),
                    textposition='outside'
                ))

                # 标记显著性
                significant = lead_lag[lead_lag['significant']]
                if not significant.empty:
                    fig3.add_trace(go.Scatter(
                        x=significant['lag'],
                        y=significant['correlation'],
                        mode='markers',
                        marker=dict(size=12, symbol='star', color='gold'),
                        name='显著 (p<0.05)'
                    ))

                fig3.update_layout(
                    height=400,
                    title_text="领先滞后相关性分析",
                    xaxis_title="滞后期（月，负值表示宏观指标领先）",
                    yaxis_title="相关系数",
                    yaxis_range=[-1, 1]
                )

                st.plotly_chart(fig3, use_container_width=True)

                # 解读
                max_corr_row = lead_lag.loc[lead_lag['correlation'].abs().idxmax()]

                st.info(f"""
                **分析结果**:
                - 最强相关出现在滞后 {max_corr_row['lag']} 个月
                - 相关系数: {max_corr_row['correlation']:.3f}
                - 统计显著性: {'显著' if max_corr_row['significant'] else '不显著'} (p={max_corr_row['p_value']:.4f})

                **解读**: {'宏观指标领先市场' if max_corr_row['lag'] < 0 else '市场领先宏观指标' if max_corr_row['lag'] > 0 else '同步关系'}
                """)

            # 数据表
            with st.expander("查看原始数据"):
                st.dataframe(
                    merged_data[['date', 'close', selected_macro]].tail(50),
                    use_container_width=True
                )

else:
    st.info("👈 请在左侧设置参数，然后点击「开始分析」")

# 页脚
st.markdown("---")
st.caption("💡 提示: 相关性不等于因果关系，仅供参考")
