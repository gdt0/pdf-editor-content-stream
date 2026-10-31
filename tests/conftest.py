import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")


@pytest.fixture
def corpus_dir():
    return CORPUS


@pytest.fixture
def manifest():
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def corpus_file():
    def _get(name):
        return os.path.join(CORPUS, name)

    return _get
