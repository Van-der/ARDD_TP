import torch
import torch.nn as nn
from torchvision import models
from torchvision.models.resnet import ResNeXt50_32X4D_Weights

class DeepFakeDetector(nn.Module):
    def __init__(self, sequence_length=20, hidden_size=2048, lstm_layers=1, num_classes=2):
        super(DeepFakeDetector, self).__init__()
        self.sequence_length = sequence_length
        self.hidden_size = hidden_size
        
        # ResNext50 backbone (pretrained)
        resnext = models.resnext50_32x4d(weights=ResNeXt50_32X4D_Weights.DEFAULT)
        # Remove the final fully connected layer to output feature vectors
        self.backbone = nn.Sequential(*list(resnext.children())[:-1])
        
        self.lstm = nn.LSTM(
            input_size=hidden_size,
            hidden_size=hidden_size,
            num_layers=lstm_layers,
            batch_first=True,
            bias=False  # Naman712 checkpoint was trained without LSTM biases
        )
        
        self.linear1 = nn.Linear(hidden_size, num_classes)
        
    def forward(self, x):
        # x shape: (batch_size, sequence_length, channels, height, width)
        batch_size, seq_len, c, h, w = x.size()
        
        # Flatten batch and sequence length to pass through backbone
        x_reshaped = x.view(batch_size * seq_len, c, h, w)
        features = self.backbone(x_reshaped) # shape: (batch_size * seq_len, 2048, 1, 1)
        
        # Reshape to (batch_size, sequence_length, hidden_size)
        features = features.view(batch_size, seq_len, -1)
        
        # Pass sequence through LSTM
        lstm_out, (hidden, cell) = self.lstm(features)
        
        # We take the output of the last time step
        last_out = lstm_out[:, -1, :] # shape: (batch_size, hidden_size)
        
        logits = self.linear1(last_out) # shape: (batch_size, num_classes)
        
        return lstm_out, logits
