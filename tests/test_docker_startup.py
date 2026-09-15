from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_does_not_bake_secrets():
    text = (ROOT / "Dockerfile").read_text()
    assert "COPY .env" not in text
    assert "ARG CBORG_API_KEY" not in text
    assert "ENV CBORG_API_KEY" not in text
    assert "VITE_F2W_AGENT_API_URL=http://127.0.0.1:8090" in text


def test_compose_product_stack_is_agent_and_ui():
    text = (ROOT / "compose.yaml").read_text()
    assert "env_file:" in text
    assert "CBORG_IPV6_BIND: \"\"" in text or "CBORG_IPV6_BIND: ''" in text
    assert "CBORG_FORCE_IPV6" in text
    assert "CBORG_IP_FAMILY: ipv6" in text
    assert "matkg_rsoxs_v1.json" in text
    assert "matkg_bl1101_v2.json" in text
    assert "matkg_bl1101_v1.json" in text
    assert "rsoxs_schema.yaml" in text
    assert "bl1101_schema.yaml" in text
    assert "matkg_xray_papers_cborg_chat.json" not in text.split("F2W_GRAPH:", 1)[-1].split("\n", 1)[0]
    assert "127.0.0.1:8090:8090" in text
    assert "${F2W_UI_PORT:-5175}" in text
    assert "127.0.0.1:5174" not in text
    assert ":-5174" not in text
    assert "CBORG_API_KEY:" not in text
    assert "enable_ipv6: true" in text
    services = ["agent", "frontend"]
    for name in services:
        assert f"  {name}:" in text
    assert "  splash:" not in text


def test_dockerignore_excludes_env():
    text = (ROOT / ".dockerignore").read_text()
    assert ".env" in text
    assert "!.env.example" in text
