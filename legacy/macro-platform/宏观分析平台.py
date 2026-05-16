"""
宏观-市场关联分析平台
Streamlit 多页应用主入口
"""
import streamlit as st
import sys
sys.path.append('C:/Users/Administrator/quant-terminal')

st.set_page_config(
    page_title="宏观-市场关联分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("📊 宏观-市场关联分析平台")

st.markdown("""
### 欢迎使用宏观-市场关联分析平台

本平台提供以下功能：

1. **宏观仪表板** - 查看当前宏观环境和 Regime 状态
2. **关联分析** - 分析宏观指标与市场指数的相关性
3. **情景分析** - 基于历史相似情景预测市场表现
4. **个股敏感度** - 分析个股对宏观因子的暴露度

---

### 数据概况

- **宏观数据**: 67,866 条记录，489 个指标
- **市场数据**: 28,364,012 条记录，11,562 只股票
- **时间范围**: 1990-2026

---

### 使用指南

1. 从左侧导航栏选择功能页面
2. 设置分析参数（日期范围、指标等）
3. 查看可视化结果和统计分析
4. 导出报告（PDF/Excel）

---

**提示**: 首次加载可能需要几秒钟，请耐心等待。
""")

# 侧边栏
with st.sidebar:
    st.header("导航")
    st.markdown("""
    - 📈 宏观仪表板
    - 🔗 关联分析
    - 🎯 情景分析
    - 💼 个股敏感度
    """)

    st.markdown("---")

    st.header("快速链接")
    st.markdown("""
    - [数据概况](http://localhost:8501)
    - [个股可视化](http://localhost:8501)
    - [使用文档](./QUICKSTART.md)
    """)

    st.markdown("---")
    st.caption("© 2026 量化研究平台")
