from concurrent.futures import ThreadPoolExecutor
import copy
import logging
import threading
import time
import uuid

from atlas.files.service import FileProblem


class JobService:
    def __init__(self):
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atlas-index")
        self.job = {"running": False, "stage": "idle", "error": None}
        self.pending_refresh = None
        self.closed = False

    def status(self):
        with self.lock:
            return copy.deepcopy(self.job)

    def progress(self, stage=None, **details):
        with self.lock:
            if stage is not None:
                self.job["stage"] = stage
            self.job.update(details)

    def start(self, kind, operation):
        with self.lock:
            if self.closed or self.job["running"]:
                raise FileProblem("已有任务运行中或应用正在退出", 409)
            self.job = {"id": uuid.uuid4().hex, "kind": kind, "running": True,
                        "stage": "queued", "startedAt": time.time(), "error": None}
            self.executor.submit(self._run, operation)
            return copy.deepcopy(self.job)

    def refresh(self, operation):
        with self.lock:
            if self.closed:
                raise RuntimeError("应用正在退出")
            if self.job["running"]:
                self.pending_refresh = operation
                self.job["refreshQueued"] = True
                return copy.deepcopy(self.job)
            return self.start("refresh", operation)

    def _run(self, operation):
        while operation is not None:
            failure = None
            try:
                result = operation()
                self.progress(result=result)
            except Exception as error:
                logging.getLogger(__name__).exception("后台任务执行失败")
                failure = str(error) if isinstance(error, ValueError) else type(error).__name__
            with self.lock:
                operation = self.pending_refresh
                self.pending_refresh = None
                self.job["refreshQueued"] = False
                if failure is not None:
                    self.job.update(error=failure, stage="error")
                if operation is None:
                    self.job.update(running=False, finishedAt=time.time(),
                                    stage="error" if self.job.get("error") else "done")
                else:
                    self.job.update(kind="refresh", stage="queued", error=None)
                    self.job.pop("result", None)

    def close(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
