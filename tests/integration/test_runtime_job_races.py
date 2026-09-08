"""Independent controllers contend through actual SQLite connections."""

import json
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from factory.runtime_jobs import RuntimeJobs
from factory.store import Store

WORKER = """
import json, sys
from pathlib import Path
from factory.store import Store
from factory.runtime_jobs import RuntimeJobs
store = Store(Path(sys.argv[1]))
jobs = RuntimeJobs(store)
print('ready', flush=True)
sys.stdin.readline()
if sys.argv[2] == 'agent':
    result = jobs.schedule_agent(sys.argv[3], usd_limit=10, max_attempts=2)
elif sys.argv[2] == 'launch':
    from factory.agent_launches import AgentLaunches
    from factory.sandbox.base import RunHandle, RunStatus
    class Sandbox:
        def exec_detached(self, handle, script, env):
            with (Path(sys.argv[1]).parent / 'spawns').open('a') as stream:
                stream.write('spawn\\n')
        def poll(self, handle):
            return RunStatus.RUNNING
    invocation = store.runtime.invocation(sys.argv[3])
    handle = RunHandle(invocation['run_id'], 1, 'factory-build-race', '/work', Path(sys.argv[1]).parent / 'attempt')
    result = AgentLaunches(store, Sandbox()).start(sys.argv[3], handle, 'script', {}, usd_limit=10, max_attempts=2)
else:
    job = jobs.request_certification(sys.argv[3], {'sandbox': 'factory-build-race', 'generation': 'one'})
    result = [job['id'], jobs.claim_certification(job['id'], now=10, duration=30)]
print(json.dumps(result), flush=True)
store.close()
"""


def race(path: Path, operation: str, identities: list[str]) -> list[object]:
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(path), operation, identity],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for identity in identities
    ]
    try:
        for process in processes:
            assert process.stdout is not None
            assert select.select([process.stdout], [], [], 20)[0], "controller did not become ready"
            assert process.stdout.readline().strip() == "ready"
        with ThreadPoolExecutor(max_workers=len(processes)) as pool:
            futures = [
                pool.submit(process.communicate, "go\n", timeout=20) for process in processes
            ]
            results = [future.result() for future in futures]
        for process, (_, stderr) in zip(processes, results, strict=True):
            assert process.returncode == 0, stderr
        return [json.loads(stdout) for stdout, _ in results]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_two_controllers_cannot_take_the_last_agent_slot(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    store.runtime.configure("project", "synthetic", {"max_active_agents": 1})
    for number in range(2):
        run = store.insert_run(linear_id=f"SYN-{number}", project="synthetic", team="SYN")
        store.runtime.start_invocation(str(number), run.id, 1, "builder", {})
    results = race(path, "agent", ["0", "1"])
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    store.close()


def test_two_controllers_join_and_claim_one_certification(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    results = race(path, "certification", [run.id, run.id])
    first, second = results
    assert isinstance(first, list)
    assert isinstance(second, list)
    assert first[0] == second[0]
    assert sum(result[1] is not None for result in (first, second)) == 1
    store.close()


def test_two_controllers_launch_the_same_invocation_only_once(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    results = race(path, "launch", ["builder", "builder"])
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert (tmp_path / "spawns").read_text().splitlines() == ["spawn"]
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    store.close()


def test_independent_controllers_preserve_child_progress_reservations(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    store.runtime.configure(
        "project", "synthetic", {"max_active_agents": 8, "delegation_mode": "read-only"}
    )
    for number in range(8):
        run = store.insert_run(linear_id=f"SYN-{number}", project="synthetic", team="SYN")
        store.runtime.start_invocation(str(number), run.id, 1, "builder", {})
    results = race(path, "agent", [str(number) for number in range(8)])
    assert results.count(True) == 4
    assert results.count(False) == 4
    store.close()
    reopened = Store(path)
    jobs = RuntimeJobs(reopened)
    parents = jobs.active_agents("synthetic")
    for parent in parents:
        identifier = "child-" + parent["invocation_id"]
        reopened.runtime.start_invocation(identifier, parent["run_id"], 1, "documenter", {})
        assert jobs.schedule_agent(
            identifier, parent_id=parent["invocation_id"], usd_limit=10, max_attempts=2
        )
    assert len(jobs.active_agents("synthetic")) == 8
    reopened.close()
