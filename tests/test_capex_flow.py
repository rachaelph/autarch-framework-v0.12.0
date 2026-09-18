from __future__ import annotations

import sys
from pathlib import Path

import pytest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from capex_flow import load_reviews, run_pipeline, write_outputs  # noqa: E402


DATA = EXAMPLES / "data" / "capex"


def test_supplied_abbyy_packages_reach_expected_decisions() -> None:
    package = run_pipeline(DATA, 2000.0)
    invoices = {row["invoice_number"]: row for row in package["invoices"]}

    shelving = invoices["INV12-62027"]
    assert shelving["recommendation"] == "OPEX"
    assert shelving["eligible_total"] == 911.08
    assert {line["recommended_task"] for line in shelving["lines"] if line["capital_nature"]} == {"5225"}

    environmental = invoices["36764068-GRP005"]
    assert environmental["recommendation"] == "CAPEX"
    assert environmental["eligible_total"] == 28305.0
    assert {line["recommended_task"] for line in environmental["lines"] if line["capital_nature"]} == {"0007"}
    assert any(item["code"] == "E4" for item in environmental["exceptions"])
    assert all(invoice["automatic_posting"] is False for invoice in invoices.values())


def test_review_is_applied_but_does_not_create_rule_before_threshold(tmp_path: Path) -> None:
    invoice_id = "58aceec5-f35a-48a7-8faa-2f1a195af203"
    (tmp_path / "reviews.jsonl").write_text(
        '{"invoice_id":"' + invoice_id + '","decision":"RECLASSIFY","reviewer":"finance","reclassified_as":"OPEX"}\n',
        encoding="utf-8",
    )
    package = run_pipeline(DATA, 2000.0, load_reviews(tmp_path), learning_confirmations=50)
    reviewed = next(row for row in package["invoices"] if row["invoice_id"] == invoice_id)

    assert reviewed["review_status"] == "RECLASSIFY"
    assert reviewed["final_decision"] == "OPEX"
    assert package["candidate_rules"] == []


def test_power_bi_and_dashboard_outputs_are_created(tmp_path: Path) -> None:
    outputs = write_outputs(run_pipeline(DATA, 2000.0), tmp_path)

    expected = {
        "decision_package.json",
        "invoices.csv",
        "invoice_lines.csv",
        "review_queue.csv",
        "candidate_rules.csv",
        "audit_trace.csv",
        "dashboard.html",
    }
    assert expected <= {path.name for path in outputs.iterdir()}
    assert "Advisory output only" in (outputs / "dashboard.html").read_text(encoding="utf-8")


def test_autarch_gates_each_maf_stage_and_records_evidence(tmp_path: Path) -> None:
    from capex_agents import GovernedMAFStages, STAGES, StageSpec

    calls = []

    class TestProvider:
        def __init__(self, stage):
            self.stage = stage

        def complete(self, prompt, system=None):
            calls.append(self.stage)
            return '{"result":"review"}'

        def close(self):
            pass

    specs = {stage: StageSpec("Return JSON", lambda result, payload: result) for stage in STAGES}
    with GovernedMAFStages(
        tmp_path, "test-transport", lambda: None, specs, provider_factory=TestProvider
    ) as workflow:
        denied = workflow.children[STAGES[0]].enact("capex.asset_classification", {"payload": {}})
        assert not denied.executed
        assert calls == []
        for stage in STAGES:
            assert workflow.run(stage, {"invoice_id": "test"}) == {"result": "review"}
        assert calls == list(STAGES)
        assert len({entry["why_id"] for entry in workflow.trace}) == 7
        evidence = workflow.evidence()
        assert evidence["chain_verified"]
        assert Path(evidence["audit_path"]).is_file()


