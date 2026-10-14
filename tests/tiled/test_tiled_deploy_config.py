"""T20B: Tiled managed/external configuration rules (no server needed)."""
import json
import stat
from pathlib import Path

import pytest
import yaml

from app.modules.deployment import tiled_config as tc


def _env(**kw):
    return {k: str(v) for k, v in kw.items()}


def test_pin_is_exactly_0218():
    assert tc.PINNED_TILED_VERSION == "0.2.18"
    assert tc.PINNED_TILED_WHEEL_SHA256 == "416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06"


@pytest.mark.parametrize("profile,url", [("als-dev", "https://tiled-staging.als.lbl.gov"),
                                         ("als-prod", "https://tiled.als.lbl.gov")])
def test_als_profiles_are_external_with_fixed_url(profile, url):
    settings = tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE=profile))
    assert settings.mode == "external" and settings.managed is None
    assert settings.external.url == url
    assert settings.external.require_writer_role == "admin"
    # Trailing slash / case normalize to the same fixed URL.
    same = tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE=profile, F2W_TILED_URL=url.upper() + "/"))
    assert same.external.url == url


def test_als_profile_rejects_other_url_and_managed_mode():
    with pytest.raises(tc.TiledConfigError, match="connects only to"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="als-dev", F2W_TILED_URL="https://tiled.als.lbl.gov"))
    with pytest.raises(tc.TiledConfigError, match="does not allow"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="als-prod", F2W_TILED_MODE="managed"))
    with pytest.raises(tc.TiledConfigError, match="must not configure a managed server"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="als-prod", F2W_TILED_DATA_DIR="/x"))


def test_local_profile_is_managed_on_loopback_8765():
    settings = tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="local"))
    m = settings.managed
    assert settings.mode == "managed" and settings.external is None
    assert (m.host, m.port, str(m.data_dir)) == ("127.0.0.1", 8765, "/var/lib/fair2wise/tiled")
    assert m.owner == tc.OWNER_LOCAL
    with pytest.raises(tc.TiledConfigError, match="does not allow"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="local", F2W_TILED_MODE="external"))
    with pytest.raises(tc.TiledConfigError, match="always loopback"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="local", F2W_TILED_URL="http://127.0.0.1:1"))


@pytest.mark.parametrize("url", ["https://tiled.als.lbl.gov", "http://tiled-staging.als.lbl.gov:8000",
                                 "http://10.0.0.5:8765", "http://example.org"])
def test_test_profile_external_accepts_loopback_only(url):
    with pytest.raises(tc.TiledConfigError):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="test", F2W_TILED_MODE="external", F2W_TILED_URL=url))


def test_test_profile_external_loopback():
    settings = tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="test", F2W_TILED_MODE="external",
                                           F2W_TILED_URL="http://127.0.0.1:4321/api/graphql"))
    assert settings.external.url == "http://127.0.0.1:4321"
    assert settings.external.require_writer_role is None


def test_profile_required_and_secret_values_rejected():
    with pytest.raises(tc.TiledConfigError, match="F2W_DEPLOYMENT_PROFILE"):
        tc.load_tiled_settings({})
    with pytest.raises(tc.TiledConfigError, match="secret value"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="als-dev", F2W_TILED_WRITER_SECRET="abc"))


def test_legacy_variables_are_ignored():
    settings = tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="als-dev", TILED_URI="http://127.0.0.1:8765",
                                           TILED_API_KEY="k", F2W_LIVE_TILED="1"))
    assert settings.external.url == "https://tiled-staging.als.lbl.gov"
    assert settings.external.writer_secret_file is None


def test_version_override_must_match_pin():
    with pytest.raises(tc.TiledConfigError, match="pinned"):
        tc.load_tiled_settings(_env(F2W_DEPLOYMENT_PROFILE="als-dev", F2W_TILED_EXPECTED_VERSION="0.2.19"))


def test_managed_config_rejects_non_loopback_and_bad_owner(tmp_path):
    with pytest.raises(tc.TiledConfigError, match="loopback"):
        tc.ManagedTiledConfig(profile="local", data_dir=tmp_path, tiled_python=Path("/x"), host="0.0.0.0")
    with pytest.raises(tc.TiledConfigError, match="test profile"):
        tc.ManagedTiledConfig(profile="local", data_dir=tmp_path, tiled_python=Path("/x"), owner=tc.OWNER_TEST)
    with pytest.raises(tc.TiledConfigError, match="cannot run"):
        tc.ManagedTiledConfig(profile="als-dev", data_dir=tmp_path, tiled_python=Path("/x"))


def test_rendered_server_config_has_no_secrets(tmp_path):
    cfg = tc.ManagedTiledConfig(profile="test", data_dir=tmp_path, tiled_python=Path("/x"), port=0,
                                owner=tc.OWNER_TEST, authn=tc.AuthnSpec())
    rendered = tc.render_server_config(cfg, port=1234, init_catalog=True)
    text = yaml.safe_dump(rendered)
    assert "single_user_api_key" not in text and "secret_keys" not in text and "users_to_passwords" not in text
    assert rendered["authentication"]["allow_anonymous_access"] is False
    assert rendered["uvicorn"] == {"host": "127.0.0.1", "port": 1234}
    assert rendered["catalog"]["uri"] == f"sqlite:///{tmp_path / 'catalog.db'}"
    assert rendered["authentication"]["tiled_admins"] == [{"provider": "f2wtest", "id": "admin"}]
    assert "allow_origins" not in rendered  # no browser origins for local Tiled
    assert tc.render_server_config(cfg, port=1, init_catalog=False)["catalog"]["init_if_not_exists"] is False


def test_secret_file_helpers(tmp_path):
    path = tmp_path / "s" / "writer.key"
    tc.write_private_file(path, "abc123\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert tc.read_secret_file(path, alphanumeric=True) == "abc123"
    path.write_text("not-alnum!")
    with pytest.raises(tc.TiledConfigError) as err:
        tc.read_secret_file(path, alphanumeric=True)
    assert "not-alnum" not in str(err.value)
    with pytest.raises(tc.TiledConfigError, match="Cannot read"):
        tc.read_secret_file(tmp_path / "missing")
    assert len(tc.generate_api_key()) == 64 and tc.generate_api_key().isalnum()


def test_authn_spec_validation():
    with pytest.raises(tc.TiledConfigError):
        tc.AuthnSpec(users=("a",), admins=("b",))
    assert json.dumps(tc.AuthnSpec().__dict__)  # serializable, no secrets
