"""Exercise the OPSEC gate with synthetic, isolated Git histories."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT
from tests.support import minimal_subprocess_env

SCRIPT = PROJECT_ROOT / "tools" / "opsec_scan.sh"
RULE_CASES = [
    ("email", "mcq08" + "9"),
    ("vocab-a", "bukmache" + "r"),
    ("vocab-b", "bettin" + "g"),
    ("project", "CS2Op" + "s"),
    ("name", "Micha" + "l"),
    ("name", "Micha" + "ł"),
]


class Repo:
    def __init__(self, path, git, bash, env):
        self.path, self.git_exe, self.bash, self.env = path, git, bash, env

    def run(self, args, *, cwd=None):
        return subprocess.run(  # noqa: S603 -- local executables, synthetic repositories
            args, cwd=cwd or self.path, env=self.env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
        )

    def git(self, *args):
        result = self.run([
            self.git_exe, "-c", "user.name=TEST", "-c", "user.email=test@example.invalid",
            *args,
        ])
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def commit(self, content="TEST-clean\n", path="sample.txt"):
        target = self.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-m", "TEST-change")
        return self.git("rev-parse", "HEAD")

    def scan(self, *args, cwd=None):
        return self.run([self.bash, SCRIPT.as_posix(), *args], cwd=cwd)


@pytest.fixture
def repo(tmp_path):
    git = shutil.which("git")
    bash = shutil.which("bash")
    if git and os.name == "nt":
        git_bash = Path(git).parent.parent / "bin" / "bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    if not git or not bash:
        pytest.skip("OPSEC scanner requires Git and Bash")
    path = tmp_path / "repo"
    path.mkdir()
    env = minimal_subprocess_env(tmp_path, TMPDIR=tmp_path.as_posix(), GIT_CONFIG_NOSYSTEM="1")
    result = Repo(path, git, bash, env)
    result.git("init", "--initial-branch=fixture")
    result.commit()
    return result


def test_tree_clean(repo):
    result = repo.scan("--tree")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("rule,value", RULE_CASES, ids=[r[0] + str(i) for i, r in enumerate(RULE_CASES)])
def test_tree_matches_all_rules_case_insensitively(repo, rule, value):
    (repo.path / "sample.txt").write_text(value.upper() + "\n", encoding="utf-8")
    result = repo.scan("--tree")
    assert result.returncode == 1, result.stderr
    assert value.upper() not in result.stdout + result.stderr


def test_tree_excludes_media(repo):
    for path in ("image.png", "clip.mp4"):
        repo.commit(RULE_CASES[0][1] + "\n", path)
    assert repo.scan("--tree").returncode == 0


@pytest.mark.parametrize("mode", ["tree", "range"])
def test_utf8_rule_does_not_match_unrelated_character(repo, mode):
    repo.env["LC_ALL"] = "C"
    base = repo.git("rev-parse", "HEAD")
    repo.commit("Micha" + chr(0x144) + "\n")
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("rule,value", RULE_CASES, ids=[r[0] + str(i) for i, r in enumerate(RULE_CASES)])
def test_range_finds_added_then_removed_content(repo, rule, value):
    base = repo.git("rev-parse", "HEAD")
    hit = repo.commit(value.upper() + "\n" + value + "\n")
    repo.commit()
    result = repo.scan("--range", f"{base}..HEAD")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{hit}:sample.txt:{rule}"]
    assert value not in result.stdout + result.stderr
    assert repo.scan("--tree").returncode == 0


@pytest.mark.parametrize("mismatch", [None, "sha", "path", "rule"])
def test_allowlist_binds_full_commit_path_and_rule(repo, mismatch):
    base = repo.git("rev-parse", "HEAD")
    hit = repo.commit(RULE_CASES[0][1] + "\n", "folder/sample file.txt")
    repo.commit(path="folder/sample file.txt")
    sha = base if mismatch == "sha" else hit
    path = "other.txt" if mismatch == "path" else "folder/sample file.txt"
    rule = "name" if mismatch == "rule" else "email"
    allow = repo.path / "allow.txt"
    allow.write_text(f"# TEST-exception\n{sha}:{path}:{rule}\n", encoding="utf-8")
    result = repo.scan("--range", f"{base}..HEAD", "--allowlist", "allow.txt")
    assert result.returncode == (0 if mismatch is None else 1), result.stderr
    expected = [] if mismatch is None else [f"{hit}:folder/sample file.txt:email"]
    assert result.stdout.splitlines() == expected


def test_range_ignores_content_before_base(repo):
    base = repo.commit(RULE_CASES[0][1] + "\n")
    repo.commit()
    result = repo.scan("--range", f"{base}..HEAD")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("rev_range", [
    "missing..HEAD", "HEAD..missing", "", "HEAD..HEAD", "..HEAD", "HEAD..",
    "HEAD...HEAD", "0" * 40 + "..HEAD", "HEAD.." + "0" * 40,
])
def test_invalid_or_empty_range_fails_closed(repo, rev_range):
    result = repo.scan("--range", rev_range)
    assert result.returncode == 2, result.stderr


@pytest.mark.parametrize("allow_content", [None, "invalid\n", "a" * 40 + ":sample.txt:unknown\n"])
def test_missing_or_invalid_allowlist_fails_closed(repo, allow_content):
    base = repo.git("rev-parse", "HEAD")
    repo.commit("TEST-next\n")
    if allow_content is not None:
        (repo.path / "allow.txt").write_text(allow_content, encoding="utf-8")
    result = repo.scan("--range", f"{base}..HEAD", "--allowlist", "allow.txt")
    assert result.returncode == 2, result.stderr


def test_tree_git_error_fails_closed(repo, tmp_path):
    result = repo.scan("--tree", cwd=tmp_path)
    assert result.returncode == 2, result.stderr


def test_added_header_like_line_is_still_scanned(repo):
    base = repo.git("rev-parse", "HEAD")
    hit = repo.commit("++ b/" + RULE_CASES[0][1] + "\n")
    result = repo.scan("--range", f"{base}..HEAD")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{hit}:sample.txt:email"]


def test_scanner_and_allowlist_do_not_match_their_own_rules(repo):
    for path in ("tools/opsec_scan.sh", ".opsec-history-allow", "tests/test_opsec_scan.py"):
        source = PROJECT_ROOT / path
        assert source.is_file(), f"Missing gate artifact: {path}"
        target = repo.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        repo.git("add", "--", path)
    result = repo.scan("--tree")
    assert result.returncode == 0, result.stderr