def test_maf_contracts_reject_invented_codes_and_unsupported_decisions() -> None:
    pytest.importorskip("pydantic")
    from capex_agents import build_stage_specs

    specs = build_stage_specs()
    payload = {
        "invoice": {"region": "North America", "lines": [{"line_id": "1"}]},
        "task_catalog": [], "asset_classes": [], "policies": [],
        "evidence": {"line:1": "Oven door"},
    }
    explanation = {"line_id": "1", "rationale": "Needs a catalog", "evidence_refs": ["line:1"], "confidence": 0.4}
    with pytest.raises(ValueError, match="unavailable task"):
        specs["asset_classification"].validate({"lines": [dict(
            explanation, task_code="MADE-UP", asset_class_code=None, status="MATCHED"
        )]}, payload)
    with pytest.raises(ValueError, match="unsupported"):
        specs["capitalization_decision"].validate({"lines": [dict(
            explanation, recommendation="CAPEX", policy_ids=[]
        )]}, payload)
    reviewed = specs["capitalization_decision"].validate({"lines": [dict(
        explanation, recommendation="REVIEW", policy_ids=[]
    )]}, payload)
    assert reviewed["lines"][0]["recommendation"] == "REVIEW"


@pytest.mark.parametrize("region,field,code,citation", [
    ("North America", "task_code", "0007", "task:0007"),
    ("Europe", "asset_class_code", "CLASS-A", "class:CLASS-A"),
])
@pytest.mark.parametrize("status", ["MATCHED", "AMBIGUOUS"])
def test_classification_citation_contract_matches_prompt_and_schema(region, field, code, citation, status) -> None:
    pytest.importorskip("pydantic")
    from capex_agents import build_stage_specs

    spec = build_stage_specs()["asset_classification"]
    schema = spec.run_kwargs["options"]["response_format"].model_json_schema()
    description = schema["$defs"]["ClassificationLine"]["properties"]["evidence_refs"]["description"]
    assert description in spec.instructions
    assert "task:<task_code>" in description
    assert "class:<asset_class_code>" in description
    assert "AMBIGUOUS as well as MATCHED" in description

    entry = {"code": code, "description": "Synthetic catalog entry"}
    payload = {
        "invoice": {"region": region, "lines": [{"line_id": "1"}]},
        "task_catalog": [entry] if field == "task_code" else [],
        "asset_classes": [entry] if field == "asset_class_code" else [],
        "evidence": {"line:1": "Synthetic invoice line", citation: entry},
    }
    line = {
        "line_id": "1", "rationale": "Synthetic classification", "confidence": 0.8,
        "task_code": None, "asset_class_code": None, "status": status,
        "evidence_refs": ["line:1"],
    }
    line[field] = code
    with pytest.raises(ValueError, match="Selected catalog codes require source citations") as error:
        spec.validate({"lines": [line]}, payload)
    assert "line 1" in str(error.value)
    assert citation in str(error.value)
    assert line["evidence_refs"] == ["line:1"]

    cited = dict(line, evidence_refs=["line:1", citation])
    assert spec.validate({"lines": [cited]}, payload) == {"lines": [cited]}
    unresolved = dict(line, task_code=None, asset_class_code=None, status="UNAVAILABLE")
    assert spec.validate({"lines": [unresolved]}, payload) == {"lines": [unresolved]}


@pytest.mark.parametrize("invalid_ref", [
    "approved_business_coding", "why-test-classification", "stage:asset_classification:1",
])
def test_coding_validation_citations_use_exact_evidence_keys(invalid_ref) -> None:
    pytest.importorskip("pydantic")
    from capex_agents import build_stage_specs

    spec = build_stage_specs()["coding_validation"]
    schema = spec.run_kwargs["options"]["response_format"].model_json_schema()
    description = schema["$defs"]["CodingLine"]["properties"]["evidence_refs"]["description"]
    assert description in spec.instructions
    assert "exact top-level keys" in description
    assert "approved_coding, not approved_business_coding" in spec.instructions
    payload = {
        "invoice": {"lines": [{"line_id": "1"}]},
        "approved_business_coding": {}, "expected_coding_status": {"1": "MISSING"},
        "evidence": {
            "line:1": "Synthetic invoice line", "approved_coding": {},
            "stage:asset_classification": "why-test-classification",
        },
    }
    line = {
        "line_id": "1", "status": "MISSING", "confidence": 0.8,
        "rationale": "Approved coding was not supplied",
        "evidence_refs": ["line:1", "approved_coding", "stage:asset_classification"],
    }
    assert spec.validate({"lines": [line]}, payload) == {"lines": [line]}
    invalid = dict(line, evidence_refs=["line:1", invalid_ref])
    with pytest.raises(ValueError, match="not present in its authorized context") as error:
        spec.validate({"lines": [invalid]}, payload)
    assert invalid_ref in str(error.value)


