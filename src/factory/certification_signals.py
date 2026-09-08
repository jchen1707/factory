"""One durable signal fence across probe interruption, timeout and cancellation."""

from factory.store import Store


def recorded(store: Store, run_id: str, attempt: int, invocation_id: str) -> bool:
    return (
        store.runtime.db.execute(
            "SELECT 1 FROM effects WHERE run_id=? AND attempt=? AND step=? AND "
            "((system='certification' AND key IN ('interrupt','timeout')) OR "
            "(system='child-certification-cancel' AND key='signal'))",
            (run_id, attempt, invocation_id),
        ).fetchone()
        is not None
    )


def probe_may_signal(
    store: Store, job_id: str, run_id: str, attempt: int, invocation_id: str
) -> bool:
    """Use inside the signal-intent transaction, after observing the owned process."""
    job = store.runtime.db.execute(
        "SELECT status FROM runtime_certifications WHERE id=? AND run_id=?", (job_id, run_id)
    ).fetchone()
    return (
        job is not None
        and job["status"] == "checking"
        and not recorded(store, run_id, attempt, invocation_id)
    )
