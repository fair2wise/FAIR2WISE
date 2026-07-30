import json

import fitz

from app.modules.f2w_agent.paper_evidence_agent import (
    PaperEvidenceAgent,
    summarize_extracted_terms,
)


def _pdf(path, texts):
    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def _manifest(path, filename, entry):
    path.write_text(json.dumps({"papers": {filename: entry}}), encoding="utf-8")


def test_targeted_query_reads_only_manifest_selected_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    manifest = tmp_path / "extraction_manifest.json"
    _pdf(pdf, ["SECRET PAGE ONE", "Relevant x-ray finding", "SECRET PAGE THREE"])
    _manifest(
        manifest,
        pdf.name,
        {"extraction_state": "partial", "selected_pages": [2]},
    )
    prompts = []
    agent = PaperEvidenceAgent()

    def answer(prompt):
        prompts.append(prompt)
        return json.dumps(
            {
                "status": "success",
                "sufficient": True,
                "answer": "The paper reports an x-ray finding. [PDF: paper.pdf p.2]",
                "used_pages": [2],
                "missing_topics": [],
            }
        )

    monkeypatch.setattr(agent, "_chat", answer)
    result = agent._query("What was the x-ray finding?", str(pdf), str(manifest))

    assert result["status"] == "success"
    assert result["used_pages"] == [2]
    assert "Relevant x-ray finding" in prompts[0]
    assert "SECRET PAGE ONE" not in prompts[0]
    assert "SECRET PAGE THREE" not in prompts[0]


def test_out_of_bounds_or_missing_citations_fail_closed(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    manifest = tmp_path / "extraction_manifest.json"
    _pdf(pdf, ["unprocessed claim", "eligible finding"])
    _manifest(manifest, pdf.name, {"extraction_state": "partial", "selected_pages": [2]})
    agent = PaperEvidenceAgent()
    monkeypatch.setattr(
        agent,
        "_chat",
        lambda prompt: json.dumps(
            {
                "status": "success",
                "sufficient": True,
                "answer": "A claim from an ineligible page. [PDF: paper.pdf p.1]",
                "used_pages": [1],
                "missing_topics": [],
            }
        ),
    )

    result = agent._query("What did it find?", str(pdf), str(manifest))

    assert result["status"] == "insufficient"
    assert result["sufficient"] is False
    assert result["used_pages"] == []


def test_full_query_sends_at_most_eight_ranked_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "full.pdf"
    manifest = tmp_path / "extraction_manifest.json"
    texts = [f"background page {number}" for number in range(1, 11)]
    texts[9] = "unique diffraction calibration result"
    _pdf(pdf, texts)
    _manifest(manifest, pdf.name, {"extraction_state": "full", "full": {}})
    prompts = []
    agent = PaperEvidenceAgent()

    def answer(prompt):
        prompts.append(prompt)
        return json.dumps(
            {
                "status": "success",
                "sufficient": True,
                "answer": "It reports the calibration result. [PDF: full.pdf p.10]",
                "used_pages": [10],
                "missing_topics": [],
            }
        )

    monkeypatch.setattr(agent, "_chat", answer)
    result = agent._query("diffraction calibration", str(pdf), str(manifest))

    assert result["sufficient"] is True
    assert prompts[0].count("--- full.pdf PAGE") == 8
    assert "--- full.pdf PAGE 10 ---" in prompts[0]


def test_corrupt_manifest_and_missing_pdf_stop_cleanly(tmp_path):
    agent = PaperEvidenceAgent()
    missing = agent._query("question", str(tmp_path / "missing.pdf"), str(tmp_path / "none.json"))
    assert missing["status"] == "missing_pdf"

    pdf = tmp_path / "paper.pdf"
    _pdf(pdf, ["text"])
    manifest = tmp_path / "extraction_manifest.json"
    manifest.write_text("not json", encoding="utf-8")
    corrupt = agent._query("question", str(pdf), str(manifest))
    assert corrupt["status"] == "manifest_error"


def test_invalid_claim_citations_get_one_repair_pass(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    manifest = tmp_path / "extraction_manifest.json"
    _pdf(pdf, ["First finding. Second finding."])
    _manifest(manifest, pdf.name, {"extraction_state": "partial", "selected_pages": [1]})
    replies = iter(
        [
            {
                "status": "success",
                "sufficient": True,
                "answer": "First finding. Second finding. [PDF: paper.pdf p.1]",
                "used_pages": [1],
                "missing_topics": [],
            },
            {
                "status": "success",
                "sufficient": True,
                "answer": (
                    "First finding. [PDF: paper.pdf p.1] "
                    "Second finding. [PDF: paper.pdf p.1]"
                ),
                "used_pages": [1],
                "missing_topics": [],
            },
        ]
    )
    prompts = []
    agent = PaperEvidenceAgent()

    def answer(prompt):
        prompts.append(prompt)
        return json.dumps(next(replies))

    monkeypatch.setattr(agent, "_chat", answer)
    result = agent._query("What were the findings?", str(pdf), str(manifest))

    assert result["status"] == "success"
    assert result["used_pages"] == [1]
    assert len(prompts) == 2
    assert "VALIDATION_ERRORS" in prompts[1]


def test_extracted_term_summary_is_deterministic_and_page_bounded(tmp_path):
    manifest = tmp_path / "extraction_manifest.json"
    terms = tmp_path / "terms.json"
    _manifest(
        manifest,
        "paper.pdf",
        {"extraction_state": "partial", "selected_pages": [2]},
    )
    terms.write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "term": "Eligible term",
                        "definition": "Supported definition.",
                        "category": "Method",
                        "pages": [2],
                        "source_papers": ["paper.pdf"],
                        "context_snippets": [
                            {"source_paper": "paper.pdf", "page": 2, "text": "Supported"}
                        ],
                    },
                    {
                        "term": "Unprocessed term",
                        "definition": "Must not appear.",
                        "pages": [1],
                        "source_papers": ["paper.pdf"],
                        "context_snippets": [
                            {"source_paper": "paper.pdf", "page": 1, "text": "Hidden"}
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = summarize_extracted_terms(str(terms), str(manifest), "paper.pdf")

    assert result["status"] == "success"
    assert result["term_count"] == 1
    assert "Eligible term" in result["answer"]
    assert "[PDF: paper.pdf p.2]" in result["answer"]
    assert "Unprocessed term" not in result["answer"]


def test_extracted_term_summary_prioritizes_initial_query_relevance(tmp_path):
    manifest = tmp_path / "extraction_manifest.json"
    terms = tmp_path / "terms.json"
    _manifest(
        manifest,
        "paper.pdf",
        {"extraction_state": "partial", "selected_pages": [2]},
    )
    terms.write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "term": "Ionic conductivity",
                        "definition": "Ion transport property in battery electrolytes.",
                        "category": "Property",
                        "pages": [2],
                        "source_papers": ["paper.pdf"],
                    },
                    {
                        "term": "Sample holder",
                        "definition": "Hardware used during measurement.",
                        "category": "Equipment",
                        "pages": [2],
                        "source_papers": ["paper.pdf"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = summarize_extracted_terms(
        str(terms),
        str(manifest),
        "paper.pdf",
        query="How does ionic conductivity affect battery electrolytes?",
        relevant_node_names=["Ionic conductivity"],
    )

    assert result["relevance_mode"] == "matched"
    assert result["relevant_term_count"] == 1
    assert "Ionic conductivity" in result["answer"]
    assert "Sample holder" not in result["answer"]
    assert "[PDF: paper.pdf p.2]" in result["answer"]
