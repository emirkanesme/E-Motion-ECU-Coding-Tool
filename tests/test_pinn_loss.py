import torch

from src.losses.pinn_loss import PhysicsInformedECULoss


def test_pinn_loss_is_scalar_and_non_negative() -> None:
    criterion = PhysicsInformedECULoss()
    y_pred = torch.rand(2, 1, 16, 16)
    y_true = torch.rand(2, 1, 16, 16)

    loss = criterion(y_pred, y_true, map_type="ignition")
    assert loss.ndim == 0
    assert float(loss.item()) >= 0.0


def test_boundary_penalty_increases_when_limits_violated() -> None:
    criterion = PhysicsInformedECULoss(lambda_mse=0.0, lambda_smooth=0.0, lambda_mono=0.0, lambda_bound=1.0)
    y_true = torch.zeros(1, 1, 4, 4)
    safe_pred = torch.full((1, 1, 4, 4), 0.5)
    violating_pred = torch.full((1, 1, 4, 4), 0.99)

    safe_loss = criterion(safe_pred, y_true, map_type="ignition")
    violating_loss = criterion(violating_pred, y_true, map_type="ignition")

    assert violating_loss > safe_loss


def test_supports_multichannel_predictions() -> None:
    criterion = PhysicsInformedECULoss()
    y_pred = torch.rand(2, 3, 16, 16)
    y_true = torch.rand(2, 3, 16, 16)

    loss = criterion(y_pred, y_true, map_type="afr")
    assert loss.ndim == 0
