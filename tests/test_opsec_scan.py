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

    def run(self, args, *, cwd=None, input=None, env=None):
        return subprocess.run(  # noqa: S603 -- local executables, synthetic repositories
            args, cwd=cwd or self.path, env=env or self.env, input=input, capture_output=True,
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
        command = [self.bash, SCRIPT.as_posix(), *args]
        if "OPSEC_TEST_TOOL_DIR" in self.env:
            # Git Bash prepends its own executable directories on Windows.
            # Install a fault-injection wrapper after that startup adjustment.
            command = [
                self.bash, "--noprofile", "--norc", "-c",
                'tool_dir=$(cd -- "$OPSEC_TEST_TOOL_DIR" && pwd) || exit 2; '
                'export PATH="$tool_dir:$PATH"; source "$@"',
                "TEST-scanner", SCRIPT.as_posix(), *args,
            ]
        return self.run(command, cwd=cwd)

    def git_bytes(self, *args, input=None):
        result = subprocess.run(  # noqa: S603 -- local Git, synthetic bytes only
            [self.git_exe, *args], cwd=self.path, env=self.env, input=input,
            capture_output=True, timeout=30, check=False,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        return result.stdout

    def commit_bytes(self, content, path="sample.txt"):
        target = self.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.git("add", "--", path)
        self.git("commit", "-m", "TEST-change")
        return self.git("rev-parse", "HEAD")

    def graph_commit(self, tree, *parents, message="TEST-graph", identity=None):
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(["-p", parent])
        env = self.env.copy()
        env.update({
            "GIT_AUTHOR_NAME": "TEST", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "TEST", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })
        env.update(identity or {})
        result = self.run([self.git_exe, *args], input=message + "\n", env=env)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def tree_with_file(self, path, content=b"TEST-clean\n"):
        blob = self.git_bytes("hash-object", "-w", "--stdin", input=content).strip()
        record = b"100644 blob " + blob + b"\t" + path.encode("utf-8") + b"\0"
        return self.git_bytes("mktree", "-z", input=record).decode("ascii").strip()


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
    ceiling = tmp_path / "ceiling"
    cwd = ceiling / "child"
    cwd.mkdir(parents=True)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        repo.env.pop(key, None)
    repo.env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    result = repo.scan("--tree", cwd=cwd)
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


@pytest.mark.parametrize("position", ["before", "after"])
def test_range_invalid_utf8_preserves_exact_fingerprint(repo, position):
    base = repo.git("rev-parse", "HEAD")
    value = RULE_CASES[0][1].encode("ascii")
    content = b"\xff" + value if position == "before" else value + b"\xff"
    hit = repo.commit_bytes(content + b"\n")
    repo.commit()
    result = repo.scan("--range", f"{base}..HEAD")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{hit}:sample.txt:email"]


def test_tree_invalid_utf8_before_match(repo):
    repo.commit_bytes(b"\xff" + RULE_CASES[0][1].encode("ascii") + b"\n")
    result = repo.scan("--tree")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == ["sample.txt:email"]


@pytest.mark.parametrize("mode", ["tree", "range"])
def test_nul_text_is_scanned(repo, mode):
    base = repo.git("rev-parse", "HEAD")
    hit = repo.commit_bytes(b"TEST\0" + RULE_CASES[0][1].encode("ascii") + b"\0\n")
    if mode == "range":
        repo.commit()
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    expected = "sample.txt:email" if mode == "tree" else f"{hit}:sample.txt:email"
    assert result.stdout.splitlines() == [expected]


@pytest.mark.parametrize("mode", ["tree", "range"])
def test_attributes_cannot_disable_text_scan(repo, mode):
    repo.commit("sample.txt -diff\n", ".gitattributes")
    base = repo.git("rev-parse", "HEAD")
    hit = repo.commit(RULE_CASES[0][1] + "\n")
    if mode == "range":
        repo.commit()
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    expected = "sample.txt:email" if mode == "tree" else f"{hit}:sample.txt:email"
    assert result.stdout.splitlines() == [expected]


@pytest.mark.parametrize("mode", ["tree", "range"])
@pytest.mark.parametrize("encoding,bom", [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
@pytest.mark.parametrize(
    "rule,value", [RULE_CASES[0], RULE_CASES[4], RULE_CASES[5]],
    ids=["email", "ascii-name", "utf-name"],
)
@pytest.mark.parametrize("uppercase", [False, True])
@pytest.mark.parametrize("with_bom", [False, True], ids=["no-bom", "bom"])
def test_utf16_matches_ascii_and_both_name_forms(
    repo, mode, encoding, bom, rule, value, uppercase, with_bom,
):
    repo.env["LC_ALL"] = "C.UTF-8"
    base = repo.git("rev-parse", "HEAD")
    content = (value.upper() if uppercase else value) + "\n"
    hit = repo.commit_bytes((bom if with_bom else b"") + content.encode(encoding))
    if mode == "range":
        repo.commit()
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    expected = f"sample.txt:{rule}" if mode == "tree" else f"{hit}:sample.txt:{rule}"
    assert result.stdout.splitlines() == [expected]


@pytest.mark.parametrize("extension", ["png", "mp4"])
@pytest.mark.parametrize("mode", ["tree", "range"])
def test_binary_media_content_is_excluded(repo, extension, mode):
    base = repo.git("rev-parse", "HEAD")
    path = "TEST-media." + extension
    repo.commit_bytes(b"\0\xff" + RULE_CASES[0][1].encode("ascii") + b"\n", path)
    if mode == "range":
        repo.commit_bytes(b"TEST-clean\n", path)
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_evil_merge_resolution_reports_merge_sha(repo):
    base = repo.git("rev-parse", "HEAD")
    main_tree = repo.tree_with_file("sample.txt", b"TEST-main\n")
    side_tree = repo.tree_with_file("sample.txt", b"TEST-side\n")
    main = repo.graph_commit(main_tree, base)
    side = repo.graph_commit(side_tree, base)
    merged_tree = repo.tree_with_file("sample.txt", (RULE_CASES[0][1] + "\n").encode())
    merge = repo.graph_commit(merged_tree, main, side)
    head = repo.graph_commit(main_tree, merge)
    result = repo.scan("--range", f"{base}..{head}")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{merge}:sample.txt:email"]


def test_merge_restoration_reports_merge_sha(repo):
    base = repo.commit(RULE_CASES[0][1] + "\n")
    inherited_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    clean_tree = repo.tree_with_file("sample.txt")
    first = repo.graph_commit(clean_tree, base)
    second = repo.graph_commit(inherited_tree, base)
    merge = repo.graph_commit(inherited_tree, first, second)
    head = repo.graph_commit(clean_tree, merge)
    result = repo.scan("--range", f"{base}..{head}")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{merge}:sample.txt:email"]


def test_merge_reuses_allowlisted_nonmerge_origin(repo):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    side = repo.commit(RULE_CASES[0][1] + "\n")
    side_tree = repo.git("rev-parse", f"{side}^{{tree}}")
    main = repo.graph_commit(clean_tree, base)
    merge = repo.graph_commit(side_tree, main, side)
    head = repo.graph_commit(clean_tree, merge)
    (repo.path / "allow.txt").write_text(f"{side}:sample.txt:email\n", encoding="utf-8")
    result = repo.scan("--range", f"{base}..{head}", "--allowlist", "allow.txt")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("path,prefix", [("other.txt", ""), ("sample.txt", "TEST-prefix ")])
def test_merge_provenance_requires_identical_line_and_path(repo, path, prefix):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    value = RULE_CASES[0][1]
    side = repo.commit(value + "\n")
    main = repo.graph_commit(clean_tree, base)
    merged_tree = repo.tree_with_file(path, (prefix + value + "\n").encode())
    merge = repo.graph_commit(merged_tree, main, side)
    (repo.path / "allow.txt").write_text(f"{side}:sample.txt:email\n", encoding="utf-8")
    result = repo.scan("--range", f"{base}..{merge}", "--allowlist", "allow.txt")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{merge}:{path}:email"]


def test_merge_origin_must_be_nonmerge_commit(repo):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    first = repo.graph_commit(clean_tree, base, message="TEST-first")
    second = repo.graph_commit(clean_tree, base, message="TEST-second")
    hit_tree = repo.tree_with_file("sample.txt", (RULE_CASES[0][1] + "\n").encode())
    source_merge = repo.graph_commit(hit_tree, first, second)
    clean_branch = repo.graph_commit(clean_tree, base, message="TEST-clean-branch")
    restoring_merge = repo.graph_commit(hit_tree, clean_branch, source_merge)
    head = repo.graph_commit(clean_tree, restoring_merge)
    result = repo.scan("--range", f"{base}..{head}")
    assert result.returncode == 1, result.stderr
    assert set(result.stdout.splitlines()) == {
        f"{source_merge}:sample.txt:email", f"{restoring_merge}:sample.txt:email",
    }


def test_merging_clean_base_into_branch_is_clean(repo):
    base = repo.git("rev-parse", "HEAD")
    tree = repo.git("rev-parse", f"{base}^{{tree}}")
    new_base = repo.graph_commit(tree, base, message="TEST-new-base")
    branch = repo.graph_commit(tree, base, message="TEST-branch")
    merge = repo.graph_commit(tree, branch, new_base)
    result = repo.scan("--range", f"{base}..{merge}")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("allowed", [False, True])
def test_commit_message_fingerprint_and_allowlist(repo, allowed):
    base = repo.git("rev-parse", "HEAD")
    tree = repo.git("rev-parse", f"{base}^{{tree}}")
    hit = repo.graph_commit(tree, base, message="TEST\n" + RULE_CASES[0][1])
    args = ["--range", f"{base}..{hit}"]
    if allowed:
        (repo.path / "allow.txt").write_text(f"{hit}:/message:email\n", encoding="utf-8")
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    assert result.returncode == (0 if allowed else 1), result.stderr
    assert result.stdout.splitlines() == ([] if allowed else [f"{hit}:/message:email"])


@pytest.mark.parametrize("field", ["GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"])
@pytest.mark.parametrize("allowed", [False, True])
def test_commit_identity_fingerprint_and_allowlist(repo, field, allowed):
    base = repo.git("rev-parse", "HEAD")
    tree = repo.git("rev-parse", f"{base}^{{tree}}")
    value = RULE_CASES[0][1]
    identity = {field: value + "@example.invalid" if field.endswith("EMAIL") else "TEST-" + value}
    hit = repo.graph_commit(tree, base, identity=identity)
    args = ["--range", f"{base}..{hit}"]
    if allowed:
        (repo.path / "allow.txt").write_text(f"{hit}:/identity:email\n", encoding="utf-8")
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    assert result.returncode == (0 if allowed else 1), result.stderr
    assert result.stdout.splitlines() == ([] if allowed else [f"{hit}:/identity:email"])


def name_id(repo, path):
    digest = repo.git_bytes("hash-object", "--stdin", input=path.encode("utf-8")).decode("ascii").strip()
    return "/name/" + digest


@pytest.mark.parametrize("operation", ["add", "rename", "merge"])
@pytest.mark.parametrize("allowed", [False, True])
def test_file_name_is_hashed_and_allowlist_applies(repo, operation, allowed):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    path = "TEST-" + RULE_CASES[0][1] + ".txt"
    tree = repo.tree_with_file(path, b"" if operation == "add" else b"TEST-clean\n")
    if operation == "merge":
        first = repo.graph_commit(clean_tree, base, message="TEST-first")
        second = repo.graph_commit(clean_tree, base, message="TEST-second")
        hit = repo.graph_commit(tree, first, second)
    else:
        hit = repo.graph_commit(tree, base)
    identifier = name_id(repo, path)
    args = ["--range", f"{base}..{hit}"]
    if allowed:
        (repo.path / "allow.txt").write_text(f"{hit}:{identifier}:email\n", encoding="utf-8")
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    assert result.returncode == (0 if allowed else 1), result.stderr
    assert result.stdout.splitlines() == ([] if allowed else [f"{hit}:{identifier}:email"])
    assert path not in result.stdout + result.stderr
    assert RULE_CASES[0][1] not in result.stdout + result.stderr


def test_merge_file_name_uses_allowlisted_origin(repo):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    path = "TEST-" + RULE_CASES[0][1] + ".txt"
    tree = repo.tree_with_file(path)
    side = repo.graph_commit(tree, base)
    first = repo.graph_commit(clean_tree, base)
    merge = repo.graph_commit(tree, first, side)
    (repo.path / "allow.txt").write_text(f"{side}:{name_id(repo, path)}:email\n", encoding="utf-8")
    result = repo.scan("--range", f"{base}..{merge}", "--allowlist", "allow.txt")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("mode", ["tree", "range"])
def test_matching_file_name_is_redacted_even_for_content_findings(repo, mode):
    base = repo.git("rev-parse", "HEAD")
    path = "TEST-" + RULE_CASES[0][1] + ".txt"
    hit = repo.commit(RULE_CASES[4][1] + "\n", path)
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    prefix = name_id(repo, path) if mode == "tree" else f"{hit}:{name_id(repo, path)}"
    assert set(result.stdout.splitlines()) == {f"{prefix}:email", f"{prefix}:name"}
    assert path not in result.stdout + result.stderr
    assert RULE_CASES[0][1] not in result.stdout + result.stderr


def test_colon_in_path_preserves_fingerprint_and_allowlist(repo):
    base = repo.git("rev-parse", "HEAD")
    path = "TEST:sample.txt"
    tree = repo.tree_with_file(path, (RULE_CASES[0][1] + "\n").encode())
    hit = repo.graph_commit(tree, base)
    result = repo.scan("--range", f"{base}..{hit}")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{hit}:{path}:email"]
    (repo.path / "allow.txt").write_text(f"{hit}:{path}:email\n", encoding="utf-8")
    result = repo.scan("--range", f"{base}..{hit}", "--allowlist", "allow.txt")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("marker", ["OPSEC-COMMIT ", ""])
def test_forged_message_marker_cannot_reassign_findings(repo, marker):
    base = repo.git("rev-parse", "HEAD")
    value = RULE_CASES[0][1]
    tree = repo.tree_with_file("sample.txt", (value + "\n").encode())
    hit = repo.graph_commit(tree, base, message="TEST\n" + marker + base + "\n" + value)
    result = repo.scan("--range", f"{base}..{hit}")
    assert result.returncode == 1, result.stderr
    assert set(result.stdout.splitlines()) == {f"{hit}:/message:email", f"{hit}:sample.txt:email"}


@pytest.mark.parametrize("path", ['TEST-"quote.txt', "TEST-tab\t.txt", "TEST-line\n.txt"])
@pytest.mark.parametrize("operation", ["add", "modify"])
def test_quoted_path_parser_error_fails_closed(repo, path, operation):
    base = repo.git("rev-parse", "HEAD")
    if operation == "modify":
        original_tree = repo.tree_with_file(path)
        base = repo.graph_commit(original_tree, base)
    tree = repo.tree_with_file(path, b"TEST-next\n")
    head = repo.graph_commit(tree, base)
    result = repo.scan("--range", f"{base}..{head}")
    assert result.returncode == 2, result.stderr
    assert result.stdout == ""


def test_git_ceiling_prevents_discovery_of_parent_repo(repo):
    ceiling = repo.path / "TEST-ceiling"
    cwd = ceiling / "child"
    cwd.mkdir(parents=True)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES"):
        repo.env.pop(key, None)
    assert repo.scan("--tree", cwd=cwd).returncode == 0
    repo.env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    result = repo.scan("--tree", cwd=cwd)
    assert result.returncode == 2, result.stderr


def test_tree_from_subdirectory_scans_entire_repository(repo):
    repo.commit(RULE_CASES[0][1] + "\n", "other/sample.txt")
    cwd = repo.path / "TEST-subdirectory"
    cwd.mkdir()
    result = repo.scan("--tree", cwd=cwd)
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == ["other/sample.txt:email"]


def test_unreachable_before_fails_closed(repo):
    head = repo.commit("TEST-next\n")
    result = repo.scan("--range", f"{'f' * 40}..{head}")
    assert result.returncode == 2, result.stderr


def test_divergent_range_scans_new_commits(repo):
    ancestor = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{ancestor}^{{tree}}")
    before = repo.graph_commit(clean_tree, ancestor, message="TEST-old-head")
    tree = repo.tree_with_file("sample.txt", (RULE_CASES[0][1] + "\n").encode())
    hit = repo.graph_commit(tree, ancestor, message="TEST-new-head")
    head = repo.graph_commit(clean_tree, hit)
    result = repo.scan("--range", f"{before}..{head}")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{hit}:sample.txt:email"]


def test_git_failure_after_partial_log_is_not_clean(repo, tmp_path):
    base = repo.git("rev-parse", "HEAD")
    repo.commit(RULE_CASES[0][1] + "\n")
    wrapper = tmp_path / "git-wrapper"
    wrapper.mkdir()
    real_git = Path(repo.git_exe).as_posix().replace("'", "'\\''")
    (wrapper / "git").write_text(
        "#!/usr/bin/env bash\n"
        f"'{real_git}' \"$@\"\n"
        "status=$?\n"
        "for argument in \"$@\"; do\n"
        "  if [[ $argument == log ]]; then exit 42; fi\n"
        "done\n"
        "exit \"$status\"\n",
        encoding="utf-8", newline="\n",
    )
    if os.name != "nt":
        (wrapper / "git").chmod(0o700)
    repo.env["OPSEC_TEST_TOOL_DIR"] = wrapper.as_posix()
    result = repo.scan("--range", f"{base}..HEAD")
    assert result.returncode == 2, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("mode", ["tree", "range"])
def test_matching_uses_byte_locale_under_utf8_caller(repo, tmp_path, mode):
    base = repo.git("rev-parse", "HEAD")
    hit = repo.commit_bytes(b"\xff" + RULE_CASES[0][1].encode("ascii") + b"\n")
    wrapper = tmp_path / "grep-wrapper"
    wrapper.mkdir()
    grep = repo.run([repo.bash, "-c", "command -v grep"])
    assert grep.returncode == 0, grep.stderr
    grep_path = grep.stdout.strip().replace("'", "'\\''")
    (wrapper / "grep").write_text(
        "#!/usr/bin/env bash\n"
        "[[ ${LC_ALL-} == C ]] || exit 91\n"
        f"exec '{grep_path}' \"$@\"\n",
        encoding="utf-8", newline="\n",
    )
    if os.name != "nt":
        (wrapper / "grep").chmod(0o700)
    repo.env["LC_ALL"] = "C.UTF-8"
    repo.env["OPSEC_TEST_TOOL_DIR"] = wrapper.as_posix()
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    expected = "sample.txt:email" if mode == "tree" else f"{hit}:sample.txt:email"
    assert result.stdout.splitlines() == [expected]


@pytest.mark.parametrize("mode", ["tree", "range"])
@pytest.mark.parametrize("encoding,bom", [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
@pytest.mark.parametrize("codepoint", [0x161, 0x162])
@pytest.mark.parametrize("with_bom", [False, True], ids=["no-bom", "bom"])
def test_utf16_name_does_not_fold_unrelated_character_bytes(
    repo, mode, encoding, bom, codepoint, with_bom,
):
    base = repo.git("rev-parse", "HEAD")
    repo.commit_bytes((bom if with_bom else b"") + ("Micha" + chr(codepoint) + "\n").encode(encoding))
    args = ("--tree",) if mode == "tree" else ("--range", f"{base}..HEAD")
    result = repo.scan(*args)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def _run_opsec_workflow(
    repo, tmp_path, base, head, event_name="pull_request", *, tested=None, bootstrap=None,
):
    import textwrap

    workflow = (PROJECT_ROOT / ".github/workflows/opsec.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: OPSEC checks (approved policy)\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    summary = tmp_path / "summary.md"
    env = repo.env.copy()
    env.update({
        "EVENT_NAME": event_name,
        "BASE": base if event_name == "pull_request" else "TEST-unused-base",
        "BEFORE": base if event_name == "push" else "TEST-unused-before",
        "HEAD": head,
        "TESTED_SHA": tested or head,
        "RUNNER_TEMP": runner_temp.as_posix(),
        "GITHUB_STEP_SUMMARY": summary.as_posix(),
    })
    if bootstrap is not None:
        env["OPSEC_BOOTSTRAP_POLICY"] = bootstrap
    else:
        env.pop("OPSEC_BOOTSTRAP_POLICY", None)
    if "OPSEC_TEST_TOOL_DIR" in env:
        script = (
            'tool_dir=$(cd -- "$OPSEC_TEST_TOOL_DIR" && pwd) || exit 2; '
            'export PATH="$tool_dir:$PATH"; ' + script
        )
    result = repo.run([repo.bash, "--noprofile", "--norc", "-c", script], env=env)
    return result, summary.read_text(encoding="utf-8") if summary.is_file() else ""


@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_workflow_rejects_candidate_self_allowance(repo, tmp_path, event_name):
    repo.commit(SCRIPT.read_text(encoding="utf-8"), "tools/opsec_scan.sh")
    base = repo.commit("# TEST-approved policy\n", ".opsec-history-allow")
    hit = repo.commit(RULE_CASES[0][1] + "\n")
    repo.commit()
    head = repo.commit(f"{hit}:sample.txt:email\n", ".opsec-history-allow")
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head, event_name)
    assert result.returncode == 1, result.stderr
    assert f"{hit}:sample.txt:email" in result.stdout.splitlines()
    assert "::warning::OPSEC policy changed: .opsec-history-allow" in result.stdout
    assert f"Policy: approved base `{base}`" in summary
    for label, sha in (("Base", base), ("Head", head), ("Tested", head)):
        assert f"{label} SHA: `{sha}`" in summary


def test_workflow_rejects_candidate_scanner_replacement(repo, tmp_path):
    repo.commit(SCRIPT.read_text(encoding="utf-8"), "tools/opsec_scan.sh")
    base = repo.commit("# TEST-approved policy\n", ".opsec-history-allow")
    hit = repo.commit(RULE_CASES[0][1] + "\n")
    repo.commit()
    head = repo.commit("#!/usr/bin/env bash\nexit 0\n", "tools/opsec_scan.sh")
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head)
    assert result.returncode == 1, result.stderr
    assert f"{hit}:sample.txt:email" in result.stdout.splitlines()
    assert "::warning::OPSEC policy changed: tools/opsec_scan.sh" in result.stdout
    assert f"Policy: approved base `{base}`" in summary


def test_workflow_bootstrap_uses_candidate_policy_with_warning(repo, tmp_path):
    base = repo.git("rev-parse", "HEAD")
    repo.commit(SCRIPT.read_text(encoding="utf-8"), "tools/opsec_scan.sh")
    hit = repo.commit(RULE_CASES[0][1] + "\n")
    repo.commit()
    head = repo.commit(f"{hit}:sample.txt:email\n", ".opsec-history-allow")
    bootstrap = ":".join(
        repo.git("rev-parse", f"{head}:{path}")
        for path in ("tools/opsec_scan.sh", ".opsec-history-allow")
    )
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head, bootstrap=bootstrap)
    assert result.returncode == 0, result.stderr
    assert "::warning::OPSEC bootstrap: using candidate policy" in result.stdout
    assert f"Policy: candidate `{head}` (bootstrap)" in summary


def test_workflow_unavailable_start_fails_closed(repo, tmp_path):
    head = repo.git("rev-parse", "HEAD")
    result, _ = _run_opsec_workflow(repo, tmp_path, "1" * 40, head)
    assert result.returncode == 2, result.stderr
    assert "OPSEC bootstrap" not in result.stdout


def _install_opsec_policy(repo):
    repo.commit(SCRIPT.read_text(encoding="utf-8"), "tools/opsec_scan.sh")
    return repo.commit("# TEST-policy\n", ".opsec-history-allow")


def _opsec_policy_blobs(repo, ref="HEAD"):
    return ":".join(repo.git("rev-parse", f"{ref}:{path}") for path in (
        "tools/opsec_scan.sh", ".opsec-history-allow",
    ))


@pytest.mark.parametrize("approval", ["missing", "wrong", "exact"])
def test_bootstrap_requires_exact_policy_approval(repo, tmp_path, approval):
    base = repo.git("rev-parse", "HEAD")
    head = _install_opsec_policy(repo)
    blobs = _opsec_policy_blobs(repo)
    value = {"missing": None, "wrong": "0" * 40 + ":" + "0" * 40, "exact": blobs}[approval]
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head, bootstrap=value)
    assert result.returncode == (0 if approval == "exact" else 2), result.stderr
    if approval == "exact":
        assert f"Policy: candidate `{head}` (bootstrap)" in summary
        scanner, allow = blobs.split(":")
        assert f"Scanner blob: `{scanner}`" in summary
        assert f"Allowlist blob: `{allow}`" in summary


@pytest.mark.parametrize("only", ["tools/opsec_scan.sh", ".opsec-history-allow"])
def test_partial_base_policy_fails_closed(repo, tmp_path, only):
    content = SCRIPT.read_text(encoding="utf-8") if only.startswith("tools/") else "# TEST-policy\n"
    base = repo.commit(content, only)
    if only == "tools/opsec_scan.sh":
        head = repo.commit("# TEST-policy\n", ".opsec-history-allow")
    else:
        head = repo.commit(SCRIPT.read_text(encoding="utf-8"), "tools/opsec_scan.sh")
    result, _ = _run_opsec_workflow(repo, tmp_path, base, head, bootstrap=_opsec_policy_blobs(repo))
    assert result.returncode == 2, result.stderr


@pytest.mark.parametrize("dirty", ["tracked", "untracked", "staged"])
def test_workflow_rejects_dirty_tree(repo, tmp_path, dirty):
    base = _install_opsec_policy(repo)
    head = repo.commit("TEST-next\n")
    path = "unexpected.txt" if dirty == "untracked" else "sample.txt"
    (repo.path / path).write_text("TEST-dirty\n", encoding="utf-8")
    if dirty == "staged":
        repo.git("add", "--", path)
    result, _ = _run_opsec_workflow(repo, tmp_path, base, head)
    assert result.returncode == 2, result.stderr


def test_workflow_rejects_wrong_tested_head(repo, tmp_path):
    base = _install_opsec_policy(repo)
    head = repo.commit("TEST-next\n")
    result, _ = _run_opsec_workflow(repo, tmp_path, base, head, tested=base)
    assert result.returncode == 2, result.stderr


def test_policy_warning_redacts_workflow_name(repo, tmp_path):
    base = _install_opsec_policy(repo)
    path = ".github/workflows/TEST-" + RULE_CASES[0][1] + ".yml"
    head = repo.commit("# TEST-workflow\n", path)
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head)
    assert result.returncode == 1, result.stderr
    assert path not in result.stdout + result.stderr + summary
    assert RULE_CASES[0][1] not in result.stdout + result.stderr + summary
    assert f"::warning::OPSEC policy changed: {name_id(repo, path)}" in result.stdout


def test_approved_base_summary_uses_actual_blobs(repo, tmp_path):
    base = _install_opsec_policy(repo)
    head = repo.commit("#!/usr/bin/env bash\nexit 0\n", "tools/opsec_scan.sh")
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head)
    assert result.returncode == 0, result.stderr
    scanner, allow = _opsec_policy_blobs(repo, base).split(":")
    assert f"Policy: approved base `{base}`" in summary
    assert f"Scanner blob: `{scanner}`" in summary
    assert f"Allowlist blob: `{allow}`" in summary
    for label, sha in (("Base", base), ("Head", head), ("Tested", head)):
        assert f"{label} SHA: `{sha}`" in summary


def test_workflow_edited_is_unconditional_and_bootstrap_is_explicit():
    workflow = (PROJECT_ROOT / ".github/workflows/opsec.yml").read_text(encoding="utf-8")
    assert "types: [opened, synchronize, reopened, edited]" in workflow
    assert "    if:" not in workflow
    assert "OPSEC_BOOTSTRAP_POLICY: ${{ vars.OPSEC_BOOTSTRAP_POLICY }}" in workflow


@pytest.mark.parametrize("command", ["ls-tree", "show", "hash-object", "status"])
def test_workflow_policy_read_failure_is_not_missing_policy(repo, tmp_path, command):
    base = _install_opsec_policy(repo)
    head = repo.commit("TEST-next\n")
    wrapper = tmp_path / "git-wrapper"
    wrapper.mkdir()
    real_git = Path(repo.git_exe).as_posix().replace("'", "'\\''")
    (wrapper / "git").write_text(
        "#!/usr/bin/env bash\n"
        f"for argument in \"$@\"; do [[ $argument != '{command}' ]] || exit 42; done\n"
        f"exec '{real_git}' \"$@\"\n", encoding="utf-8", newline="\n",
    )
    if os.name != "nt":
        (wrapper / "git").chmod(0o700)
    repo.env["OPSEC_TEST_TOOL_DIR"] = wrapper.as_posix()
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head)
    assert result.returncode == 2, result.stderr
    assert "OPSEC bootstrap" not in result.stdout
    assert summary == ""


