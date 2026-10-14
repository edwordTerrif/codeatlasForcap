"""분석할 코드를 가져온다: GitHub 저장소는 git clone, 로컬 폴더는 그대로 사용."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)"
    r"(?:\.git)?(?:/tree/(?P<branch>[^/?#]+)[^?#]*)?/?(?:[?#].*)?$"
)


class FetchError(Exception):
    """저장소를 가져오지 못했을 때."""


def parse_github_url(source: str) -> tuple[str, str, str | None] | None:
    """'https://github.com/owner/repo(/tree/branch)' → (owner, repo, branch)."""
    m = GITHUB_RE.match(source.strip())
    if not m:
        return None
    return m.group("owner"), m.group("repo"), m.group("branch")


def fetch_source(source: str, repos_dir: Path, timeout: int = 300) -> tuple[Path, str]:
    """분석할 폴더 경로와 화면에 표시할 이름을 돌려준다."""
    source = source.strip()
    gh = parse_github_url(source)
    if gh:
        owner, repo, branch = gh
        if shutil.which("git") is None:
            raise FetchError("git이 설치되어 있지 않습니다. https://git-scm.com 에서 설치한 뒤 다시 시도하세요.")
        dest = repos_dir / (f"{owner}__{repo}" + (f"__{branch}" if branch else ""))
        if (dest / ".git").is_dir():
            # 이미 받아 둔 저장소면 최신 코드로 갱신 (오프라인이면 받아 둔 그대로 분석)
            try:
                subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "--depth", "1"],
                               capture_output=True, text=True, timeout=60)
            except (subprocess.TimeoutExpired, OSError):
                pass
        else:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["git", "clone", "--depth", "1", "--single-branch"]
            if branch:
                cmd += ["--branch", branch]
            cmd += [f"https://github.com/{owner}/{repo}.git", str(dest)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as e:
                shutil.rmtree(dest, ignore_errors=True)
                raise FetchError("저장소를 내려받는 데 시간이 너무 오래 걸립니다.") from e
            if proc.returncode != 0:
                shutil.rmtree(dest, ignore_errors=True)
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
                raise FetchError(f"git clone 실패: {detail[0]} (주소가 맞는지, 공개 저장소인지 확인하세요)")
        return dest, f"{owner}/{repo}"

    path = Path(source).expanduser()
    if path.is_dir():
        return path.resolve(), path.resolve().name
    raise FetchError("GitHub 저장소 주소(https://github.com/사용자/저장소) 또는 내 컴퓨터의 폴더 경로를 입력하세요.")