def test_real_maf_sdk_runs_all_specialists_under_autarch(tmp_path: Path, monkeypatch) -> None:
    framework = pytest.importorskip("agent_framework")
    import json
    import capex_flow
    from capex_agents import STAGES, run_maf_pipeline

    invoice = {
        "invoice_id": "TEST-INVOICE", "invoice_number": "TEST-001", "vendor": "Test vendor",
        "country": "US", "region": "North America", "state": "MN", "currency": "USD",
        "total": 150.0, "invoice_date": "2026-09-01", "source": "test fixture",
        "source_sha256": "a" * 64, "afe_number": "", "project_number": "",
        "lines": [{"line_id": "1", "description": "Oven door", "amount": 150.0, "quantity": 1, "unit_price": 150.0}],
    }
    monkeypatch.setattr(capex_flow, "load_inputs", lambda *args, **kwargs: ([invoice], [], {}, ["Test catalog unavailable"]))
    observed = []

    class TestChatClient(framework.BaseChatClient):
        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            payload = json.loads(next(message.text for message in reversed(messages) if message.role == "user"))
            schema = options["response_format"].__name__
            observed.append(schema)
            explanation = {"rationale": "Supplied evidence is insufficient", "evidence_refs": ["line:1"], "confidence": 0.5}
            if schema == "Understanding":
                result = {"lines": [dict(explanation, line_id="1", asset_type="Oven", commodity="Food equipment", nature="COMPONENT")]}
            elif schema == "Classification":
                result = {"lines": [dict(explanation, line_id="1", task_code=None, asset_class_code=None, status="UNAVAILABLE")]}
            elif schema == "Coding":
                result = {"lines": [dict(explanation, line_id="1", status="MISSING")]}
            elif schema == "Decisions":
                result = {"lines": [dict(explanation, line_id="1", recommendation="REVIEW", policy_ids=[])]}
            elif schema == "Relationships":
                result = dict(explanation, bundle_exception=False, matched_history_ids=[])
            elif schema == "Exceptions":
                result = {"exceptions": payload["required_exceptions"], "summary": "Finance review required"}
            else:
                result = {"proposals": []}
            return framework.ChatResponse(messages=[framework.Message("assistant", [json.dumps(result)])])

    package = run_maf_pipeline(DATA, tmp_path, TestChatClient, "test-transport-not-Azure", progress=None)
    assert observed == ["Understanding", "Classification", "Coding", "Decisions", "Relationships", "Exceptions", "Learning"]
    assert [entry["agent"] for entry in package["audit_trace"]] == ["intake", *STAGES]
    assert package["engine"] == "maf"
    assert package["invoices"][0]["recommendation"] == "REVIEW"
    assert package["invoices"][0]["review_status"] == "PENDING"
    assert package["invoices"][0]["automatic_posting"] is False
    assert package["task_catalog_count"] == 0
    assert package["governance"]["chain_verified"] is True
    output_dir = write_outputs(package, tmp_path)
    dashboard = (output_dir / "dashboard.html").read_text(encoding="utf-8")
    assert "Microsoft Agent Framework + Autarch" in dashboard
    assert "Source gaps" in dashboard
    assert "0</strong><span>OPEX" in dashboard
    assert (output_dir / "signed_audit.jsonl").is_file()
    capex_flow.record_review(tmp_path, "TEST-INVOICE", "reclassify", "Test reviewer", "Fixture decision", "opex")
    reviewed = json.loads((output_dir / "decision_package.json").read_text(encoding="utf-8"))
    assert reviewed["invoices"][0]["final_decision"] == "OPEX"
    assert reviewed["invoices"][0]["recommendation"] == "REVIEW"
    assert len(observed) == 7
    assert package["governance"]["signatures_verified"] is True
    from capex_agents import apply_bound_reviews, case_content, digest, load_reviewed_cases
    reviews = load_reviews(tmp_path)
    snapshots = load_reviewed_cases(tmp_path, reviews)
    assert len(snapshots) == 1
    assert apply_bound_reviews(snapshots, reviews)[0]["final_decision"] == "OPEX"
    current = reviewed["invoices"][0]
    current["lines"][0]["amount"] = 200.0
    current["case_digest"] = digest(case_content(current))
    assert apply_bound_reviews([current], reviews) == []
    assert current["review_status"] == "STALE_REVIEW"
    assert current["final_decision"] is None


