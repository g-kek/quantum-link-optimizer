"""Small CPU neural forecaster with separate regression and event heads."""

import copy

import numpy as np
import torch
from torch import nn

from quantum_link_optimizer.dataset import Windows


class RTTForecaster(nn.Module):
    def __init__(self, window: int):
        super().__init__()
        self.network = nn.Sequential(
            nn.Flatten(),
            nn.Linear(window * 2, 48),
            nn.ReLU(),
            nn.Linear(48, 24),
            nn.ReLU(),
            nn.Linear(24, 4),
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        output = self.network(x)
        return x[:, -1] + output[:, :2], output[:, 2:]


def train_model(
    data: Windows,
    seed: int = 42,
    epochs: int = 60,
    sla_ms: float = 100.0,
) -> tuple[RTTForecaster, list[dict]]:
    if epochs < 1:
        raise ValueError("Epochs must be positive")
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    model = RTTForecaster(data.x.shape[1])
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.001)
    x = torch.from_numpy(data.x)
    y = torch.tensor((data.y - data.mean) / data.scale, dtype=torch.float32)
    events = torch.tensor(data.y > sla_ms, dtype=torch.float32)
    train = torch.from_numpy(np.flatnonzero(data.mask("train")))
    val = torch.from_numpy(np.flatnonzero(data.mask("val")))
    bce = nn.BCEWithLogitsLoss()

    def loss_on(indices: torch.Tensor) -> torch.Tensor:
        forecast, logits = model(x[indices])
        return nn.functional.mse_loss(forecast, y[indices]) + 0.2 * bce(
            logits,
            events[indices],
        )

    history = []
    best_loss = float("inf")
    best_state = copy.deepcopy(model.state_dict())
    stale = 0
    for epoch in range(epochs):
        model.train()
        order = train[torch.randperm(len(train))]
        losses = []
        for batch in order.split(128):
            optimizer.zero_grad()
            loss = loss_on(batch)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach()))
        model.eval()
        with torch.no_grad():
            val_loss = float(loss_on(val))
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": float(np.mean(losses)),
                "val_loss": val_loss,
            }
        )
        if val_loss < best_loss:
            best_loss = val_loss
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
        if stale >= 10:
            break
    model.load_state_dict(best_state)
    model.eval()
    return model, history


def predict(model: RTTForecaster, data: Windows) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    with torch.no_grad():
        forecast, logits = model(torch.from_numpy(data.x))
    return (
        forecast.numpy() * data.scale + data.mean,
        torch.sigmoid(logits).numpy(),
    )
