"""The publisher's pure parts: discovery, token resolution, credentials."""

import pytest

import publish_specs
from forgelab.proc import LabError


def make_lab(tmp_path):
    """A stand-in lab/ tree: two plans, a shared dir, and a stray file."""
    (tmp_path / "provisioncluster").mkdir()
    (tmp_path / "provisioncluster" / "ProvisionClusterSpec.java").write_text("")
    (tmp_path / "agentimage").mkdir()
    (tmp_path / "agentimage" / "BuildAgentImageSpec.java").write_text("")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "SpecConstants.java").write_text("")
    (tmp_path / "provisioncluster" / "README.md").write_text("")
    return tmp_path


def test_discovers_one_class_per_plan_directory(tmp_path):
    assert publish_specs.spec_classes(make_lab(tmp_path)) == [
        "lab.agentimage.BuildAgentImageSpec",
        "lab.provisioncluster.ProvisionClusterSpec",
    ]


def test_discovery_ignores_shared_and_non_spec_files(tmp_path):
    classes = publish_specs.spec_classes(make_lab(tmp_path))
    assert not any("shared" in c or "README" in c for c in classes)


def test_discovers_the_real_tree():
    from forgelab import paths

    classes = publish_specs.spec_classes(paths.LAB_DIR)
    assert "lab.provisioncluster.ProvisionClusterSpec" in classes
    assert "lab.deprovisioncluster.DeprovisionClusterSpec" in classes


def test_environment_token_beats_the_file(tmp_path):
    pat = tmp_path / "bamboo_pat"
    pat.write_text("from-file\n")
    env = {"FORGELAB_BAMBOO_PAT": "from-env"}
    assert publish_specs.resolve_token(env, pat) == "from-env"


def test_token_falls_back_to_the_file(tmp_path):
    pat = tmp_path / "bamboo_pat"
    pat.write_text("  from-file\n")
    assert publish_specs.resolve_token({}, pat) == "from-file"


def test_missing_token_names_the_file(tmp_path):
    with pytest.raises(LabError, match="bamboo_pat"):
        publish_specs.resolve_token({}, tmp_path / "bamboo_pat")


def test_empty_token_file_is_an_error(tmp_path):
    pat = tmp_path / "bamboo_pat"
    pat.write_text("\n")
    with pytest.raises(LabError, match="bamboo_pat"):
        publish_specs.resolve_token({}, pat)


def test_credentials_are_the_format_maven_reads():
    assert publish_specs.render_credentials("abc123") == "token=abc123\n"


def test_credentials_are_written_owner_only(tmp_path):
    dest = tmp_path / ".credentials"
    publish_specs.write_credentials("abc123", dest)
    assert dest.read_text() == "token=abc123\n"
    assert oct(dest.stat().st_mode)[-3:] == "600"


def test_credentials_overwrite_an_existing_file(tmp_path):
    dest = tmp_path / ".credentials"
    dest.write_text("token=stale\n")
    dest.chmod(0o644)  # Start with a looser mode
    publish_specs.write_credentials("fresh", dest)
    assert dest.read_text() == "token=fresh\n"
    assert oct(dest.stat().st_mode)[-3:] == "600"


@pytest.fixture
def publishes(monkeypatch, tmp_path):
    """Record the commands main() would run; keep it off the network."""
    calls = []
    monkeypatch.setattr(publish_specs.proc, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(publish_specs.proc, "require_tools", lambda *tools: None)
    monkeypatch.setattr(publish_specs, "server_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(publish_specs.paths, "SPECS_ROOT", tmp_path)
    monkeypatch.setenv("FORGELAB_BAMBOO_PAT", "test-token")
    return calls


def test_main_publishes_every_discovered_class(publishes):
    publish_specs.main([])
    published = [
        arg for args, _ in publishes for arg in args if arg.startswith("-Dexec.mainClass=")
    ]
    assert "-Dexec.mainClass=lab.provisioncluster.ProvisionClusterSpec" in published
    assert len(published) == len(publish_specs.spec_classes(publish_specs.paths.LAB_DIR))


def test_main_writes_credentials_next_to_the_pom(publishes, tmp_path):
    publish_specs.main([])
    assert (tmp_path / ".credentials").read_text() == "token=test-token\n"


def test_main_runs_maven_from_the_specs_root(publishes, tmp_path):
    publish_specs.main([])
    assert all(kwargs["cwd"] == tmp_path for _, kwargs in publishes)


def test_skip_flag_returns_quietly_when_bamboo_is_down(monkeypatch, capsys):
    monkeypatch.setattr(publish_specs, "server_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(
        publish_specs.proc, "run", lambda *a, **kw: pytest.fail("must not publish")
    )
    publish_specs.main(["--skip-if-unreachable"])
    assert "skipping" in capsys.readouterr().out


def test_without_the_flag_an_unreachable_server_is_an_error(monkeypatch):
    monkeypatch.setattr(publish_specs, "server_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(
        publish_specs.proc, "run", lambda *a, **kw: pytest.fail("must not publish")
    )
    with pytest.raises(LabError, match="unreachable"):
        publish_specs.main([])


def test_unknown_argument_is_rejected():
    with pytest.raises(LabError, match="usage:"):
        publish_specs.main(["--force"])


def test_reachability_probe_is_false_for_a_closed_port():
    assert publish_specs.server_reachable("http://127.0.0.1:1", timeout=0.5) is False
