from app.modules.term_extractor import source_repos


def test_extract_github_repositories_dedupes_and_ignores_issue_links():
    text = (
        "Code: https://github.com/scipy/scipy/blob/main/scipy/signal/_peak_finding.py "
        "and duplicate https://github.com/scipy/scipy.git. "
        "Discussion: https://github.com/scipy/scipy/issues/123"
    )

    refs = source_repos.extract_github_repositories(text)

    assert refs == [source_repos.GitHubRepoRef(owner="scipy", repo="scipy")]
    assert refs[0].url == "https://github.com/scipy/scipy"


def test_extract_python_blocks_keeps_decorators_and_line_numbers():
    source = (
        "import numpy as np\n\n"
        "@decorator\n"
        "def find_peaks_for_q(q, intensity):\n"
        "    values = np.asarray(intensity)\n"
        "    baseline = values.mean()\n"
        "    centered = values - baseline\n"
        "    return [i for i, value in enumerate(centered) if value > 0]\n"
    )

    blocks = source_repos.extract_python_blocks(source)

    assert len(blocks) == 1
    assert blocks[0].name == "find_peaks_for_q"
    assert blocks[0].start_line == 3
    assert blocks[0].end_line == 8
    assert blocks[0].source.startswith("@decorator\n")


def test_extract_github_code_snippets_fetches_function_level_source():
    source = (
        "import numpy as np\n\n"
        "def find_peaks_for_q(q, intensity):\n"
        "    values = np.asarray(intensity)\n"
        "    baseline = values.mean()\n"
        "    centered = values - baseline\n"
        "    peaks = [i for i, value in enumerate(centered) if value > centered.std()]\n"
        "    return peaks, centered\n"
    )

    class FakeResponse:
        def __init__(self, payload=None, text="", status_code=200):
            self._payload = payload
            self.text = text
            self.status_code = status_code
            self.headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs.get("headers", {})))
            if url == "https://api.github.com/repos/example/peaks":
                return FakeResponse({"default_branch": "main", "license": {"spdx_id": "MIT"}})
            if url == "https://api.github.com/repos/example/peaks/commits/main":
                return FakeResponse({"sha": "abc123"})
            if url == "https://api.github.com/repos/example/peaks/git/trees/abc123?recursive=1":
                return FakeResponse({
                    "tree": [
                        {"type": "blob", "path": "src/peaks.py", "size": len(source)},
                        {"type": "blob", "path": "tests/test_peaks.py", "size": len(source)},
                    ]
                })
            if url == "https://raw.githubusercontent.com/example/peaks/abc123/src/peaks.py":
                return FakeResponse(text=source)
            raise AssertionError(url)

    paper_text = (
        "The analysis code is available at https://github.com/example/peaks. "
        "We call find_peaks_for_q(q, intensity) for SAXS peak detection."
    )
    client = source_repos.GitHubSourceClient(token="tok", session=FakeSession())

    snippets = source_repos.extract_github_code_snippets(
        paper_text,
        source_paper="paper.pdf",
        client=client,
    )

    assert len(snippets) == 1
    snippet = snippets[0]
    assert snippet["source_type"] == "github"
    assert snippet["source_paper"] == "paper.pdf"
    assert snippet["function_name"] == "find_peaks_for_q"
    assert snippet["repo_url"] == "https://github.com/example/peaks"
    assert snippet["repo_commit_sha"] == "abc123"
    assert snippet["repository_license"] == "MIT"
    assert snippet["source_file_path"] == "src/peaks.py"
    assert "def find_peaks_for_q" in snippet["code_snippet"]
