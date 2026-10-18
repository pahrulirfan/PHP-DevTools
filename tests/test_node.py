import io
import zipfile

import pytest

from pipanel.core.node import BEGIN, END, Node, NodeError, ShellIntegration, fnm_asset, parse_list, parse_remote


def test_parse_list():
    out = "* v18.19.0\n* v20.11.1 default\n* system\n"
    assert parse_list(out) == [("18.19.0", False), ("20.11.1", True)]


def test_parse_remote_limits_per_major():
    txt = "\n".join(f"v22.{i}.0" for i in range(10)) + "\nv20.1.0\nv20.2.0 (Iron)\nv0.12.0\n"
    got = parse_remote(txt, per_major=2, majors=2)
    assert got == ["22.9.0", "22.8.0", "20.2.0", "20.1.0"]


def test_assets():
    assert fnm_asset("x86_64") == "fnm-linux" and fnm_asset("aarch64") == "fnm-arm64"
    with pytest.raises(NodeError):
        fnm_asset("riscv")


def _zip(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "BIN")
    return b.getvalue()


@pytest.mark.posix
def test_install_fnm(paths, runner):
    n = Node(paths, runner)
    urls = []
    n.install_fnm(lambda u: (urls.append(u), _zip(["fnm"]))[1], "x86_64")
    assert urls == ["https://github.com/Schniz/fnm/releases/latest/download/fnm-linux.zip"]
    assert n.available() and n.fnm_bin.stat().st_mode & 0o100
    with pytest.raises(NodeError):
        n.install_fnm(lambda u: _zip(["other"]), "x86_64")


def test_commands_validate_version(paths, runner):
    n = Node(paths, runner)
    n.install("20")
    assert runner.calls[-1][1:] == ["install", "20"] and runner.last_kw["env"]["FNM_DIR"] == str(n.fnm_dir)
    n.set_default("lts-latest")
    for bad in ("20; rm -rf", "../x", "", "v1.2.3.4"):
        with pytest.raises(NodeError):
            n.install(bad)


def test_shell_integration_idempotent_and_removable(paths, runner, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("alias ll='ls -l'\n")
    (home / ".zshrc").write_text("# zsh\n")
    sh = ShellIntegration(paths, home, Node(paths, runner))
    sh.install()
    first = (home / ".bashrc").read_text()
    sh.install()
    assert (home / ".bashrc").read_text() == first and first.count(BEGIN) == 1
    assert "alias ll" in first and "--shell bash" in first and f'PATH="{paths.bin}:$PATH"' in first
    assert "--shell zsh" in (home / ".zshrc").read_text()
    prof = (home / ".profile").read_text()
    assert "PATH" in prof and "fnm env" not in prof
    assert sh.installed()
    sh.remove()
    assert (home / ".bashrc").read_text().strip() == "alias ll='ls -l'"
    assert BEGIN not in (home / ".zshrc").read_text() and not sh.installed()
