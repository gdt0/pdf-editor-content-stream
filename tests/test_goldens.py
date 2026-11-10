"""CI gate: replay every corpus fixture and compare against golden reports.

Goldens are committed in corpus/goldens/ (regenerate with
corpus/make_goldens.py). Any change in edit or verification behavior that
alters a report shows up here.
"""
import json
import os

import pytest

from pdf_editor import edit_pdf

GOLDENS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corpus", "goldens"
)


def _fixtures(manifest):
    return [
        (fx["name"], fx) for fx in manifest["fixtures"]
    ]


def test_every_ok_fixture_has_a_golden(manifest):
    ok_names = {fx["name"] for fx in manifest["fixtures"]
                if fx["expect"]["mode"] == "ok"}
    on_disk = {f[:-5] for f in os.listdir(GOLDENS) if f.endswith(".json")}
    assert ok_names <= on_disk, "missing goldens: %s" % (ok_names - on_disk)


@pytest.mark.parametrize("name,fx", _fixtures(json.load(
    open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "corpus", "manifest.json"), encoding="utf-8"))))
def test_golden_replay(name, fx, corpus_dir, tmp_path):
    src = os.path.join(corpus_dir, fx["file"])
    out = str(tmp_path / (name + ".pdf"))
    expect = fx["expect"]

    if expect["mode"] == "ok":
        result = edit_pdf(src, out, fx["replacements"], verify=True)
        with open(os.path.join(GOLDENS, name + ".json"), encoding="utf-8") as fh:
            golden = json.load(fh)
        assert result["verification"] == golden, name
    else:
        with pytest.raises(ValueError) as exc:
            edit_pdf(src, out, fx["replacements"], verify=False)
        assert expect["message_contains"] in str(exc.value), name
