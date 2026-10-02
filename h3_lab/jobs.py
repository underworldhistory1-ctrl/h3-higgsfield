"""Durable Job Service for H3 Studio Lab.

Provides:
- Idempotent submission keyed by client request ID.
- ComfyUI queue/history reconciliation with crash-gap delayed reply handling.
- Distinct cancel_requested vs cancelled states with foreign running job protection.
- Safe asset lease tracking preventing premature input sweeping.
"""

import hashlib
import json
import logging
import os
import pathlib
import threading
import time
import uuid

_LOG = logging.getLogger("h3_lab.jobs")

VALID_STATES = (
    "draft", "validating", "uploading", "queued", "loading",
    "sampling", "decoding", "saving", "completed",
    "cancel_requested", "cancelled", "failed", "unknown"
)

ACTIVE_STATES = (
    "draft", "validating", "uploading", "queued", "loading",
    "sampling", "decoding", "saving", "cancel_requested", "unknown"
)

TERMINAL_STATES = ("completed", "cancelled", "failed")


class JobService:
    def __init__(self, storage_root: str, asset_service=None, server_instance_id: str = None):
        self.storage_root = pathlib.Path(storage_root).resolve()
        self.jobs_dir = self.storage_root / "jobs"
        self.jobs_manifest_path = self.storage_root / "jobs_manifest.json"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.asset_service = asset_service
        self.server_instance_id = server_instance_id or f"srv_{uuid.uuid4().hex[:8]}"
        self._lock = threading.RLock()
        self._jobs = {}  # job_id -> dict
        self._request_index = {}  # request_id -> job_id
        self._prompt_index = {}  # prompt_id -> job_id
        self._load_jobs()

    def _load_jobs(self):
        with self._lock:
            if self.jobs_manifest_path.is_file():
                try:
                    with open(self.jobs_manifest_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._jobs = data
                        for jid, job in self._jobs.items():
                            req_id = job.get("request_id")
                            if req_id:
                                self._request_index[req_id] = jid
                            pid = job.get("prompt_id")
                            if pid:
                                self._prompt_index[pid] = jid
                            # Re-arm asset leases if job is active
                            if self.asset_service and job.get("state") in ACTIVE_STATES:
                                for item in job.get("asset_leases", []):
                                    self.asset_service.acquire_lease(item, jid)
                except (OSError, ValueError) as err:
                    _LOG.error("Failed to load jobs manifest: %s", err)
                    self._jobs = {}

    def _save_jobs_locked(self):
        tmp = self.jobs_manifest_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._jobs, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.jobs_manifest_path)
        finally:
            if tmp.is_file():
                tmp.unlink(missing_ok=True)

    def hash_spec(self, spec: dict) -> str:
        serialized = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def submit_job(self, request_id: str, render_spec: dict, asset_leases: list = None,
                   project_id: str = None, take_id: str = None) -> tuple:
        """
        Idempotent job submission.
        Returns (job_record, is_duplicate).
        Raises ValueError with code 409 if request_id is reused with different payload.
        """
        if not request_id:
            raise ValueError("Missing client request_id")

        spec_hash = self.hash_spec(render_spec)
        leases = list(asset_leases or [])

        with self._lock:
            existing_job_id = self._request_index.get(request_id)
            if existing_job_id:
                existing = self._jobs.get(existing_job_id)
                if existing:
                    if existing.get("request_hash") == spec_hash:
                        return dict(existing), True
                    else:
                        err = ValueError("Request ID already used with different payload")
                        err.status_code = 409
                        raise err

            job_id = str(uuid.uuid4())
            now = time.time()
            record = {
                "schema_version": 1,
                "job_id": job_id,
                "request_id": request_id,
                "request_hash": spec_hash,
                "project_id": project_id,
                "take_id": take_id,
                "asset_leases": leases,
                "prompt_id": None,
                "server_instance_id": self.server_instance_id,
                "state": "validating",
                "progress": {"step": 0, "total": 0, "phase": "validating"},
                "output": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }

            self._jobs[job_id] = record
            self._request_index[request_id] = job_id
            if self.asset_service:
                for item in leases:
                    self.asset_service.acquire_lease(item, job_id)
            self._save_jobs_locked()
            return dict(record), False

    def get_job(self, job_id: str) -> dict:
        with self._lock:
            rec = self._jobs.get(job_id)
            return dict(rec) if rec else None

    def get_job_by_request_id(self, request_id: str) -> dict:
        with self._lock:
            jid = self._request_index.get(request_id)
            return self.get_job(jid) if jid else None

    def get_job_by_prompt_id(self, prompt_id: str) -> dict:
        with self._lock:
            jid = self._prompt_index.get(prompt_id)
            return self.get_job(jid) if jid else None

    def update_job(self, job_id: str, **kwargs) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Job not found: {job_id}")

            for k, v in kwargs.items():
                if k == "state":
                    if v not in VALID_STATES:
                        raise ValueError(f"Invalid state: {v}")
                    job["state"] = v
                    if v in TERMINAL_STATES and self.asset_service:
                        self.asset_service.release_all_leases_for_owner(job_id)
                elif k == "prompt_id" and v:
                    job["prompt_id"] = v
                    self._prompt_index[v] = job_id
                elif k == "progress" and isinstance(v, dict):
                    job["progress"] = dict(v)
                elif k in ("output", "error", "take_id", "project_id"):
                    job[k] = v

            job["updated_at"] = time.time()
            self._save_jobs_locked()
            return dict(job)

    def is_file_leased(self, filename: str) -> bool:
        """Check if any non-terminal job holds a lease on filename."""
        with self._lock:
            for job in self._jobs.values():
                if job.get("state") in ACTIVE_STATES:
                    if filename in job.get("asset_leases", []):
                        return True
            return False

    async def reconcile_submission_gap(self, job_id: str, comfy_client) -> dict:
        """
        Reconcile an accepted-but-delayed submission where the prompt was sent
        to ComfyUI but the response with prompt_id was lost or interrupted.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            req_id = job.get("request_id")
            if not req_id:
                return dict(job)

        queue = await comfy_client.get_queue()
        running = queue.get("queue_running", [])
        pending = queue.get("queue_pending", [])

        # Look in queue extra_data for our request_id / job_id
        for entry in running + pending:
            # ComfyUI queue format: [number, prompt_id, prompt_dict, extra_data, outputs]
            if len(entry) >= 4 and isinstance(entry[3], dict):
                extra = entry[3].get("extra_pnginfo", {}) or entry[3]
                if extra.get("h3_lab_job_id") == job_id or extra.get("h3_lab_request_id") == req_id:
                    prompt_id = entry[1]
                    return self.update_job(job_id, prompt_id=prompt_id, state="queued")

        # Check history
        history = await comfy_client.get_history()
        for pid, hitem in history.items():
            prompt_data = hitem.get("prompt", [])
            if len(prompt_data) >= 4 and isinstance(prompt_data[3], dict):
                extra = prompt_data[3].get("extra_pnginfo", {}) or prompt_data[3]
                if extra.get("h3_lab_job_id") == job_id or extra.get("h3_lab_request_id") == req_id:
                    outputs = hitem.get("outputs", {})
                    status = hitem.get("status", {})
                    # Look for video output
                    video_info = None
                    for node_out in outputs.values():
                        if "videos" in node_out:
                            video_info = node_out["videos"][0]
                            break
                    if video_info:
                        return self.update_job(job_id, prompt_id=pid, state="completed", output=video_info)
                    elif status.get("status_str") == "error":
                        return self.update_job(job_id, prompt_id=pid, state="failed", error=status)

        # If not found anywhere and state was validating, mark unknown to preserve leases
        with self._lock:
            if job["state"] in ("validating", "uploading"):
                return self.update_job(job_id, state="unknown")
            return dict(job)

    async def request_cancel(self, job_id: str, comfy_client) -> dict:
        """
        Request cancellation of a job.
        Rules:
        - cancel_requested is NOT cancelled.
        - Check target job before interruption; refuse interrupt if foreign job is running.
        - Do not delete inputs while state is unknown or cancel_requested.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Job not found: {job_id}")
            if job["state"] in TERMINAL_STATES:
                return dict(job)
            prompt_id = job.get("prompt_id")

        # Mark cancel_requested first
        job = self.update_job(job_id, state="cancel_requested")
        if not prompt_id:
            # Not yet queued in ComfyUI: immediately transition to cancelled
            return self.update_job(job_id, state="cancelled")

        try:
            queue = await comfy_client.get_queue()
        except Exception as err:
            _LOG.warning("Failed to check queue during cancellation: %s", err)
            # Retain cancel_requested and leases!
            return job

        running = queue.get("queue_running", [])
        pending = queue.get("queue_pending", [])

        # 1. If in pending queue, delete it directly
        pending_ids = [item[1] for item in pending if len(item) > 1]
        if prompt_id in pending_ids:
            try:
                res = await comfy_client.delete_from_queue([prompt_id])
                if res.get("ok", True):
                    # Verify deletion
                    updated_queue = await comfy_client.get_queue()
                    still_pending = [item[1] for item in updated_queue.get("queue_pending", [])]
                    if prompt_id not in still_pending:
                        return self.update_job(job_id, state="cancelled")
            except Exception as e:
                _LOG.warning("Queue delete failed: %s", e)
                return job

        # 2. If in running queue
        running_ids = [item[1] for item in running if len(item) > 1]
        if prompt_id in running_ids:
            # Job is actively running: send interrupt
            try:
                res = await comfy_client.interrupt()
                if not res.get("ok", False):
                    # ComfyUI returned error (e.g. HTTP 500)
                    _LOG.warning("Interrupt returned error: %s", res)
                    return job  # Keep cancel_requested!
            except Exception as e:
                _LOG.warning("Interrupt request threw: %s", e)
                return job

            # Wait or poll to confirm removal from running
            try:
                for _ in range(5):
                    q = await comfy_client.get_queue()
                    r_ids = [item[1] for item in q.get("queue_running", [])]
                    if prompt_id not in r_ids:
                        return self.update_job(job_id, state="cancelled")
                    time.sleep(0.1)
            except Exception:
                pass
            return job

        # 3. Foreign job is running?
        if running_ids and prompt_id not in running_ids:
            _LOG.info("Another job (%s) is running, not target (%s); refusing global interrupt",
                      running_ids[0], prompt_id)
            return job

        # Not in running or pending; check history
        try:
            history = await comfy_client.get_history(prompt_id)
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "success":
                    # Finished before cancel arrived
                    video_info = None
                    for node_out in entry.get("outputs", {}).values():
                        if "videos" in node_out:
                            video_info = node_out["videos"][0]
                            break
                    if video_info:
                        return self.update_job(job_id, state="completed", output=video_info)
                return self.update_job(job_id, state="cancelled")
        except Exception:
            pass

        return job
