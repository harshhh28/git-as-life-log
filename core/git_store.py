from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from git import Repo


@dataclass
class GitCommitResult:
    committed: bool
    commit_hash: str | None
    message: str


class GitStore:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()
        self.repo = Repo(self.repo_root)

    def commit_paths(self, paths: Iterable[Path], message: str) -> GitCommitResult:
        rel_paths = []
        for p in paths:
            rel_paths.append(str(Path(p).resolve().relative_to(self.repo_root)))
        if rel_paths:
            self.repo.index.add(rel_paths)
        if not self.repo.is_dirty(untracked_files=True):
            return GitCommitResult(False, None, "No changes detected")
        commit = self.repo.index.commit(message)
        return GitCommitResult(True, commit.hexsha, message)

    def list_commits(self, path: str | None = None, max_count: int = 50) -> list[dict[str, str]]:
        kwargs = {"max_count": max_count}
        if path:
            kwargs["paths"] = path
        commits = []
        for c in self.repo.iter_commits(**kwargs):
            commits.append(
                {
                    "hexsha": c.hexsha,
                    "summary": c.summary,
                    "committed_datetime": c.committed_datetime.isoformat(),
                    "author": str(c.author),
                }
            )
        return commits

    def git_log_text(self, path: str | None = None, max_count: int = 100) -> str:
        commits = self.list_commits(path=path, max_count=max_count)
        lines = [f"{c['committed_datetime']} | {c['hexsha'][:8]} | {c['summary']}" for c in commits]
        return "\n".join(lines)
