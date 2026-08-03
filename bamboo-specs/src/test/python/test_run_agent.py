"""The agent's environment handoff: one registry location for every plan."""

import run_agent


def test_points_jobs_at_the_clone_the_agent_runs_from():
    env = run_agent.registry_dir_env({"PATH": "/usr/bin"})
    assert env["FORGELAB_REGISTRY_DIR"] == str(
        run_agent.CLONE_ROOT / "cluster_registered"
    )
    assert env["PATH"] == "/usr/bin"


def test_leaves_an_explicit_setting_alone():
    env = run_agent.registry_dir_env({"FORGELAB_REGISTRY_DIR": "/elsewhere"})
    assert env["FORGELAB_REGISTRY_DIR"] == "/elsewhere"


def test_the_clone_root_is_a_checkout():
    assert (run_agent.CLONE_ROOT / "Makefile").is_file()
