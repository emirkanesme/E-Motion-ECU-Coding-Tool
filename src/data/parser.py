import csv
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import torch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - ECU_PARSER - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ECUDataParser")


class ECUMapParser:
    """Parse WinOLS-like dumps and convert them into U-Net tensors."""

    def __init__(self, norm_method: str = "min-max", epsilon: float = 1e-8) -> None:
        if norm_method not in {"min-max", "z-score"}:
            raise ValueError("Invalid normalization method. Use 'min-max' or 'z-score'.")
        self.norm_method = norm_method
        self.epsilon = epsilon
        self.map_metadata: Dict[str, Dict[str, float]] = {}

    def parse_winols_dump(self, file_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Parse a map dump where first row is X axis and first column is Y axis.

        Expected numeric layout:
            [empty, x1, x2, ...]
            [y1,   z11,z12,...]
            [y2,   z21,z22,...]
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        logger.info("Parsing WinOLS dump: %s", file_path)
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

        raw_data: List[List[float]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "name" in stripped.lower():
                continue

            delimiter = self._detect_delimiter(stripped)
            tokens = next(csv.reader([stripped], delimiter=delimiter))

            numeric_row: List[float] = []
            for token in tokens:
                normalized = token.strip().replace(",", ".")
                if self._is_numeric(normalized):
                    numeric_row.append(float(normalized))
            if numeric_row:
                raw_data.append(numeric_row)

        if len(raw_data) < 2:
            raise ValueError("Not enough numeric rows to construct an ECU map.")

        matrix = np.array(raw_data, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[0] < 2 or matrix.shape[1] < 2:
            raise ValueError("Parsed matrix is too small for axis/value separation.")

        x_axis = matrix[0, 1:]
        y_axis = matrix[1:, 0]
        z_matrix = matrix[1:, 1:]

        if z_matrix.size == 0:
            raise ValueError("Parsed map contains an empty Z matrix.")

        logger.info("Parse complete. Z matrix shape: %s", z_matrix.shape)
        return x_axis, y_axis, z_matrix

    @staticmethod
    def _detect_delimiter(line: str) -> str:
        if ";" in line:
            return ";"
        return ","

    @staticmethod
    def _is_numeric(value: str) -> bool:
        try:
            float(value)
            return True
        except ValueError:
            return False

    def normalize(self, data: np.ndarray, feature_name: str) -> np.ndarray:
        """Normalize data and store metadata for inverse transform."""
        if self.norm_method == "min-max":
            d_min = float(np.min(data))
            d_max = float(np.max(data))
            self.map_metadata[feature_name] = {"min": d_min, "max": d_max}
            return (data - d_min) / (d_max - d_min + self.epsilon)

        d_mean = float(np.mean(data))
        d_std = float(np.std(data))
        self.map_metadata[feature_name] = {"mean": d_mean, "std": d_std}
        return (data - d_mean) / (d_std + self.epsilon)

    def process_to_tensor(self, file_path: str) -> torch.Tensor:
        """Parse + normalize map and return tensor with shape (3, H, W)."""
        x_axis, y_axis, z_matrix = self.parse_winols_dump(file_path)

        z_norm = self.normalize(z_matrix, "Z_Target")
        x_mesh, y_mesh = np.meshgrid(x_axis, y_axis)
        x_norm = self.normalize(x_mesh, "X_RPM_Axis")
        y_norm = self.normalize(y_mesh, "Y_Load_Axis")

        stacked = np.stack([z_norm, x_norm, y_norm], axis=0)
        tensor_output = torch.from_numpy(stacked.astype(np.float32))
        logger.info("Tensor conversion complete. Shape: %s", tuple(tensor_output.shape))
        return tensor_output

    def inverse_transform(self, tensor_data: torch.Tensor, feature_name: str) -> np.ndarray:
        """Inverse normalized data back to physical values for a given feature."""
        data = tensor_data.detach().cpu().numpy()
        meta = self.map_metadata.get(feature_name)
        if meta is None:
            raise KeyError(f"Missing normalization metadata for feature: {feature_name}")

        if self.norm_method == "min-max":
            return data * (meta["max"] - meta["min"]) + meta["min"]
        return data * meta["std"] + meta["mean"]
