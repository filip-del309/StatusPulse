import os
import subprocess
import tempfile
from pathlib import Path


class GitRepository:

    def __init__(
        self,
        url: str,
        path: str,
        branch: str,
        username: str,
        token: str,
    ):
        self.url = url
        self.path = Path(path)
        self.branch = branch
        self.username = username
        self.token = token

    def _run(self, command: list[str]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            askpass = Path(directory) / "git-askpass"

            askpass.write_text(
                """#!/bin/sh
case "$1" in
    *Username*)
        printf '%s\\n' "$GIT_USERNAME"
        ;;
    *Password*)
        printf '%s\\n' "$GIT_PASSWORD"
        ;;
esac
""",
                encoding="utf-8",
            )

            askpass.chmod(0o700)

            environment = os.environ.copy()

            environment["GIT_ASKPASS"] = str(askpass)
            environment["GIT_USERNAME"] = self.username
            environment["GIT_PASSWORD"] = self.token
            environment["GIT_TERMINAL_PROMPT"] = "0"

            subprocess.run(
                command,
                check=True,
                env=environment,
            )

    def clone(self) -> None:
        if self.path.exists():
            if not (self.path / ".git").exists():
                raise RuntimeError(
                    f"Path exists but is not a Git repository: {self.path}"
                )

            return

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._run([
            "git",
            "clone",
            "--branch",
            self.branch,
            self.url,
            str(self.path),
        ])

    def pull(self) -> None:
        self._run([
            "git",
            "-C",
            str(self.path),
            "checkout",
            self.branch,
        ])

        self._run([
            "git",
            "-C",
            str(self.path),
            "pull",
            "--ff-only",
            "origin",
            self.branch,
        ])