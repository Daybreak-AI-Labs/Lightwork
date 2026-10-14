from types import SimpleNamespace


def _studio_form():
    domains = (
        "organization",
        "industry",
        "jurisdiction",
        "data",
        "system",
        "vendor",
        "ai",
        "operational",
    )
    fields = []
    for index, _domain in enumerate(domains):
        field_type = "record_ref" if index == 0 else "evidence_ref" if index == 1 else "boolean"
        fields.append(
            {
                "field_id": f"field-{index}",
                "label_ref": f"label-{index}",
                "field_type": field_type,
                "required": True,
                "options": [],
                "reference_kind": "supplier" if index == 0 else "document" if index == 1 else None,
                "default": None,
            }
        )
    return SimpleNamespace(
        blueprint={
            "forms": [
                {
                    "form_id": "intake",
                    "object_id": "supplier",
                    "title_ref": "Supplier intake",
                    "sections": [
                        {"section_id": domain, "field_ids": [f"field-{index}"]}
                        for index, domain in enumerate(domains)
                    ],
                }
            ],
            "objects": [{"object_id": "supplier", "fields": fields}],
            "rules": [],
        }
    )


def test_studio_record_and_evidence_fields_import_as_named_item_questions():
    from maverick_dashboard.compliance_questionnaire_builder import _form

    result = _form(_studio_form(), "intake")

    assert result["incompatibilities"] == []
    assert result["questions"][0]["kind"] == "opaque_ref"
    assert result["questions"][0]["name"].endswith("(record: Supplier)")
    assert result["questions"][1]["kind"] == "opaque_ref"
    assert result["questions"][1]["name"].endswith("(evidence: Document)")


def test_named_item_conditions_compile_to_native_stable_references():
    from maverick import compliance_scoping
    from maverick_dashboard.compliance_questionnaire_builder import (
        Condition,
        Design,
        Question,
        Rule,
        _compile,
        _condition_to_design,
    )

    domains = (
        "organization",
        "industry",
        "jurisdiction",
        "data",
        "system",
        "vendor",
        "ai",
        "operational",
    )
    questions_in = [
        Question(
            name="Supplier record",
            domain=domains[0],
            kind="opaque_ref",
            options=[],
            required=True,
        )
    ]
    questions_in.extend(
        Question(
            name=f"Question {index + 1}",
            domain=domain,
            kind="boolean",
            options=[],
            required=True,
            condition=(
                Condition(question=0, operator="equals", value="Northwind Supply")
                if index == 1
                else None
            ),
        )
        for index, domain in enumerate(domains[1:], start=1)
    )
    design = Design(
        name="Supplier review",
        questions=questions_in,
        rules=[
            Rule(
                framework="Internal policy",
                controls=["SUP-1"],
                reason="Supplier record matches",
                conditions=[
                    Condition(
                        question=0,
                        operator="in",
                        value=["Northwind Supply", "Contoso Services"],
                    )
                ],
            )
        ],
    )

    questions, rules, names = _compile(design, "questionnaire-supplier-review")
    assert questions[1]["condition"]["value"] == "ref:northwind-supply"
    assert rules[0]["conditions"][0]["value"] == [
        "ref:northwind-supply",
        "ref:contoso-services",
    ]
    assert _condition_to_design(
        {"question_id": "question-0", "operator": "equals", "value": "ref:northwind-supply"},
        {"question-0": 0},
        [{"answer_type": "opaque_ref", "options": []}],
    )["value"] == "northwind-supply"

    validated = compliance_scoping._creation_material(
        "questionnaire-supplier-review",
        questions,
        rules,
        0,
        "user:reviewer",
        names,
    )
    assert validated["questions"][0]["answer_type"] == "opaque_ref"
