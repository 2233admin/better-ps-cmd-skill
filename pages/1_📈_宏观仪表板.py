"""
宏观仪表板页面
显示当前宏观环境、Regime 状态、关键指标
"""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import sys
sys.path.append('C:/Users/Administrator/quant-terminal')

from macro_market_analyzer import MacroMarketAnalyzer

st.set_page_config(page_title="宏观仪表板", page_icon="📈", layout="wide")

st.title("📈 宏观仪表板")

# 初始化分析器
@st.cache_resource
def get_analyzer():
    return MacroMarketAnalyzer()

analyzer = get_analyzer()

# 侧边栏设置
st.sidebar.header("设置")
lookback_days = st.sidebar.slider("回看天数", 30, 365, 180)

# 主要内容
col1, col2, col3, col4 = st.columns(4)

# 获取当前 Regime
with st.spinner("加载宏观数据..."):
    regime_info = analyzer.get_current_regime()

if 'error' in regime_info:
    st.error(f"无法加载数据: {regime_info['error']}")
else:
    # 显示当前 Regime
    regime_desc = regime_info['description']

    with col1:
        st.metric(
            "当前 Regime",
            regime_desc['name'],
            help=regime_desc['characteristics']
        )

    # 显示关键宏观指标
    macro_data = regime_info['macro_data']

    with col2:
        if 'CPI' in macro_data:
            st.metric("CPI (%)", f"{macro_data['CPI']:.2f}")

    with col3:
        if 'PMI' in macro_data:
            pmi_val = macro_data['PMI']
            st.metric(
                "PMI",
                f"{pmi_val:.1f}",
                delta="扩张" if pmi_val > 50 else "收缩"
            )

    with col4:
        if 'M2' in macro_data:
            st.metric("M2 同比 (%)", f"{macro_data['M2']:.1f}")

    # Regime 描述
    st.markdown("---")
    st.subheader("Regime 特征")

    col_a, col_b = st.columns(2)

    with col_a:
        st.info(f"**特征**: {regime_desc['characteristics']}")
        st.info(f"**市场含义**: {regime_desc['market_implication']}")

    with col_b:
        st.info(f"**政策立场**: {regime_desc['policy_stance']}")

    # 宏观指标时间序列
    st.markdown("---")
    st.subheader("关键宏观指标走势")

    # 加载历史数据
    indicators_to_plot = ['CPI', 'PPI', 'PMI', 'M2']

    with st.spinner("加载历史数据..."):
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=indicators_to_plot,
            vertical_spacing=0.12,
            horizontal_spacing=0.1
        )

        positions = [(1, 1), (1, 2), (2, 1), (2, 2)]

        for idx, indicator in enumerate(indicators_to_plot):
            try:
                df = analyzer.load_macro_indicator(indicator)
                if not df.empty:
                    # 只显示最近的数据
                    df = df.tail(lookback_days)

                    row, col = positions[idx]
                    fig.add_trace(
                        go.Scatter(
                            x=df['date'],
                            y=df['value'],
                            name=indicator,
                            mode='lines',
                            line=dict(width=2)
                        ),
                        row=row, col=col
                    )

                    # 添加参考线
                    if indicator == 'PMI':
                        fig.add_hline(
                            y=50, line_dash="dash", line_color="gray",
                            row=row, col=col
                        )
            except Exception as e:
                st.warning(f"无法加载 {indicator}: {str(e)}")

        fig.update_layout(
            height=600,
            showlegend=False,
            title_text="宏观指标历史走势"
        )

        st.plotly_chart(fig, use_container_width=True)

    # 市场指数表现
    st.markdown("---")
    st.subheader("主要市场指数表现")

    indices = {
        'sh000001': '上证指数',
        'sh000300': '沪深300',
        'sz399001': '深证成指',
        'sz399006': '创业板指'
    }

    index_cols = st.columns(len(indices))

    for idx, (symbol, name) in enumerate(indices.items()):
        try:
            df = analyzer.load_market_index(symbol)
            if not df.empty and len(df) >= 2:
                latest = df.iloc[-1]
                prev = df.iloc[-2]

                change = latest['close'] - prev['close']
                change_pct = (change / prev['close']) * 100

                with index_cols[idx]:
                    st.metric(
                        name,
                        f"{latest['close']:.2f}",
                        f"{change_pct:+.2f}%"
                    )
        except:
            pass

    # Regime 历史分布
    st.markdown("---")
    st.subheader("历史 Regime 分布")

    st.info("💡 **提示**: 此功能需要完整的历史 Regime 标注数据，将在后续版本中实现。")

# 页脚
st.markdown("---")
st.caption("数据更新时间: " + regime_info.get('date', 'N/A'))
