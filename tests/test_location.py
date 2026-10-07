import pytest
from lsst.codemetrics.location import ENV_VAR, REPOS_SUBDIR, DataRoot, config_file, data_root


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Keep the real environment and real config file out of every test.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Fixture used to unset the environment variable, redirect the
        config file lookup, and move off the real working directory.
    tmp_path : `~pathlib.Path`
        Temporary directory to point the config lookup and the working
        directory at.
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path / "cwd" if (tmp_path / "cwd").mkdir() or True else tmp_path)


def write_config(text):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_explicit_beats_everything(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from_env"))
    write_config(f'data_dir = "{tmp_path / "from_config"}"\n')
    result = data_root(tmp_path / "explicit")
    assert result.path == tmp_path / "explicit"
    assert result.source == "argument"


def test_environment_beats_config(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from_env"))
    write_config(f'data_dir = "{tmp_path / "from_config"}"\n')
    result = data_root()
    assert result.path == tmp_path / "from_env"
    assert result.source == ENV_VAR


def test_config_used_when_environment_is_unset(tmp_path):
    write_config(f'data_dir = "{tmp_path / "from_config"}"\n')
    result = data_root()
    assert result.path == tmp_path / "from_config"
    assert "config" in result.source


def test_current_directory_is_the_last_resort():
    from pathlib import Path

    result = data_root()
    assert result.path == Path.cwd()
    assert result.source == "current directory"


def test_empty_environment_variable_is_treated_as_unset(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, "")
    write_config(f'data_dir = "{tmp_path / "from_config"}"\n')
    assert data_root().path == tmp_path / "from_config"


def test_user_home_is_expanded(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv(ENV_VAR, "~/somewhere")
    assert data_root().path == Path.home() / "somewhere"


def test_config_without_the_key_falls_through():
    from pathlib import Path

    write_config('unrelated = "value"\n')
    assert data_root().path == Path.cwd()


def test_malformed_config_is_reported_not_ignored():
    path = write_config("this is not = valid = toml\n")
    with pytest.raises(ValueError, match=str(path.name)):
        data_root()


def test_config_file_respects_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    assert config_file().parent.parent == tmp_path / "elsewhere"


def test_config_file_defaults_under_dot_config(monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_file() == Path.home() / ".config" / "code-metrics" / "config.toml"


def test_repos_subdir_is_the_documented_name():
    assert REPOS_SUBDIR == "repos"


def test_data_root_reports_where_it_came_from(tmp_path):
    result = data_root(tmp_path)
    assert isinstance(result, DataRoot)
    assert "argument" in result.describe()
    assert str(tmp_path) in result.describe()
