"""
Regenerate golden verification reports for the corpus.

Run after intentionally changing editor/harness behavior, then review the
golden diffs like code — they are the regression contract:

    python corpus/make_goldens.py
"""
import json
import os
import tempfile

from pdf_editor import edit_pdf

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDENS = os.path.join(HERE, "goldens")


def main():
    os.makedirs(GOLDENS, exist_ok=True)
    with open(os.path.join(HERE, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    tmp = tempfile.mkdtemp()
    made = []
    for fx in manifest["fixtures"]:
        if fx["expect"]["mode"] != "ok":
            continue
        src = os.path.join(HERE, fx["file"])
        out = os.path.join(tmp, fx["name"] + ".pdf")
        result = edit_pdf(src, out, fx["replacements"], verify=True)
        report = result["verification"]
        path = os.path.join(GOLDENS, fx["name"] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        made.append(fx["name"])
    print("goldens written:", ", ".join(made))


if __name__ == "__main__":
    main()