@pytest.mark.parametrize("only", ["tools/opsec_scan.sh", ".opsec-history-allow"])
def test_workflow_missing_candidate_policy_fails_closed(repo, tmp_path, only):
    base = repo.git("rev-parse", "HEAD")
    content = SCRIPT.read_text(encoding="utf-8") if only.startswith("tools/") else "# TEST-policy\n"
    head = repo.commit(content, only)
    result, _ = _run_opsec_workflow(repo, tmp_path, base, head, bootstrap="0" * 40 + ":" + "0" * 40)
    assert result.returncode == 2, result.stderr
    assert "OPSEC bootstrap" not in result.stdout


def test_workflow_checks_tested_merge_tree_with_candidate_head_range(repo, tmp_path):
    base = _install_opsec_policy(repo)
    head = repo.commit("TEST-next\n")
    tree = repo.git("rev-parse", f"{head}^{{tree}}")
    tested = repo.graph_commit(tree, base, head)
    repo.git("update-ref", "HEAD", tested)
    result, summary = _run_opsec_workflow(repo, tmp_path, base, head, tested=tested)
    assert result.returncode == 0, result.stderr
    assert f"Head SHA: `{head}`" in summary
    assert f"Tested SHA: `{tested}`" in summary


def _merge_fixture(repo, kind):
    path = "sample.txt" if kind == "line" else "TEST-" + RULE_CASES[0][1] + ".txt"
    content = (RULE_CASES[0][1] + "\n").encode() if kind == "line" else b"TEST-clean\n"
    return path, repo.tree_with_file(path, content)


