from typing import Literal

import torch
import torch.nn as nn
import torch.nn.functional as F

MapType = Literal["ignition", "afr"]


class PhysicsInformedECULoss(nn.Module):
    """Physics-informed loss for ECU map optimization."""

    def __init__(
        self,
        lambda_mse: float = 1.0,
        lambda_smooth: float = 0.8,
        lambda_bound: float = 50.0,
        lambda_mono: float = 20.0,
        max_safe_advance_norm: float = 0.90,
        max_safe_afr_norm: float = 0.85,
    ) -> None:
        super().__init__()
        self.w_mse = lambda_mse
        self.w_smooth = lambda_smooth
        self.w_bound = lambda_bound
        self.w_mono = lambda_mono
        self.max_adv = max_safe_advance_norm
        self.max_afr = max_safe_afr_norm

        laplacian_kernel = torch.tensor(
            [[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]],
            dtype=torch.float32,
        )
        self.register_buffer("laplacian_filter", laplacian_kernel.view(1, 1, 3, 3))

    def _smoothness_loss(self, y_pred: torch.Tensor) -> torch.Tensor:
        # Apply Laplacian channel-wise to support single/multi-channel outputs.
        channels = y_pred.size(1)
        kernel = self.laplacian_filter.expand(channels, 1, 3, 3)
        laplacian_out = F.conv2d(y_pred, kernel, padding=1, groups=channels)
        return torch.mean(torch.abs(laplacian_out))

    def forward(
        self,
        y_pred: torch.Tensor,
        y_true: torch.Tensor,
        map_type: MapType,
    ) -> torch.Tensor:
        if map_type not in {"ignition", "afr"}:
            raise ValueError("map_type must be either 'ignition' or 'afr'.")

        loss_mse = F.mse_loss(y_pred, y_true)
        loss_smooth = self._smoothness_loss(y_pred)

        loss_bound = torch.tensor(0.0, device=y_pred.device)
        loss_mono = torch.tensor(0.0, device=y_pred.device)

        y_axis_diff = torch.diff(y_pred, dim=2)

        if map_type == "ignition":
            # Ignition advance should not exceed safe normalized threshold.
            boundary_violation = F.relu(y_pred - self.max_adv)
            # With increased load, advance should typically not increase.
            monotonic_violation = F.relu(y_axis_diff)
        else:
            # For normalized AFR maps where larger values mean leaner mixture,
            # high-load cells should not exceed safe lean limit.
            boundary_violation = F.relu(y_pred - self.max_afr)
            # With increased load, AFR should usually stay flat or become richer.
            monotonic_violation = F.relu(y_axis_diff)

        loss_bound = torch.mean(boundary_violation ** 2)
        loss_mono = torch.mean(monotonic_violation ** 2)

        total_loss = (
            self.w_mse * loss_mse
            + self.w_smooth * loss_smooth
            + self.w_bound * loss_bound
            + self.w_mono * loss_mono
        )
        return total_loss
