"""Temporal Fusion Transformer 模型定义"""

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class GatedResidualNetwork(nn.Module):
        """GRN - TFT 的核心组件"""

        def __init__(self, d_model: int, d_hidden: int | None = None, dropout: float = 0.1):
            super().__init__()
            d_hidden = d_hidden or d_model
            self.fc1 = nn.Linear(d_model, d_hidden)
            self.fc2 = nn.Linear(d_hidden, d_model)
            self.gate = nn.Linear(d_model, d_model)
            self.norm = nn.LayerNorm(d_model)
            self.dropout = nn.Dropout(dropout)

        def forward(self, x):
            h = F.elu(self.fc1(x))
            h = self.dropout(self.fc2(h))
            gate = torch.sigmoid(self.gate(h))
            return self.norm(x + gate * h)

    class TemporalFusionTransformer(nn.Module):
        """简化版 TFT 模型"""

        def __init__(
            self,
            n_features: int = 6,
            d_model: int = 128,
            n_heads: int = 8,
            n_layers: int = 4,
            seq_len: int = 60,
            pred_len: int = 5,
            dropout: float = 0.1,
        ):
            super().__init__()
            self.seq_len = seq_len
            self.pred_len = pred_len

            # 特征嵌入
            self.input_proj = nn.Linear(n_features, d_model)

            # GRN 用于特征选择
            self.feature_grn = GatedResidualNetwork(d_model, dropout=dropout)

            # Transformer 编码器
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=n_heads,
                dim_feedforward=d_model * 4,
                dropout=dropout,
                batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

            # 输出头
            self.output_proj = nn.Sequential(
                nn.Linear(d_model, d_model // 2),
                nn.ReLU(),
                nn.Linear(d_model // 2, pred_len * 3),  # 3: direction_prob (up, down, neutral)
            )

        def forward(self, x):
            """
            Args:
                x: (batch, seq_len, n_features)
            Returns:
                (batch, pred_len, 3) - 方向概率
            """
            h = self.input_proj(x)
            h = self.feature_grn(h)
            h = self.encoder(h)

            # 使用最后一个时间步的输出
            out = self.output_proj(h[:, -1, :])
            out = out.view(-1, self.pred_len, 3)
            return F.softmax(out, dim=-1)

except ImportError:
    # PyTorch 未安装时提供占位
    class TemporalFusionTransformer:
        def __init__(self, *args, **kwargs):
            raise ImportError("PyTorch required: pip install torch")

    class GatedResidualNetwork:
        def __init__(self, *args, **kwargs):
            raise ImportError("PyTorch required: pip install torch")