def _scan_merge_fixture(repo, base, head, allowed, origin, path):
    identifier = path if path == "sample.txt" else name_id(repo, path)
    args = ["--range", f"{base}..{head}"]
    if allowed:
        (repo.path / "allow.txt").write_text(f"{origin}:{identifier}:email\n", encoding="utf-8")
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    return result, identifier


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_merge_readdition_after_shared_removal_reports_merge(repo, kind, allowed):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    path, hit_tree = _merge_fixture(repo, kind)
    origin = repo.graph_commit(hit_tree, base)
    removed = repo.graph_commit(clean_tree, origin)
    first = repo.graph_commit(clean_tree, removed, message="TEST-first")
    second = repo.graph_commit(clean_tree, removed, message="TEST-second")
    merge = repo.graph_commit(hit_tree, first, second)
    head = repo.graph_commit(clean_tree, merge)
    result, identifier = _scan_merge_fixture(repo, base, head, allowed, origin, path)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_merge_restored_before_range_reports_merge(repo, kind, allowed):
    initial = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{initial}^{{tree}}")
    path, hit_tree = _merge_fixture(repo, kind)
    base = repo.graph_commit(hit_tree, initial)
    first = repo.graph_commit(clean_tree, base, message="TEST-first")
    second = repo.graph_commit(hit_tree, base, message="TEST-second")
    merge = repo.graph_commit(hit_tree, first, second)
    head = repo.graph_commit(clean_tree, merge)
    result, identifier = _scan_merge_fixture(repo, base, head, allowed, base, path)
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"{merge}:{identifier}:email"]


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_clean_merge_reuses_secondary_parent_origin(repo, kind, allowed):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    path, hit_tree = _merge_fixture(repo, kind)
    origin = repo.graph_commit(hit_tree, base)
    first = repo.graph_commit(clean_tree, base)
    merge = repo.graph_commit(hit_tree, first, origin)
    head = repo.graph_commit(clean_tree, merge)
    result, identifier = _scan_merge_fixture(repo, base, head, allowed, origin, path)
    assert result.returncode == (0 if allowed else 1), result.stderr
    assert result.stdout.splitlines() == ([] if allowed else [f"{origin}:{identifier}:email"])


