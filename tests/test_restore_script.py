"""Exercise backup and restore against synthetic volumes and a fake Docker CLI."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT
from tests.support import minimal_subprocess_env

DOCKER_HELPER = r'''
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

root = Path(os.environ["TEST_ROOT"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
state_file = root / "running.json"
running = json.loads(state_file.read_text(encoding="utf-8"))
mode = os.environ.get("TEST_DOCKER_MODE", "")
assert args[0] == "compose", args
command = args[1]
if command == "ps":
    if args[-2:] != ["api", "web"] and mode == "initial-status-error":
        sys.exit(42)
    if args[-2:] == ["api", "web"] and mode == "status-error":
        sys.exit(42)
    for service in running:
        print(service)
elif command == "stop":
    if args[2:] == ["web", "api"] and mode == "stop-error":
        sys.exit(42)
    if mode != "still-running":
        running = [service for service in running if service not in args[2:]]
        state_file.write_text(json.dumps(running), encoding="utf-8")
elif command == "start":
    state_file.write_text(json.dumps(sorted(set(running + args[2:]))), encoding="utf-8")
elif command == "up":
    state_file.write_text(json.dumps(["api", "web"]), encoding="utf-8")
elif command == "run":
    entrypoint = args[args.index("--entrypoint") + 1]
    volumes = root / "volumes"
    if entrypoint == "tar":
        if mode == "backup-error":
            sys.exit(42)
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            archive.add(volumes / "control", arcname="control")
            archive.add(volumes / "runtime", arcname="runtime")
    elif entrypoint == "sh":
        (root / "running-at-extraction.json").write_text(json.dumps(running), encoding="utf-8")
        for name in ("control", "runtime"):
            shutil.rmtree(volumes / name)
        with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()), mode="r:gz") as archive:
            archive.extractall(volumes, filter="data")
    elif entrypoint == "python":
        source = sys.stdin.read().replace(
            "/data/control/control.db", (volumes / "control" / "control.db").as_posix()
        )
        result = subprocess.run([sys.executable, "-c", source], check=False)
        sys.exit(result.returncode)
    else:
        raise AssertionError(args)
else:
    raise AssertionError(args)
'''


class Deployment:
    def __init__(self, root: Path, bash: str):
        self.root = root
        self.bash = bash
        self.deploy = root / "deploy"
        self.tools = root / "bin"
        self.volumes = root / "volumes"
        self.backups = root / "backups"
        for directory in (self.deploy, self.tools, self.backups):
            directory.mkdir()
        for name in ("control", "runtime"):
            (self.volumes / name).mkdir(parents=True)
        (self.volumes / "runtime" / "current.txt").write_text("TEST-current", encoding="utf-8")
        with closing(sqlite3.connect(self.volumes / "control" / "control.db")) as connection, connection:
            connection.execute("CREATE TABLE test_current (value INTEGER)")
        (root / "running.json").write_text('["api", "web"]', encoding="utf-8")
        for script in ("backup.sh", "restore.sh"):
            shutil.copyfile(PROJECT_ROOT / "deploy" / script, self.deploy / script)
            (self.deploy / script).chmod(0o700)
        (root / "docker_helper.py").write_text(DOCKER_HELPER, encoding="utf-8")
        wrappers = {
            "docker": '#!/usr/bin/env bash\nexec "$TEST_PYTHON" "$TEST_HELPER" "$@"\n',
            "date": '#!/usr/bin/env bash\nprintf "%s\\n" "20261018T000000Z"\n',
        }
        for name, source in wrappers.items():
            (self.tools / name).write_text(source, encoding="utf-8", newline="\n")
            (self.tools / name).chmod(0o700)

    def archive(self, kind="healthy"):
        source = self.root / "archive-source"
        (source / "control").mkdir(parents=True)
        (source / "runtime").mkdir()
        (source / "runtime" / "restored.txt").write_text("TEST-restored", encoding="utf-8")
        database = source / "control" / "control.db"
        if kind == "corrupt":
            database.write_bytes(b"TEST-not-a-database")
        elif kind != "missing":
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
                connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
                if kind == "foreign-key":
                    connection.execute("INSERT INTO child VALUES (123)")
        archive_path = self.root / "restore.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(source / "control", arcname="control")
            archive.add(source / "runtime", arcname="runtime")
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        archive_path.with_suffix(".gz.sha256").write_text(
            f"{digest}  {archive_path.name}\n", encoding="ascii", newline="\n"
        )
        return archive_path

    def run(self, script="restore.sh", *options, mode="", archive=None, keep="14"):
        env = minimal_subprocess_env(
            self.root,
            TMPDIR=self.root.as_posix(),
            TEST_ROOT=self.root.as_posix(),
            TEST_HELPER=(self.root / "docker_helper.py").as_posix(),
            TEST_PYTHON=Path(sys.executable).as_posix(),
            TEST_TOOL_DIR=self.tools.as_posix(),
            TEST_DOCKER_MODE=mode,
            BACKUP_DIR=self.backups.as_posix(),
            KEEP=keep,
            MSYS2_ARG_CONV_EXCL="*",
        )
        arguments = [str(self.deploy / script)]
        if script == "restore.sh":
            arguments.extend([str(archive or self.archive()), "--yes"])
        arguments.extend(options)
        return subprocess.run(  # noqa: S603 -- fixed local scripts, isolated fake Docker only
            [
                self.bash, "--noprofile", "--norc", "-c",
                'tool_dir=$(cd -- "$TEST_TOOL_DIR" && pwd) || exit 2; '
                'export PATH="$tool_dir:$PATH"; exec bash "$@"',
                "TEST-deployment", *arguments,
            ],
            cwd=self.root, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, check=False,
        )

    def calls(self):
        log = self.root / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def destructive_calls(self):
        return [call for call in self.calls() if "--entrypoint" in call and "sh" in call]

    def up_calls(self):
        return [call for call in self.calls() if call[:2] == ["compose", "up"]]


@pytest.fixture
def deployment(tmp_path):
    bash = shutil.which("bash")
    git = shutil.which("git")
    if git and os.name == "nt":
        git_bash = Path(git).parent.parent / "bin" / "bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    if not bash:
        pytest.skip("Deployment scripts require Bash")
    return Deployment(tmp_path, bash)


@pytest.mark.parametrize("mode", ["backup-error", "initial-status-error"])
def test_restore_pre_backup_failure_preserves_existing_data(deployment, mode):
    result = deployment.run(mode=mode)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not deployment.destructive_calls()
    assert not deployment.up_calls()
    assert (deployment.volumes / "runtime" / "current.txt").read_text() == "TEST-current"


def test_backup_initial_status_error_does_not_archive(deployment):
    result = deployment.run("backup.sh", "--cold", mode="initial-status-error")
    assert result.returncode == 1, result.stdout + result.stderr
    assert not any("tar" in call for call in deployment.calls())
    assert list(deployment.backups.iterdir()) == []


def test_restore_explicit_no_pre_backup_warns_conservative_reset(deployment):
    result = deployment.run("restore.sh", "--no-pre-backup", "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--revoke-all" in result.stdout + result.stderr
    assert "restore-quarantine" in result.stdout + result.stderr
    assert not any("tar" in call for call in deployment.calls())
    assert (deployment.volumes / "runtime" / "restored.txt").is_file()


@pytest.mark.parametrize("mode", ["stop-error", "still-running", "status-error"])
def test_restore_requires_verified_stopped_services(deployment, mode):
    result = deployment.run(mode=mode)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not deployment.destructive_calls()
    assert not deployment.up_calls()
    assert (deployment.volumes / "runtime" / "current.txt").is_file()


def test_restore_no_start_checks_database_and_prints_next_steps(deployment):
    result = deployment.run("restore.sh", "--no-start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not deployment.up_calls()
    assert any("python" in call for call in deployment.calls())
    assert "DAAS_MAINTENANCE_MODE=true" in result.stdout
    assert "restore-quarantine" in result.stdout
    assert "docker compose stop api" in result.stdout
    assert "docker compose run --rm --no-deps -T api python -m app.cli restore-quarantine" in result.stdout
    assert "BACKUP_ARCHIVE=" in result.stdout
    assert str(next(deployment.backups.glob("*.tar.gz")).name) in result.stdout


@pytest.mark.parametrize("kind", ["corrupt", "foreign-key", "missing"])
def test_restore_database_check_failure_does_not_start(deployment, kind):
    result = deployment.run(archive=deployment.archive(kind))
    assert result.returncode == 1, result.stdout + result.stderr
    assert not deployment.up_calls()
    assert any("python" in call for call in deployment.calls())


def test_restore_healthy_default_starts_only_after_database_check(deployment):
    result = deployment.run()
    assert result.returncode == 0, result.stdout + result.stderr
    calls = deployment.calls()
    check_index = next(index for index, call in enumerate(calls) if "python" in call)
    start_index = next(index for index, call in enumerate(calls) if call[:2] == ["compose", "up"])
    assert check_index < start_index
    assert "--no-deps" in calls[check_index]
    assert json.loads((deployment.root / "running-at-extraction.json").read_text()) == []


def test_backups_in_same_second_never_overwrite_previous_archive(deployment):
    first = deployment.run("backup.sh", "--cold")
    assert first.returncode == 0, first.stdout + first.stderr
    archive = next(deployment.backups.glob("*.tar.gz"))
    previous = {path.name: path.read_bytes() for path in deployment.backups.iterdir()}
    (deployment.volumes / "runtime" / "current.txt").write_text("TEST-changed", encoding="utf-8")
    second = deployment.run("backup.sh", "--cold")
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(list(deployment.backups.glob("*.tar.gz"))) == 2
    assert {name: (deployment.backups / name).read_bytes() for name in previous} == previous
    assert archive.name in first.stdout
    assert "BACKUP_ARCHIVE=" in first.stdout


def test_backup_publication_collision_preserves_existing_archive(deployment):
    archive = deployment.backups / "daas-20261018T000000Z.tar.gz"
    archive.write_bytes(b"TEST-existing-backup")
    wrapper = deployment.tools / "mktemp"
    wrapper.write_text(
        '#!/usr/bin/env bash\n'
        'partial="$BACKUP_DIR/daas-20261018T000000Z"\n'
        ': >"$partial"\n'
        'printf "%s\\n" "$partial"\n',
        encoding="utf-8", newline="\n",
    )
    wrapper.chmod(0o700)
    result = deployment.run("backup.sh", "--cold")
    assert result.returncode == 1, result.stdout + result.stderr
    assert archive.read_bytes() == b"TEST-existing-backup"


@pytest.mark.parametrize("same_mtime", [False, True])
def test_backup_retention_keeps_latest_with_same_second_unique_suffix(deployment, same_mtime):
    wrapper = deployment.tools / "mktemp"
    if same_mtime:
        link_wrapper = deployment.tools / "ln"
        link_wrapper.write_text(
            '#!/usr/bin/env bash\n'
            'command -p ln "$@" || exit 1\n'
            'touch -t 202601010000.00 "$BACKUP_DIR/"*.tar.gz\n',
            encoding="utf-8", newline="\n",
        )
        link_wrapper.chmod(0o700)

    def reserve(suffix):
        wrapper.write_text(
            '#!/usr/bin/env bash\n'
            f'partial="$BACKUP_DIR/daas-20261018T000000Z-{suffix}"\n'
            ': >"$partial"\n'
            'printf "%s\\n" "$partial"\n',
            encoding="utf-8", newline="\n",
        )
        wrapper.chmod(0o700)

    reserve("ZZZZZZZZZZ")
    first = deployment.run("backup.sh", "--cold", keep="1")
    assert first.returncode == 0, first.stdout + first.stderr
    older = next(deployment.backups.glob("*.tar.gz"))
    os.utime(older, (1, 1))
    reserve("AAAAAAAAAA")
    second = deployment.run("backup.sh", "--cold", keep="1")
    assert second.returncode == 0, second.stdout + second.stderr
    assert not older.exists()
    current = list(deployment.backups.glob("*.tar.gz"))
    assert len(current) == 1
    assert current[0].name in second.stdout
    assert current[0].with_suffix(".gz.sha256").is_file()


def test_restore_preserves_input_archive_during_pre_backup_retention(deployment):
    original = deployment.archive()
    source = deployment.backups / "pre-restore-20260101T000000Z.tar.gz"
    shutil.copyfile(original, source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    checksum = source.with_suffix(".gz.sha256")
    checksum.write_text(f"{digest}  {source.name}\n", encoding="ascii", newline="\n")
    result = deployment.run(archive=source, keep="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() == original.read_bytes()
    assert checksum.is_file()
    assert len(list(deployment.backups.glob("*.tar.gz"))) == 2


def test_restore_preserves_symlink_target_during_pre_backup_retention(deployment):
    original = deployment.archive()
    source = deployment.backups / "pre-restore-20260101T000000Z.tar.gz"
    shutil.copyfile(original, source)
    alias = deployment.root / "restore-alias.tar.gz"
    try:
        alias.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"Creating real symbolic links is unavailable: {type(exc).__name__}")
    assert alias.is_symlink()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    alias.with_suffix(".gz.sha256").write_text(
        f"{digest}  {alias.name}\n", encoding="ascii", newline="\n"
    )
    result = deployment.run(archive=alias, keep="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() == original.read_bytes()
    assert alias.is_file()
    assert len(list(deployment.backups.glob("*.tar.gz"))) == 2
