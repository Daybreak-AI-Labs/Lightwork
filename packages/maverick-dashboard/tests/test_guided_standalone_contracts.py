from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from maverick_dashboard.consent_experiments_api import router as experiments_router
from maverick_dashboard.consent_publishing_api import router as publishing_router
from maverick_dashboard.privacy_data_discovery_api import router as discovery_router


class GuidedInputs(HTMLParser):
    def __init__(self, source: str):
        super().__init__()
        self.inputs: list[dict[str, str]] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "textarea" and "data-guided-endpoint" in values:
            self.inputs.append(values)


def test_standalone_fields_resolve_the_existing_api_request_schemas():
    app = FastAPI()
    app.include_router(discovery_router, prefix="/api/v1")
    app.include_router(experiments_router, prefix="/api/v1")
    document = app.openapi()

    def resolve(schema):
        while "$ref" in schema:
            schema = document["components"]["schemas"][schema["$ref"].split("/")[-1]]
        return schema

    templates = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    bindings = []
    for name in ("privacy_data_discovery.html", "consent_experiments.html"):
        inputs = GuidedInputs((templates / name).read_text(encoding="utf-8")).inputs
        assert len(inputs) == 2
        for field in inputs:
            operation = document["paths"][field["data-guided-endpoint"]][
                field.get("data-guided-method", "post")
            ]
            request = resolve(operation["requestBody"]["content"]["application/json"]["schema"])
            child = resolve(request["properties"][field["data-guided-property"]])
            bindings.append(field["data-guided-property"])
            if field["data-guided-property"] == "limits":
                assert set(child["properties"]) == {
                    "max_records", "max_files", "max_bytes", "max_seconds",
                    "max_findings", "max_fields_per_record", "max_record_bytes",
                    "max_document_bytes",
                }
                for limit in child["properties"].values():
                    assert limit["type"] in {"integer", "number"}
                    assert "minimum" in limit and "maximum" in limit
            elif field["data-guided-property"] == "variants":
                assert child["minItems"] == 2
                assert child["maxItems"] == 10
                assert "equal_choice_evidence_sha256" in resolve(child["items"])["required"]
            else:
                assert any(option.get("type") == "null" for option in child["anyOf"])
    assert bindings == ["limits", "limits", "variants", "audience"]


def test_each_publishing_resource_kind_binds_its_own_closed_request_contract():
    class Kinds(HTMLParser):
        def handle_starttag(self, tag, attrs):
            fields = dict(attrs)
            if tag == "option" and "data-guided-endpoint" in fields:
                bindings[fields["value"]] = fields["data-guided-endpoint"]

    bindings = {}
    template = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "consent_publishing.html"
    Kinds().feed(template.read_text(encoding="utf-8"))
    expected = {
        "purposes": "PurposeIn", "topics": "TopicIn",
        "custom-preferences": "CustomPreferenceIn", "notices": "NoticeIn",
        "translations": "TranslationIn", "themes": "ThemeTokenIn",
        "templates": "TemplateIn", "rule-groups": "RuleGroupIn",
    }
    assert bindings.keys() == expected.keys()
    app = FastAPI()
    app.include_router(publishing_router, prefix="/api/v1")
    document = app.openapi()
    for kind, endpoint in bindings.items():
        body = document["paths"][endpoint]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert body["$ref"] == f"#/components/schemas/{expected[kind]}"
        schema = document["components"]["schemas"][expected[kind]]
        assert schema["additionalProperties"] is False
        assert {"version", "expected_current_version", "idempotency_key"} <= set(schema["required"])
    template_actions = document["components"]["schemas"]["TemplateIn"]["properties"]["actions"]
    assert set(template_actions["propertyNames"]["enum"]) == {
        "accept_all", "close", "manage_preferences", "reject_all", "save_preferences",
    }