@pytest.mark.parametrize("allowed", [False, True])
def test_automatic_merge_same_file_reuses_secondary_parent_line(repo, allowed):
    initial = repo.git("rev-parse", "HEAD")
    base_tree = repo.tree_with_file("sample.txt", b"TEST-first\nTEST-middle\nTEST-last\n")
    base = repo.graph_commit(base_tree, initial)
    value = RULE_CASES[0][1].encode()
    first_tree = repo.tree_with_file("sample.txt", b"TEST-updated\nTEST-middle\nTEST-last\n")
    side_tree = repo.tree_with_file("sample.txt", b"TEST-first\nTEST-middle\n" + value + b"\n")
    first = repo.graph_commit(first_tree, base)
    side = repo.graph_commit(side_tree, base)
    merged_tree = repo.git("merge-tree", "--write-tree", first, side).splitlines()[0]
    merge = repo.graph_commit(merged_tree, first, side)
    result, identifier = _scan_merge_fixture(repo, base, merge, allowed, side, "sample.txt")
    assert result.returncode == (0 if allowed else 1), result.stderr
    assert result.stdout.splitlines() == ([] if allowed else [f"{side}:{identifier}:email"])


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_merge_origin_must_be_reachable_from_matching_parent(repo, kind, allowed):
    initial = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{initial}^{{tree}}")
    path, hit_tree = _merge_fixture(repo, kind)
    base = repo.graph_commit(hit_tree, initial)
    removed = repo.graph_commit(clean_tree, base)
    origin = repo.graph_commit(hit_tree, removed)
    first = repo.graph_commit(clean_tree, origin)
    second = repo.graph_commit(hit_tree, base, message="TEST-second")
    merge = repo.graph_commit(hit_tree, first, second)
    result, identifier = _scan_merge_fixture(repo, base, merge, allowed, origin, path)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("prefix", [b"\0", b"\xff", b"\t"], ids=["nul", "nonutf8", "tab"])
