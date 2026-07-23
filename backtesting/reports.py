"""Export backtest reports to developer-only result folders."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .utils import timestamp_slug
from .versioning import RunMetadata


class ReportExporter:
    """Write summary, predictions, weekly, and metrics reports for a run."""

    def __init__(self, results_dir: Path):
        self.results_dir = Path(results_dir)

    def export(self, metadata: RunMetadata, predictions: list[dict[str, Any]], metrics: dict[str, Any]) -> Path:
        """Export standard report artifacts and return the output directory."""
        output_dir = self.results_dir / timestamp_slug()
        output_dir.mkdir(parents=True, exist_ok=False)
        (output_dir / "summary.json").write_text(json.dumps({"run": metadata.to_dict(), "metrics": metrics}, indent=2, default=str))
        (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
        self._write_csv(output_dir / "predictions.csv", predictions)
        weekly = [{"week": week, "accuracy": acc} for week, acc in metrics.get("weekly_accuracy", {}).items()]
        self._write_csv(output_dir / "weekly.csv", weekly)
        return output_dir

    def _write_csv(self, path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("")
            return
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
