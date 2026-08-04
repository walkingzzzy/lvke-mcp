"""Hard-cancellable execution boundary for synchronous research providers.

``asyncio.to_thread`` can stop awaiting a blocking provider, but it cannot stop
the underlying HTTP/SDK call or its potential billing.  This module keeps sync
provider calls in a dedicated child process.  Timeout or cancellation
terminates (and, if necessary, kills) that process, so no orphan provider call
can continue after the research runtime has declared it stopped.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import pickle
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any


class SyncProviderExecutionError(RuntimeError):
    """Base error for the isolated synchronous-provider boundary."""


class SyncProviderIsolationUnavailable(SyncProviderExecutionError):
    """The provider or call cannot cross a spawn-process boundary."""


class SyncProviderCallTimeout(SyncProviderExecutionError):
    """The provider process was killed after its hard deadline."""


class SyncProviderCallCancelled(SyncProviderExecutionError):
    """The provider process was killed after research cancellation."""


class SyncProviderProcessTerminated(SyncProviderExecutionError):
    """A sibling call killed or unexpectedly lost the shared provider process."""


def _pickleable(value: Any, *, label: str) -> None:
    try:
        pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:  # noqa: BLE001 - stable boundary error
        raise SyncProviderIsolationUnavailable(
            f"{label} is not serializable for isolated provider execution"
        ) from exc


def _sync_provider_worker(
    provider: Any,
    requests,
    responses,
    max_workers: int,
) -> None:
    """Child entrypoint; requests run concurrently but share one kill boundary."""

    response_lock = threading.Lock()

    def publish(message: tuple[Any, ...]) -> None:
        with response_lock:
            responses.put(message)

    def completed(call_id: str, future: Future) -> None:
        try:
            value = future.result()
            _pickleable(value, label="provider response")
        except Exception as exc:  # noqa: BLE001 - returned as typed parent error
            publish(
                (
                    "result",
                    call_id,
                    False,
                    {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            )
        else:
            publish(("result", call_id, True, value))

    with ThreadPoolExecutor(
        max_workers=max(1, int(max_workers)),
        thread_name_prefix="research-provider-call",
    ) as executor:
        while True:
            message = requests.get()
            if not isinstance(message, tuple) or not message:
                continue
            operation = message[0]
            if operation == "close":
                break
            if operation != "call" or len(message) != 5:
                continue
            _operation, call_id, method_name, args, kwargs = message
            try:
                method = getattr(provider, str(method_name))
                future = executor.submit(method, *tuple(args), **dict(kwargs))
            except Exception as exc:  # noqa: BLE001
                publish(
                    (
                        "result",
                        str(call_id),
                        False,
                        {"type": type(exc).__name__, "message": str(exc)},
                    )
                )
                continue
            future.add_done_callback(
                lambda item, current_id=str(call_id): completed(current_id, item)
            )

    state = dict(getattr(provider, "__dict__", {}) or {})
    try:
        _pickleable(state, label="provider state")
    except SyncProviderIsolationUnavailable:
        state = {}
    publish(("closed", "", True, state))


class SyncProviderProcess:
    """Reusable, concurrent child-process boundary for one provider instance."""

    def __init__(self, provider: Any, *, max_workers: int = 1) -> None:
        self.provider = provider
        self.max_workers = max(1, int(max_workers))
        self._ctx = multiprocessing.get_context("spawn")
        self._requests = None
        self._responses = None
        self._process = None
        self._listener: threading.Thread | None = None
        self._listener_stop = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._pending: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._closed_result: queue.Queue = queue.Queue(maxsize=1)

    @property
    def pid(self) -> int | None:
        process = self._process
        return int(process.pid) if process is not None and process.pid else None

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._process is not None and self._process.is_alive():
                return
            _pickleable(self.provider, label="synchronous provider")
            requests = self._ctx.Queue()
            responses = self._ctx.Queue()
            process = self._ctx.Process(
                target=_sync_provider_worker,
                args=(self.provider, requests, responses, self.max_workers),
                name=f"research-provider-{getattr(self.provider, 'name', 'sync')}",
                daemon=True,
            )
            try:
                process.start()
            except Exception as exc:  # noqa: BLE001
                requests.close()
                responses.close()
                raise SyncProviderIsolationUnavailable(
                    "synchronous provider process could not be started"
                ) from exc
            self._requests = requests
            self._responses = responses
            self._process = process
            self._listener_stop.clear()
            self._listener = threading.Thread(
                target=self._listen,
                name=f"research-provider-listener-{process.pid}",
                daemon=True,
            )
            self._listener.start()

    def _listen(self) -> None:
        responses = self._responses
        process = self._process
        if responses is None:
            return
        while not self._listener_stop.is_set():
            try:
                message = responses.get(timeout=0.05)
            except queue.Empty:
                if process is not None and not process.is_alive():
                    self._fail_pending("provider process exited before returning a result")
                    return
                continue
            except (EOFError, OSError):
                self._fail_pending("provider response channel closed unexpectedly")
                return
            if not isinstance(message, tuple) or len(message) != 4:
                continue
            kind, call_id, success, payload = message
            if kind == "closed":
                try:
                    self._closed_result.put_nowait(payload if success else {})
                except queue.Full:
                    pass
                return
            if kind != "result":
                continue
            with self._pending_lock:
                target = self._pending.get(str(call_id))
            if target is not None:
                try:
                    target.put_nowait((bool(success), payload))
                except queue.Full:
                    pass

    def _fail_pending(self, message: str) -> None:
        with self._pending_lock:
            targets = list(self._pending.values())
        for target in targets:
            try:
                target.put_nowait((False, {"terminated": True, "message": message}))
            except queue.Full:
                pass

    async def call(
        self,
        method_name: str,
        *args: Any,
        timeout: float,
        should_cancel: Callable[[], bool] | None = None,
        **kwargs: Any,
    ) -> Any:
        self._ensure_started()
        requests = self._requests
        process = self._process
        if requests is None or process is None:
            raise SyncProviderIsolationUnavailable("provider process is unavailable")
        call_id = uuid.uuid4().hex
        message = ("call", call_id, str(method_name), tuple(args), dict(kwargs))
        _pickleable(message, label="provider call")
        result_queue: queue.Queue = queue.Queue(maxsize=2)
        with self._pending_lock:
            self._pending[call_id] = result_queue
        try:
            requests.put(message)
            deadline = time.monotonic() + max(0.001, float(timeout))
            while True:
                try:
                    success, payload = result_queue.get_nowait()
                except queue.Empty:
                    success = None
                    payload = None
                if success is not None:
                    if success:
                        return payload
                    if isinstance(payload, dict) and payload.get("terminated"):
                        raise SyncProviderProcessTerminated(
                            str(payload.get("message") or "provider process terminated")
                        )
                    detail = payload if isinstance(payload, dict) else {}
                    message_text = str(detail.get("message") or "provider call failed")
                    error_type = str(detail.get("type") or "ProviderError")
                    raise SyncProviderExecutionError(f"{error_type}: {message_text}")
                if should_cancel is not None and should_cancel():
                    self.terminate(
                        "research cancellation requested",
                        expected_process=process,
                    )
                    raise SyncProviderCallCancelled(
                        "synchronous provider process killed after cancellation"
                    )
                if time.monotonic() >= deadline:
                    self.terminate(
                        "provider call deadline exceeded",
                        expected_process=process,
                    )
                    raise SyncProviderCallTimeout(
                        "synchronous provider process killed after timeout"
                    )
                if not process.is_alive():
                    self.terminate(
                        "provider process exited unexpectedly",
                        expected_process=process,
                    )
                    raise SyncProviderProcessTerminated(
                        "provider process exited before returning a result"
                    )
                await asyncio.sleep(0.01)
        finally:
            with self._pending_lock:
                self._pending.pop(call_id, None)

    def terminate(
        self,
        reason: str = "provider process terminated",
        *,
        expected_process=None,
    ) -> None:
        with self._lifecycle_lock:
            process = self._process
            if expected_process is not None and process is not expected_process:
                return
            if process is not None and process.is_alive():
                process.terminate()
                process.join(timeout=0.25)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
            self._listener_stop.set()
            self._fail_pending(reason)

    async def aclose(self) -> None:
        with self._lifecycle_lock:
            process = self._process
            requests = self._requests
        if process is None:
            return
        if process.is_alive() and requests is not None:
            try:
                requests.put(("close",))
            except (OSError, ValueError):
                pass
            deadline = time.monotonic() + 2.0
            state: dict[str, Any] = {}
            while time.monotonic() < deadline:
                try:
                    received = self._closed_result.get_nowait()
                except queue.Empty:
                    if not process.is_alive():
                        break
                    await asyncio.sleep(0.01)
                    continue
                if isinstance(received, dict):
                    state = received
                break
            if state and hasattr(self.provider, "__dict__"):
                self.provider.__dict__.clear()
                self.provider.__dict__.update(state)
        self.terminate("provider executor closed")
        listener = self._listener
        if listener is not None:
            listener.join(timeout=0.25)
        for channel in (self._requests, self._responses):
            if channel is None:
                continue
            try:
                channel.close()
                channel.cancel_join_thread()
            except (OSError, ValueError):
                pass
        with self._lifecycle_lock:
            self._process = None
            self._requests = None
            self._responses = None