def test_governed_model_failure_is_not_retried_or_replaced(tmp_path: Path) -> None:
    from capex_agents import GovernedMAFStages, STAGES, StageSpec

    calls = []

    class FailedProvider:
        def complete(self, prompt, system=None):
            calls.append(prompt)
            raise TypeError("Test transport failure")

        def close(self):
            pass

    specs = {stage: StageSpec("Return JSON", lambda result, payload: result) for stage in STAGES}
    with GovernedMAFStages(tmp_path, "test", lambda: None, specs, provider_factory=lambda stage: FailedProvider()) as workflow:
        with pytest.raises(RuntimeError, match="Test transport failure"):
            workflow.run(STAGES[0], {})
        assert len(calls) == 1
        assert workflow.evidence()["signatures_verified"]


def test_governed_budget_blocks_the_next_model_call(tmp_path: Path) -> None:
    from capex_agents import GovernedMAFStages, STAGES, StageSpec

    calls = []

    class CountedProvider:
        def complete(self, prompt, system=None):
            calls.append(prompt)
            return '{}'

        def close(self):
            pass

    specs = {stage: StageSpec("Return JSON", lambda result, payload: result) for stage in STAGES}
    with GovernedMAFStages(tmp_path, "test", lambda: None, specs, max_calls=1,
                          provider_factory=lambda stage: CountedProvider()) as workflow:
        workflow.run(STAGES[0], {})
        with pytest.raises(RuntimeError):
            workflow.run(STAGES[1], {})
        assert len(calls) == 1


def test_relationship_candidates_require_prior_capitalized_strong_links() -> None:
    from capex_agents import _history_candidates

    invoice = {"invoice_id": "new", "invoice_date": "2026-09-01", "afe_number": "AFE-1", "vendor": "Same vendor"}
    prior = {"id": "asset", "invoice_date": "2026-01-01", "afe_number": "AFE-1", "capitalized": True}
    records = [prior, dict(prior, id="future", invoice_date="2026-10-01"),
               dict(prior, id="unposted", capitalized=False),
               dict(prior, id="vendor-only", afe_number="", vendor="Same vendor")]
    assert _history_candidates(invoice, records) == [prior]


def test_learning_cannot_count_multiple_lines_as_independent_reviews() -> None:
    pytest.importorskip("pydantic")
    from capex_agents import build_stage_specs

    schema = build_stage_specs()["continuous_learning"]
    review = {"review_id": "review-1", "invoice_id": "one-invoice", "final_decision": "CAPEX",
              "country": "US", "currency": "USD",
              "lines": [{"asset_type": "Oven", "recommended_task": "COOK"}] * 50}
    proposal = {"asset_type": "Oven", "task_code": "COOK", "country": "US", "currency": "USD",
                "decision": "CAPEX", "condition": "Same reviewed scope", "rationale": "Test",
                "supporting_review_ids": ["review-1"]}
    with pytest.raises(ValueError, match="independent invoices"):
        schema.validate({"proposals": [proposal]}, {"eligible_reviews": [review], "learning_confirmations": 50})


def test_cli_defaults_to_maf_and_never_falls_back(tmp_path: Path, monkeypatch, capsys) -> None:
    from capex_flow import build_parser, main

    assert build_parser().parse_args([]).engine == "maf"
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    sentinel = output_dir / "decision_package.json"
    sentinel.write_text("previous output", encoding="utf-8")
    assert main(["--workspace", str(tmp_path)]) == 2
    assert sentinel.read_text(encoding="utf-8") == "previous output"
    assert "No offline fallback" in capsys.readouterr().err


