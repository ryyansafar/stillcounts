import json
from pathlib import Path

from scripts.make_vectors import OUT, build


def test_golden_vectors_are_up_to_date_with_the_reference_merge():
    expected = json.loads(json.dumps(build(), ensure_ascii=False, sort_keys=True))
    assert json.loads(Path(OUT).read_text()) == expected, "run: python -m scripts.make_vectors"
