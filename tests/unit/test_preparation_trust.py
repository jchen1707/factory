"""First-start trust changes are bounded by the declared project and workdir."""

from pathlib import Path

import pytest

from factory.agent import runtime_preparation_worker as worker


def test_worktree_trust_change_is_expected() -> None:
    original = {"model": "example"}
    expected = worker.expected_configurations(
        original, Path("/project/.factory/worktrees/T-1"), Path("/project")
    )
    assert {"model": "example", "projects": {"/project": {"trust_level": "trusted"}}} in expected
    assert original == {"model": "example"}


def test_clone_trust_is_still_supported() -> None:
    expected = worker.expected_configurations({}, Path("/project/clone"), Path("/project"))
    assert {"projects": {"/project/clone": {"trust_level": "trusted"}}} in expected
    assert {"projects": {"/": {"trust_level": "trusted"}}} not in expected
    assert {"model": "changed"} not in expected


@pytest.mark.parametrize("root", ["/other", "relative", "/project/../"])
def test_invalid_trust_root_is_refused(root: str) -> None:
    with pytest.raises(worker.PreparationFailure):
        worker.expected_configurations({}, Path("/project/work"), Path(root))


@pytest.mark.parametrize("distrusted", ["/project", "/project/work"])
def test_explicit_distrust_is_never_overridden(distrusted: str) -> None:
    with pytest.raises(worker.PreparationFailure):
        worker.expected_configurations(
            {"projects": {distrusted: {"trust_level": "untrusted"}}},
            Path("/project/work"),
            Path("/project"),
        )
