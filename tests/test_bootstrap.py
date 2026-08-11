from __future__ import annotations

from fastapi.testclient import TestClient


def test_bibtex_bootstrap_filters_topics(client: TestClient) -> None:
    bib = b"""
@article{touch2025,
  title={Learning Tactile Representations for Robot Manipulation},
  author={Researcher, Ada and Scientist, Grace},
  date={2025},
  eprint={2501.00001},
  url={https://arxiv.org/abs/2501.00001},
  note={A useful tactile paper}
}
@article{archive2022,
  title={Archives and Public Administration},
  author={Historian, Alex},
  date={2022}
}
"""
    response = client.post(
        "/api/bootstrap/bibtex",
        files={"file": ("library.bib", bib, "application/x-bibtex")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["entries"] == 2
    assert result["relevant"] == 1
    assert result["skipped"] == 1
    assert "机器人触觉" in result["node_ids"]