@pytest.mark.parametrize("allowed", [False, True])
def test_merge_provenance_compares_raw_bytes_before_normalizing(repo, allowed, prefix):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    value = RULE_CASES[0][1].encode()
    origin_tree = repo.tree_with_file("sample.txt", value + b"\n")
    parent_tree = repo.tree_with_file("sample.txt", prefix + value + b"\n")
    origin = repo.graph_commit(origin_tree, base)
    side = repo.graph_commit(parent_tree, origin)
    first = repo.graph_commit(clean_tree, base)
    merge = repo.graph_commit(origin_tree, first, side)
    identifier = "sample.txt"
    args = ["--range", f"{base}..{merge}"]
    if allowed:
        (repo.path / "allow.txt").write_text(
            f"{origin}:{identifier}:email\n{side}:{identifier}:email\n", encoding="utf-8",
        )
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.update({f"{origin}:{identifier}:email", f"{side}:{identifier}:email"})
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("prefix", [b"", b"\0", b"\xff", b"\t"], ids=["plain", "nul", "nonutf8", "tab"])
def test_merge_extra_copy_reports_merge(repo, allowed, prefix):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    line = prefix + RULE_CASES[0][1].encode() + b"\n"
    origin = repo.graph_commit(repo.tree_with_file("sample.txt", line), base)
    first = repo.graph_commit(clean_tree, base)
    tree = repo.tree_with_file("sample.txt", line + b"TEST-new-context\n" + line)
    merge = repo.graph_commit(tree, first, origin)
    head = repo.graph_commit(clean_tree, merge)
    result, identifier = _scan_merge_fixture(repo, base, head, allowed, origin, "sample.txt")
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("allowed", [False, True])
def test_merge_duplicates_shared_copy_reports_merge(repo, allowed):
    base = repo.git("rev-parse", "HEAD")
    line = RULE_CASES[0][1].encode() + b"\n"
    origin = repo.graph_commit(repo.tree_with_file("sample.txt", line), base)
    first = repo.graph_commit(repo.tree_with_file("sample.txt", line + b"TEST-first\n"), origin)
    second = repo.graph_commit(repo.tree_with_file("sample.txt", line + b"TEST-second\n"), origin)
    tree = repo.tree_with_file("sample.txt", line + b"TEST-first\nTEST-second\n" + line)
    merge = repo.graph_commit(tree, first, second)
    result, identifier = _scan_merge_fixture(repo, base, merge, allowed, origin, "sample.txt")
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_merge_restores_shared_origin_reports_merge(repo, kind, allowed):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    path, hit_tree = _merge_fixture(repo, kind)
    origin = repo.graph_commit(hit_tree, base)
    first = repo.graph_commit(clean_tree, origin, message="TEST-first-removal")
    second = repo.graph_commit(hit_tree, origin, message="TEST-second-keeps-copy")
    merge = repo.graph_commit(hit_tree, first, second)
    result, identifier = _scan_merge_fixture(repo, base, merge, allowed, origin, path)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("allowed", [False, True])
