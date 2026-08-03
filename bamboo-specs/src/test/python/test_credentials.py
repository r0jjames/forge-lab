import stat

import pytest

from forgelab import credentials, registry


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.paths, "FORGELAB_HOME", tmp_path)
    return tmp_path


def test_generate_covers_only_the_enabled_addons():
    assert sorted(credentials.generate(["splunk"])) == ["splunk_admin_password"]


def test_generate_covers_keycloak_admin_and_app_user():
    assert sorted(credentials.generate(["keycloak"])) == [
        "keycloak_admin_password",
        "keycloak_app_user_password",
    ]


def test_generate_is_empty_for_an_addon_with_no_secrets():
    assert credentials.generate(["hdfs"]) == {}


def test_generate_is_empty_for_no_addons():
    assert credentials.generate([]) == {}


def test_generate_never_repeats_a_password():
    values = credentials.generate(["keycloak", "splunk"])
    assert len(set(values.values())) == 3


def test_generate_passwords_are_long_enough_for_splunk():
    """Splunk refuses an admin password shorter than 8 characters."""
    values = credentials.generate(["splunk"])
    assert len(values["splunk_admin_password"]) >= 8


def test_render_quotes_values_and_sorts_keys():
    text = credentials.render("lab1", {"b_password": "two", "a_password": "one"})
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert lines == ['a_password: "one"', 'b_password: "two"']


def test_render_names_the_cluster_in_the_header():
    assert "lab1" in credentials.render("lab1", {})


def test_write_is_owner_only(home):
    creds = credentials.write("lab1", {"splunk_admin_password": "hunter22"})
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600


def test_write_lands_beside_the_ssh_key_not_in_the_repo(home):
    assert credentials.write("lab1", {}).parent == home


def test_read_round_trips_what_write_wrote(home):
    values = {"splunk_admin_password": "hunter22", "keycloak_admin_password": "abc-_1"}
    credentials.write("lab1", values)
    assert credentials.read("lab1") == values


def test_read_is_empty_when_there_is_no_file(home):
    assert credentials.read("nosuch") == {}


def test_remove_deletes_the_file(home):
    credentials.write("lab1", {"splunk_admin_password": "hunter22"})
    credentials.remove("lab1")
    assert not credentials.path("lab1").exists()


def test_remove_is_quiet_when_there_is_no_file(home):
    credentials.remove("nosuch")


def test_registry_records_the_pointer_not_the_secret(home, monkeypatch):
    monkeypatch.setattr(registry.paths, "SSH_KEY", home / "id_ed25519")
    text = registry.render(
        "lab1", "k8s", "2026-08-03T10:00:00Z", [], [],
        credentials=home / "lab1-credentials.yml",
    )
    assert "lab1-credentials.yml" in text
    assert "hunter22" not in text
    assert "password" not in text


def test_registry_omits_the_pointer_when_there_are_no_credentials(home, monkeypatch):
    monkeypatch.setattr(registry.paths, "SSH_KEY", home / "id_ed25519")
    text = registry.render("lab1", "k8s", "2026-08-03T10:00:00Z", [], [])
    assert "credentials:" not in text
