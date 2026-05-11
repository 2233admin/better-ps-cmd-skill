"""
量化数据可视化面板
支持K线图、技术指标（MACD、RSI等）、多阶导数分析

运行方式：
streamlit run stock_visualizer.py
"""
import streamlit as st
import duckdb
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from datetime import datetime, timedelta


DB_PATH = "C:/Users/Administrator/quant-terminal/data/quant.duckdb"


@st.cache_resource
def get_connection():
    return duckdb.connect(DB_PATH, read_only=True)


def calculate_macd(df, fast=12, slow=26, signal=9):
    exp1 = df['close'].ewm(span=fast, adjust=False).mean()
    exp2 = df['close'].ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram


def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_bollinger_bands(df, period=20, std_dev=2):
    sma = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return sma, upper, lower


def load_stock_data(symbol, start_date, end_date):
    conn = get_connection()
    query = f"""
        SELECT date, open, high, low, close, volume, amount
        FROM tdx_daily
        WHERE symbol = '{symbol}'
          AND date >= '{start_date}'
          AND date <= '{end_date}'
        ORDER BY date
    """
    df = conn.execute(query).df()
    return df


def get_stock_list():
    conn = get_connection()
    query = "SELECT DISTINCT symbol FROM tdx_daily ORDER BY symbol LIMIT 1000"
    stocks = conn.execute(query).fetchall()
    return [s[0] for s in stocks]


def main():
    st.set_page_config(page_title="量化数据可视化", layout="wide")
    
    st.title("📈 量化数据可视化面板")
    
    # 侧边栏
    st.sidebar.header("参数设置")
    
    # 股票选择
    stocks = get_stock_list()
    symbol = st.sidebar.selectbox("选择股票", stocks, index=0)
    
    # 日期范围
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=180)
    
    date_range = st.sidebar.date_input(
        "日期范围",
        value=(start_date, end_date),
        max_value=end_date
    )
    
    if len(date_range) == 2:
        start_date, end_date = date_range
    
    # 技术指标选择
    st.sidebar.subheader("技术指标")
    show_macd = st.sidebar.checkbox("MACD", value=True)
    show_rsi = st.sidebar.checkbox("RSI", value=True)
    show_bb = st.sidebar.checkbox("布林带", value=False)
    
    st.sidebar.subheader("高级分析")
    show_derivative1 = st.sidebar.checkbox("一阶导数（速度）", value=False)
    show_derivative2 = st.sidebar.checkbox("二阶导数（加速度）", value=False)
    
    # 加载数据
    with st.spinner("加载数据中..."):
        df = load_stock_data(symbol, start_date, end_date)
    
    if df.empty:
        st.error("没有找到数据")
        return
    
    # 显示基本信息
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("最新收盘价", f"{df['close'].iloc[-1]:.2f}")
    with col2:
        change = df['close'].iloc[-1] - df['close'].iloc[-2]
        change_pct = (change / df['close'].iloc[-2]) * 100
        st.metric("涨跌", f"{change:.2f}", f"{change_pct:.2f}%")
    with col3:
        st.metric("最高价", f"{df['high'].max():.2f}")
    with col4:
        st.metric("最低价", f"{df['low'].min():.2f}")
    
    # 计算指标
    indicators = []
    if show_macd:
        indicators.append('MACD')
        df['macd'], df['signal'], df['histogram'] = calculate_macd(df)
    if show_rsi:
        indicators.append('RSI')
        df['rsi'] = calculate_rsi(df)
    if show_bb:
        indicators.append('BB')
        df['bb_sma'], df['bb_upper'], df['bb_lower'] = calculate_bollinger_bands(df)
    if show_derivative1:
        indicators.append('D1')
        df['d1'] = df['close'].diff()
    if show_derivative2:
        indicators.append('D2')
        df['d2'] = df['close'].diff().diff()
    
    # 创建图表
    subplot_count = 1
    titles = [f'{symbol} K线图']
    
    if show_macd:
        subplot_count += 1
        titles.append('MACD')
    if show_rsi:
        subplot_count += 1
        titles.append('RSI')
    if show_derivative1 or show_derivative2:
        subplot_count += 1
        titles.append('价格导数')
    
    row_heights = [0.5] + [0.15] * (subplot_count - 1)
    fig = make_subplots(
        rows=subplot_count, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=titles,
        row_heights=row_heights
    )
    
    # K线图
    fig.add_trace(
        go.Candlestick(
            x=df['date'], open=df['open'], high=df['high'],
            low=df['low'], close=df['close'], name='K线'
        ),
        row=1, col=1
    )
    
    # 布林带
    if show_bb:
        fig.add_trace(go.Scatter(x=df['date'], y=df['bb_upper'], name='上轨',
                                line=dict(color='red', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['bb_sma'], name='中轨',
                                line=dict(color='orange', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['bb_lower'], name='下轨',
                                line=dict(color='green', width=1)), row=1, col=1)
    
    current_row = 2
    
    # MACD
    if show_macd:
        fig.add_trace(go.Scatter(x=df['date'], y=df['macd'], name='MACD',
                                line=dict(color='blue')), row=current_row, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['signal'], name='Signal',
                                line=dict(color='orange')), row=current_row, col=1)
        colors = ['red' if x > 0 else 'green' for x in df['histogram']]
        fig.add_trace(go.Bar(x=df['date'], y=df['histogram'], name='Histogram',
                            marker_color=colors), row=current_row, col=1)
        current_row += 1
    
    # RSI
    if show_rsi:
        fig.add_trace(go.Scatter(x=df['date'], y=df['rsi'], name='RSI',
                                line=dict(color='purple', width=2)), row=current_row, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="red", row=current_row, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", row=current_row, col=1)
        current_row += 1
    
    # 导数
    if show_derivative1 or show_derivative2:
        if show_derivative1:
            fig.add_trace(go.Scatter(x=df['date'], y=df['d1'], name='一阶导数',
                                    line=dict(color='blue')), row=current_row, col=1)
        if show_derivative2:
            fig.add_trace(go.Scatter(x=df['date'], y=df['d2'], name='二阶导数',
                                    line=dict(color='red')), row=current_row, col=1)
        current_row += 1
    
    fig.update_layout(height=800, showlegend=True, xaxis_rangeslider_visible=False)
    fig.update_xaxes(type='date')
    
    st.plotly_chart(fig, use_container_width=True)
    
    # 数据表
    with st.expander("查看原始数据"):
        st.dataframe(df.tail(50))


if __name__ == "__main__":
    main()