def test_merge_preserves_two_independent_copies(repo, allowed):
    initial = repo.git("rev-parse", "HEAD")
    base_tree = repo.tree_with_file("sample.txt", b"TEST-first\nTEST-middle\nTEST-last\n")
    base = repo.graph_commit(base_tree, initial)
    line = RULE_CASES[0][1].encode() + b"\n"
    first = repo.graph_commit(
        repo.tree_with_file("sample.txt", line + b"TEST-middle\nTEST-last\n"), base,
    )
    second = repo.graph_commit(
        repo.tree_with_file("sample.txt", b"TEST-first\nTEST-middle\n" + line), base,
    )
    tree = repo.git("merge-tree", "--write-tree", first, second).splitlines()[0]
    merge = repo.graph_commit(tree, first, second)
    args = ["--range", f"{base}..{merge}"]
    origins = {f"{first}:sample.txt:email", f"{second}:sample.txt:email"}
    if allowed:
        (repo.path / "allow.txt").write_text("\n".join(sorted(origins)) + "\n", encoding="utf-8")
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    assert result.returncode == (0 if allowed else 1), result.stderr
    assert set(result.stdout.splitlines()) == (set() if allowed else origins)


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_criss_cross_merge_uses_all_bases(repo, kind, allowed):
    base = repo.git("rev-parse", "HEAD")
    value = RULE_CASES[0][1]
    paths = ["one.txt", "two.txt"] if kind == "line" else [f"TEST-{value}-{i}.txt" for i in (1, 2)]
    content = (value + "\n").encode() if kind == "line" else b"TEST-clean\n"
    blob = repo.git_bytes("hash-object", "-w", "--stdin", input=content).strip()
    records = [b"100644 blob " + blob + b"\t" + path.encode() + b"\0" for path in paths]
    trees = [repo.git_bytes("mktree", "-z", input=record).decode().strip() for record in records]
    both = repo.git_bytes("mktree", "-z", input=b"".join(records)).decode().strip()
    empty = repo.git_bytes("mktree", "-z", input=b"").decode().strip()
    origins = [repo.graph_commit(tree, base) for tree in trees]
    first = repo.graph_commit(empty, *origins)
    second = repo.graph_commit(both, *reversed(origins))
    assert set(repo.git("merge-base", "--all", first, second).splitlines()) == set(origins)
    merge = repo.graph_commit(both, first, second)
    identifiers = paths if kind == "line" else [name_id(repo, path) for path in paths]
    original_hits = {f"{sha}:{path}:email" for sha, path in zip(origins, identifiers, strict=True)}
    args = ["--range", f"{base}..{merge}"]
    if allowed:
        (repo.path / "allow.txt").write_text("\n".join(sorted(original_hits)) + "\n", encoding="utf-8")
        args.extend(["--allowlist", "allow.txt"])
    result = repo.scan(*args)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{path}:email" for path in identifiers}
    if not allowed:
        expected.update(original_hits)
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("kind", ["line", "name"])
@pytest.mark.parametrize("allowed", [False, True])
def test_merge_secondary_is_itself_the_base(repo, kind, allowed):
    base = repo.git("rev-parse", "HEAD")
    clean_tree = repo.git("rev-parse", f"{base}^{{tree}}")
    path, hit_tree = _merge_fixture(repo, kind)
    origin = repo.graph_commit(hit_tree, base)
    first = repo.graph_commit(clean_tree, origin)
    merge = repo.graph_commit(hit_tree, first, origin)
    result, identifier = _scan_merge_fixture(repo, base, merge, allowed, origin, path)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.parametrize("allowed", [False, True])
def test_merge_name_present_as_directory_in_base(repo, allowed):
    initial = repo.git("rev-parse", "HEAD")
    path, hit_tree = _merge_fixture(repo, "name")
    child_tree = repo.tree_with_file("child.txt")
    entry = f"040000 tree {child_tree}\t{path}\0".encode()
    base_tree = repo.git_bytes("mktree", "-z", input=entry).decode().strip()
    base = repo.graph_commit(base_tree, initial)
    empty = repo.git_bytes("mktree", "-z", input=b"").decode().strip()
    first = repo.graph_commit(empty, base)
    origin = repo.graph_commit(hit_tree, base)
    merge = repo.graph_commit(hit_tree, first, origin)
    result, identifier = _scan_merge_fixture(repo, base, merge, allowed, origin, path)
    assert result.returncode == 1, result.stderr
    expected = {f"{merge}:{identifier}:email"}
    if not allowed:
        expected.add(f"{origin}:{identifier}:email")
    assert set(result.stdout.splitlines()) == expected
