"""The primer notebook embeds the data-model diagram, so it must stay identical to docs/diagrams."""

import base64
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_primer_embeds_the_current_data_model_diagram():
    nb = json.loads((REPO / "notebooks" / "primer.ipynb").read_text())
    embedded = [
        cell["attachments"]["canonical-data-model.png"]["image/png"]
        for cell in nb["cells"]
        if "canonical-data-model.png" in cell.get("attachments", {})
    ]
    assert len(embedded) == 1, "the primer should embed the data-model diagram once"
    png = (REPO / "docs" / "diagrams" / "canonical-data-model.png").read_bytes()
    assert base64.b64decode(embedded[0]) == png, (
        "notebooks/primer.ipynb embeds a stale copy of docs/diagrams/canonical-data-model.png; "
        "re-embed it (see docs/diagrams/README.md)"
    )
