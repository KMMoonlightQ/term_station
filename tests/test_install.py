import os
from pathlib import Path
import subprocess


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def install_fixture(tmp_path, shell):
    home = tmp_path / "station's home"
    home.mkdir()
    artifact = tmp_path / "term-station"
    artifact.write_text("#!/bin/sh\nprintf '%s\\n' 'term-station test'\n")
    artifact.chmod(0o755)
    environment = {**os.environ, "TERM_STATION_INSTALL_HOME": str(home), "SHELL": shell,
                   "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    environment.pop("ZDOTDIR", None)
    return home, artifact, environment


def test_zsh_install_preserves_config_and_deduplicates_path(tmp_path):
    home, artifact, environment = install_fixture(tmp_path, "/bin/zsh")
    config = tmp_path / "zsh config"
    config.mkdir()
    environment["ZDOTDIR"] = str(config)
    original = "export EXISTING='keep me'"  # No trailing newline.
    for name in (".zprofile", ".zshrc"):
        (config / name).write_text(original)
        (config / name).chmod(0o600)
    subprocess.run([str(INSTALLER), str(artifact)], env=environment, check=True, capture_output=True)
    first = {name: (config / name).read_bytes() for name in (".zprofile", ".zshrc")}
    subprocess.run([str(INSTALLER), str(artifact)], env=environment, check=True, capture_output=True)
    for name in first:
        path = config / name
        assert path.read_bytes() == first[name]
        assert path.stat().st_mode & 0o777 == 0o600
        backups = list(config.glob(name + ".term-station-backup.*"))
        assert len(backups) == 1
        assert backups[0].read_text() == original
    result = subprocess.run(
        ["/bin/zsh", "-dfc", 'source "$1/.zprofile"; source "$1/.zshrc"; source "$1/.zshrc"; '
         'command -v term-station; term-station --version; print -r -- "$EXISTING"; print -r -- "$PATH"',
         "zsh", str(config)], env=environment, capture_output=True, text=True, check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[:3] == [str(home / ".local/bin/term-station"), "term-station test", "keep me"]
    assert lines[-1].split(":").count(str(home / ".local/bin")) == 1
    assert (home / ".local/bin/term-station").read_bytes() == artifact.read_bytes()
    assert not list((home / ".local/bin").glob(".term-station.*"))


def test_bash_updates_the_existing_login_profile(tmp_path):
    home, artifact, environment = install_fixture(tmp_path, "/bin/bash")
    (home / ".bash_profile").write_text("export BASH_EXISTING=kept\n")
    (home / ".profile").write_text("# unused profile\n")
    subprocess.run([str(INSTALLER), str(artifact)], env=environment, check=True, capture_output=True)
    assert (home / ".profile").read_text() == "# unused profile\n"
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c",
         'source "$1/.bash_profile"; source "$1/.bashrc"; command -v term-station; '
         'term-station --version; printf "%s\\n" "$BASH_EXISTING"', "bash", str(home)],
        env=environment, capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == [str(home / ".local/bin/term-station"), "term-station test", "kept"]


def test_failed_artifact_does_not_change_installation_or_config(tmp_path):
    home, artifact, environment = install_fixture(tmp_path, "/bin/zsh")
    original = "# existing config\n"
    (home / ".zshrc").write_text(original)
    artifact.write_text("#!/bin/sh\nexit 1\n")
    result = subprocess.run([str(INSTALLER), str(artifact)], env=environment, capture_output=True)
    assert result.returncode != 0
    assert (home / ".zshrc").read_text() == original
    assert not (home / ".local/bin").exists()


def test_directory_install_relocates_dependencies_and_preserves_previous_runtime(tmp_path):
    import shutil

    home, _, environment = install_fixture(tmp_path, "/bin/zsh")
    bundle = tmp_path / "bundle with spaces"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (internal / "value.txt").write_text("version one\n")
    (internal / "current.txt").symlink_to("value.txt")
    binary = bundle / "term-station"
    binary.write_text(
        '#!/bin/sh\nset -eu\nexe=$0\n'
        'if [ -L "$exe" ]; then exe=$(readlink "$exe"); fi\n'
        'cat "$(dirname -- "$exe")/_internal/current.txt"\n'
    )
    binary.chmod(0o755)
    subprocess.run([str(INSTALLER), str(bundle)], env=environment, check=True, capture_output=True)
    command = home / ".local/bin/term-station"
    old_runtime = command.resolve().parent
    assert command.is_symlink()
    assert old_runtime != bundle
    assert (old_runtime / "_internal/current.txt").is_symlink()

    (internal / "value.txt").write_text("version two\n")
    # Accept both the directory and the executable within that directory.
    subprocess.run([str(INSTALLER), str(binary)], env=environment, check=True, capture_output=True)
    assert command.resolve().parent != old_runtime
    shutil.rmtree(bundle)
    result = subprocess.run([str(command), "--version"], env=environment, capture_output=True, text=True, check=True)
    assert result.stdout == "version two\n"
    # An old detached daemon must retain its original dependencies after upgrade.
    assert (old_runtime / "_internal/current.txt").read_text() == "version one\n"
    result = subprocess.run([str(old_runtime / "term-station")], env=environment, capture_output=True, text=True, check=True)
    assert result.stdout == "version one\n"


def test_upgrade_prioritizes_local_command_and_replaces_old_path_block(tmp_path):
    home, artifact, environment = install_fixture(tmp_path, "/bin/bash")
    bin_path = home / ".local/bin"
    original = (
        "export KEEP_SETTING=yes\n"
        "# >>> term-station PATH >>>\n"
        "export OLD_STATION_BLOCK=yes\n"
        "# <<< term-station PATH <<<\n"
        "export AFTER_SETTING=yes\n"
    )
    (home / ".bashrc").write_text(original)
    subprocess.run([str(INSTALLER), str(artifact)], env=environment, check=True, capture_output=True)
    environment["PATH"] = f"/usr/bin:{bin_path}:/bin:{bin_path}"
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c",
         'source "$1/.bashrc"; source "$1/.bashrc"; printf "%s\\n" "$PATH"; '
         'printf "%s %s %s\\n" "$KEEP_SETTING" "$AFTER_SETTING" "${OLD_STATION_BLOCK-no}"',
         "bash", str(home)], env=environment, capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == [f"{bin_path}:/usr/bin:/bin", "yes yes no"]
    assert (home / ".bashrc").read_text().count("# >>> term-station PATH >>>") == 1
    assert next(home.glob(".bashrc.term-station-backup.*")).read_text() == original


def test_incomplete_bundle_does_not_replace_existing_command(tmp_path):
    home, artifact, environment = install_fixture(tmp_path, "/bin/zsh")
    subprocess.run([str(INSTALLER), str(artifact)], env=environment, check=True, capture_output=True)
    command = home / ".local/bin/term-station"
    before_command = command.read_bytes()
    before_config = (home / ".zshrc").read_bytes()
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    result = subprocess.run([str(INSTALLER), str(incomplete)], env=environment, capture_output=True)
    assert result.returncode != 0
    assert command.read_bytes() == before_command
    assert (home / ".zshrc").read_bytes() == before_config
