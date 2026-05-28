"""重建模型模块 (DIRE)"""
import torch
import torch.nn as nn
from torchvision.models import resnet18, ResNet18_Weights


class SimpleDDIMReconstructor(nn.Module):
    """简易DDIM重建模块（模拟SD去噪过程）"""

    def __init__(self):
        super().__init__()
        self.encoder = resnet18(weights=ResNet18_Weights.DEFAULT)
        self.encoder.fc = nn.Linear(512, 256)
        self.decoder = nn.Sequential(
            nn.Linear(256, 512),
            nn.ReLU(),
            nn.Linear(512, 3 * 256 * 256)
        )

    def forward(self, x):
        feat = self.encoder(x)
        recon = self.decoder(feat).view(-1, 3, 256, 256)
        return recon
