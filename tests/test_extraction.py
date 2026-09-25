"""Extraction on text-layer PDFs must be exact; planted defects must all be caught."""

from invoice_pipeline.evaluate import run


def test_text_layer_fields_exact_and_defects_caught(text_inbox):
    s = run(text_inbox, None)["summary"]
    for field, c in s["fields"].items():
        assert c["text_ok"] == c["text_n"], (field, c)
    for defect, v in s["defects"].items():
        assert v["caught"] == v["planted"], defect
    assert s["clean"]["flagged"] == 0
