"""LLM fallback wiring, with a stand-in client: request shape, parsing, refusal handling.
The live model is measured separately (evaluate --llm), not in unit tests."""

import json
from types import SimpleNamespace as NS

from invoice_pipeline.llm import SCHEMA, LLMFallback, to_invoice
from invoice_pipeline.models import ExtractionResult, Invoice

PAYLOAD = {
    "vendor_name": "Example GmbH", "vendor_tax_id": "DE 136695976", "invoice_number": "RE-1",
    "issue_date": "2026-09-01", "due_date": "2026-10-01", "currency": "eur", "subtotal": "150.00",
    "tax_rate": "19", "tax_amount": "28.50", "total": "178.50",
    "line_items": [{"description": "a", "quantity": "2", "unit_price": "50.00", "amount": "100.00"},
                   {"description": "b", "quantity": "0.5", "unit_price": "100.00", "amount": "50.00"}],
}


class FakeMessages:
    def __init__(self, stop_reason="end_turn", payload=PAYLOAD):
        self.stop_reason, self.payload, self.calls = stop_reason, payload, []

    def create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason=self.stop_reason, model=kw["model"],
                  usage=NS(input_tokens=1500, output_tokens=400),
                  content=[NS(type="text", text=json.dumps(self.payload))])


def client(messages):
    return NS(beta=NS(messages=messages))


def test_request_carries_pdf_schema_and_parses(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    msgs = FakeMessages()
    fb = LLMFallback(model="test-model", client=client(msgs))
    result = fb(str(pdf), ExtractionResult(invoice=Invoice(), method="ocr", confidence=0.5))
    call = msgs.calls[0]
    assert call["output_config"]["format"]["schema"] is SCHEMA
    assert call["messages"][0]["content"][0]["source"]["media_type"] == "application/pdf"
    assert result.method == "ocr+llm" and result.confidence == 1.0
    inv = result.invoice
    assert inv.vendor_tax_id == "DE136695976" and inv.currency == "EUR" and str(inv.total) == "178.50"
    assert len(inv.line_items) == 2 and fb.usage["calls"] == 1


def test_refusal_keeps_rules_result(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    fb = LLMFallback(model="m", client=client(FakeMessages(stop_reason="refusal")))
    assert fb(str(pdf), ExtractionResult(invoice=Invoice(), method="ocr", confidence=0.5)) is None
    assert fb.usage["refusals"] == 1


def test_unparseable_values_become_null():
    inv = to_invoice({**PAYLOAD, "total": "n/a", "issue_date": "01/09/2026"})
    assert inv.total is None and inv.issue_date is None


def test_schema_objects_are_closed():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(SCHEMA)
