from pathlib import Path

import numpy as np
import torch

from src.data.parser import ECUMapParser


def test_process_to_tensor_shape_and_dtype(tmp_path: Path) -> None:
    csv_content = "\n".join(
        [
            "0,1000,2000,3000",
            "20,10.0,15.0,20.0",
            "40,9.0,14.0,19.0",
            "60,8.0,13.0,18.0",
        ]
    )
    file_path = tmp_path / "map.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    parser = ECUMapParser(norm_method="min-max")
    tensor = parser.process_to_tensor(str(file_path))

    assert isinstance(tensor, torch.Tensor)
    assert tensor.shape == (3, 3, 3)
    assert tensor.dtype == torch.float32
    assert torch.all(tensor >= 0.0)
    assert torch.all(tensor <= 1.0)


def test_inverse_transform_recovers_original_z(tmp_path: Path) -> None:
    csv_content = "\n".join(
        [
            "0,1000,2000",
            "20,10.0,15.0",
            "40,9.0,14.0",
        ]
    )
    file_path = tmp_path / "map.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    parser = ECUMapParser(norm_method="min-max")
    _, _, z_matrix = parser.parse_winols_dump(str(file_path))
    z_norm = parser.normalize(z_matrix, "Z_Target")
    recovered = parser.inverse_transform(torch.from_numpy(z_norm.astype(np.float32)), "Z_Target")

    np.testing.assert_allclose(recovered, z_matrix, rtol=1e-5, atol=1e-5)
