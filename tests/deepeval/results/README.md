# tests/deepeval/results

Deepeval test run artifacts live here.

## Contents

| File | Description |
|------|-------------|
| `deepeval_results.xml` | JUnit XML output from the most recent `pytest` run |

## Sanitization rules

Generated XML files must contain **only** pytest/deepeval metric summaries.
Do **not** store:
- API keys or tokens
- Raw LLM responses beyond what deepeval records in `<failure>` messages
- Full prompt text or retrieved context payloads
- Graph node payloads, PDF snippets, or other runtime data

## Generating results

```bash
pytest tests/deepeval/ -v \
  --junitxml=tests/deepeval/results/deepeval_results.xml
```

Timestamps and hostnames are redacted before committing (see pre-commit hooks).