@pytest.mark.parametrize("stage,region", [
    ("document_intelligence", "North America"),
    ("asset_classification", "North America"),
    ("asset_classification", "Europe"),
    ("coding_validation", "North America"),
])
def test_maf_azure_transport_uses_structured_responses_and_bounded_requests(tmp_path: Path, monkeypatch, stage, region) -> None:
    pytest.importorskip("agent_framework.openai")
    import json
    import httpx
    import openai
    from capex_agents import GovernedMAFStages, azure_client_factory, build_stage_specs

    requests = []
    response = {"lines": [{"line_id": "1", "asset_type": "Oven", "commodity": "Food equipment",
                           "nature": "COMPONENT", "confidence": 0.8, "rationale": "A stated oven component",
                           "evidence_refs": ["line:1"]}]}
    payload = {
        "invoice": {"region": region, "lines": [{"line_id": "1", "description": "Oven door"}]},
        "evidence": {"line:1": "Oven door", "batch:first": "First request only"},
    }
    if stage == "asset_classification":
        code = "0007" if region == "North America" else "CLASS-A"
        citation = f"task:{code}" if region == "North America" else f"class:{code}"
        entry = {"code": code, "description": "Synthetic catalog entry"}
        payload.update(
            task_catalog=[entry] if region == "North America" else [],
            asset_classes=[entry] if region == "Europe" else [],
        )
        payload["evidence"][citation] = entry
        response = {"lines": [{
            "line_id": "1", "task_code": code if region == "North America" else None,
            "asset_class_code": code if region == "Europe" else None, "status": "MATCHED",
            "confidence": 0.8, "rationale": "Synthetic catalog classification",
            "evidence_refs": ["line:1", citation],
        }]}
    elif stage == "coding_validation":
        payload.update(approved_business_coding={}, expected_coding_status={"1": "MISSING"})
        payload["evidence"]["approved_coding"] = {}
        response = {"lines": [{
            "line_id": "1", "status": "MISSING", "confidence": 0.8,
            "rationale": "Approved coding was not supplied", "evidence_refs": ["line:1", "approved_coding"],
        }]}

    async def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.host == "test-resource.openai.azure.com"
        assert request.url.path.endswith("/deployments/test-deployment/chat/completions")
        schema = body["response_format"]["json_schema"]["schema"]
        line_model = {
            "document_intelligence": "UnderstandingLine", "asset_classification": "ClassificationLine",
            "coding_validation": "CodingLine",
        }[stage]
        citations = schema["$defs"][line_model]["properties"]["evidence_refs"]["items"]
        if "$ref" in citations:
            citations = schema["$defs"][citations["$ref"].rsplit("/", 1)[-1]]
        assert citations["enum"] == sorted(payload["evidence"])
        assert "task_catalog" not in citations["enum"]
        if stage == "asset_classification":
            assert "task:<task_code>" in json.dumps(body["messages"])
            assert "class:<asset_class_code>" in json.dumps(body["messages"])
            description = schema["$defs"]["ClassificationLine"]["properties"]["evidence_refs"]["description"]
            assert "task:<task_code>" in description
            assert "class:<asset_class_code>" in description
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 0,
            "model": "test-deployment", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(response)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        })

    original_client = openai.AsyncAzureOpenAI

    class TestAzureClient(original_client):
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["timeout"] == 30.0
            super().__init__(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr(openai, "AsyncAzureOpenAI", TestAzureClient)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-test-key-not-a-real-credential")
    factory, deployment = azure_client_factory("azure:test-deployment", "https://test-resource.openai.azure.com", "key", 30.0)
    with GovernedMAFStages(tmp_path, deployment, factory, build_stage_specs(1000)) as workflow:
        output = workflow.run(stage, payload)
        assert output == response
        del payload["evidence"]["batch:first"]
        payload["evidence"]["batch:second"] = "Second request only"
        assert workflow.run(stage, payload) == response
        assert workflow.evidence()["signatures_verified"]
    assert len(requests) == 2
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert requests[0]["store"] is False


def test_europe_policy_classification_and_bundling_path(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("pydantic")
    import json
    import capex_flow
    from capex_agents import run_maf_pipeline

    invoice = {
        "invoice_id": "TEST-EU", "invoice_number": "EU-1", "vendor": "Test vendor", "region": "Europe",
        "country": "NO", "currency": "NOK", "state": "", "total": 175.0, "invoice_date": "2026-09-01",
        "source": "Synthetic EU fixture", "source_sha256": "b" * 64, "afe_number": "AFE-1",
        "lines": [{"line_id": "1", "description": "Equipment component", "amount": 150.0},
                  {"line_id": "2", "description": "Service", "amount": 25.0}],
    }
    monkeypatch.setattr(capex_flow, "load_inputs", lambda *args, **kwargs: ([invoice], [], {}, []))
    references = {
        "policies": [{"id": "POL-NO", "country": "NO", "currency": "NOK", "text": "Synthetic test policy",
                      "source": "Test manual", "approved_by": "Test owner"},
                     {"id": "POL-US", "country": "US", "currency": "USD", "text": "Wrong scope test policy",
                      "source": "Test manual", "approved_by": "Test owner"}],
        "asset_classes": [{"code": "CLASS-A", "country": "NO", "description": "Test equipment", "source": "Test JDE"}],
        "history": [{"id": "prior-asset", "invoice_date": "2026-01-01", "afe_number": "AFE-1",
                     "description": "Test capitalized equipment", "source": "Test asset register", "capitalized": True}],
        "business_coding": {"TEST-EU": {"lines": {"1": {"asset_class_code": "CLASS-B"}, "2": {"asset_class_code": "CLASS-A"}}}},
    }
    reference_path = tmp_path / "references.json"
    reference_path.write_text(json.dumps(references), encoding="utf-8")

    class ScriptedProvider:
        def __init__(self, stage):
            self.stage = stage

        def complete(self, prompt, system=None):
            payload = json.loads(prompt)
            explanation = {"rationale": "Synthetic test reasoning", "confidence": 0.9, "evidence_refs": ["invoice"]}
            if self.stage == "asset_relationship":
                return json.dumps(dict(explanation, bundle_exception=True, matched_history_ids=["prior-asset"]))
            if self.stage == "exception_management":
                return json.dumps({"exceptions": payload["required_exceptions"], "summary": "Test review"})
            if self.stage == "continuous_learning":
                return '{"proposals": []}'
            lines = []
            for line in payload["invoice"]["lines"]:
                result = dict(explanation, line_id=line["line_id"])
                if self.stage == "document_intelligence":
                    result.update(asset_type="Equipment", commodity="Equipment", nature="COMPONENT")
                elif self.stage == "asset_classification":
                    assert payload["task_catalog"] == []
                    result.update(task_code=None, asset_class_code="CLASS-A", status="MATCHED", evidence_refs=["class:CLASS-A"])
                elif self.stage == "coding_validation":
                    result.update(status=payload["expected_coding_status"][line["line_id"]])
                else:
                    assert [policy["id"] for policy in payload["policies"]] == ["POL-NO"]
                    result.update(recommendation="CAPEX" if line["line_id"] == "1" else "OPEX",
                                  policy_ids=["POL-NO"], evidence_refs=["policy:POL-NO"])
                lines.append(result)
            return json.dumps({"lines": lines})

        def close(self):
            pass

    package = run_maf_pipeline(DATA, tmp_path, lambda: None, "scripted-test-only",
                               references_path=reference_path, provider_factory=ScriptedProvider, progress=None)
    result = package["invoices"][0]
    assert result["recommendation"] == "MIXED"
    assert result["amounts_by_recommendation"] == {"CAPEX": "150.00", "OPEX": "25.00", "REVIEW": "0.00"}
    assert result["lines"][0]["actual_asset_class"] == "CLASS-B"
    assert {item["code"] for item in result["exceptions"]} == {"E2", "E3"}
    assert result["related_invoice_ids"] == ["prior-asset"]
    assert result["review_status"] == "PENDING"