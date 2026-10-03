"""HTTP API Routes for H3 Studio Lab under /h3_studio/lab/*.

Namespaced separately from legacy production routes.
"""

import asyncio
import json
import logging
import os
import pathlib
import shutil
import uuid
import re
import subprocess
from aiohttp import web

from .assets import AssetService
from .capabilities import check_capabilities
from .contexts import ContextService
from .jobs import JobService
from .projects import ProjectService
from .paths import owned_path, owned_video

_LOG = logging.getLogger("h3_lab.routes")


def create_lab_services(storage_root: str, output_root=None):
    root = pathlib.Path(storage_root) / "lab_storage"
    root.mkdir(parents=True, exist_ok=True)
    assets = AssetService(str(root))
    jobs = JobService(str(root), asset_service=assets)
    projects = ProjectService(str(root), asset_service=assets, output_root=output_root)
    contexts = ContextService(str(root))
    return {
        "assets": assets,
        "jobs": jobs,
        "projects": projects,
        "contexts": contexts,
        "storage_root": root,
    }


def register_lab_routes(app_or_routes, storage_root: str, folder_paths_mod=None, nodes_mod=None,
                        *, output_root=None, input_root=None, comfy_client=None):
    output_root = pathlib.Path(output_root).resolve() if output_root else None
    input_root = pathlib.Path(input_root).resolve() if input_root else None
    services = create_lab_services(storage_root, output_root=output_root)
    assets = services["assets"]
    jobs = services["jobs"]
    projects = services["projects"]
    contexts = services["contexts"]

    # Support either aiohttp web.Application or PromptServer routes table
    router = app_or_routes.router if hasattr(app_or_routes, "router") else app_or_routes

    async def handle_capabilities(request):
        caps = check_capabilities(folder_paths_mod, nodes_mod)
        return web.json_response(caps)

    async def handle_list_projects(request):
        projs = await asyncio.to_thread(projects.list_projects)
        return web.json_response({"projects": projs})

    async def handle_create_project(request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = body.get("name", "New Project")
        canvas = body.get("canvas")
        proj = await asyncio.to_thread(projects.create_project, name=name, canvas=canvas)
        return web.json_response(proj, status=201)

    async def handle_get_project(request):
        pid = request.match_info["id"]
        proj = await asyncio.to_thread(projects.get_project, pid)
        if not proj:
            return web.json_response({"error": "Project not found"}, status=404)
        return web.json_response(proj)

    async def handle_save_project(request):
        pid = request.match_info["id"]
        try:
            body = await request.json()
            proj_data = body.get("project", {})
            expected_rev = body.get("expected_revision")
            saved = await asyncio.to_thread(projects.save_project, pid, proj_data, expected_rev)
            return web.json_response(saved)
        except ValueError as e:
            code = getattr(e, "status_code", 400)
            return web.json_response({"error": str(e)}, status=code)
        except KeyError:
            return web.json_response({"error": "Project not found"}, status=404)

    async def handle_duplicate_project(request):
        pid = request.match_info["id"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            copy = await asyncio.to_thread(projects.duplicate_project, pid, body.get("name"))
            return web.json_response(copy)
        except KeyError:
            return web.json_response({"error": "Project not found"}, status=404)

    async def handle_export_project(request):
        pid = request.match_info["id"]
        try:
            bundle_path = await asyncio.to_thread(projects.export_bundle, pid)
            return web.FileResponse(
                bundle_path,
                headers={"Content-Disposition": f'attachment; filename="project_{pid}.zip"'}
            )
        except KeyError:
            return web.json_response({"error": "Project not found"}, status=404)
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=400)

    async def handle_import_project(request):
        reader = await request.multipart()
        field = await reader.next()
        if not field:
            return web.json_response({"error": "No file uploaded"}, status=400)

        tmp_zip = services["storage_root"] / f"upload_{os.urandom(8).hex()}.zip"
        with open(tmp_zip, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)

        try:
            imported = await asyncio.to_thread(projects.import_bundle, str(tmp_zip))
            return web.json_response(imported, status=201)
        except ValueError as err:
            return web.json_response({"error": str(err)}, status=400)
        finally:
            tmp_zip.unlink(missing_ok=True)

    async def handle_submit_job(request):
        if comfy_client is None:
            return web.json_response({"error": "Lab queue bridge is unavailable; use the Video workspace"}, status=503)
        try:
            body = await request.json()
            req_id = body.get("request_id")
            spec = body.get("render_spec", {})
            leases = body.get("asset_leases", [])
            pid = body.get("project_id")
            tid = body.get("take_id")
            job_rec, is_dup = await asyncio.to_thread(
                jobs.submit_job, req_id, spec, leases, pid, tid
            )
            if not is_dup:
                try:
                    result = await comfy_client.submit_prompt(spec, {
                        "h3_lab_job_id": job_rec["job_id"], "h3_lab_request_id": req_id})
                    if not isinstance(result, dict) or not result.get("prompt_id"):
                        raise RuntimeError("Queue did not acknowledge a prompt ID")
                    job_rec = await asyncio.to_thread(jobs.update_job, job_rec["job_id"],
                        prompt_id=result["prompt_id"], state="queued")
                    if job_rec.get("state") == "cancel_requested":
                        job_rec = await jobs.request_cancel(job_rec["job_id"], comfy_client)
                except ValueError as error:
                    await asyncio.to_thread(jobs.update_job, job_rec["job_id"], state="failed", error=str(error))
                    return web.json_response({"error": str(error), "job_id": job_rec["job_id"]}, status=400)
                except Exception:
                    await asyncio.to_thread(jobs.update_job, job_rec["job_id"], state="unknown",
                        error="Queue acknowledgement unavailable; reconcile before retrying")
                    return web.json_response({"error": "Queue acknowledgement unavailable", "job_id": job_rec["job_id"]}, status=503)
            return web.json_response({"job": job_rec, "is_duplicate": is_dup}, status=200 if is_dup else 201)
        except ValueError as e:
            code = getattr(e, "status_code", 400)
            return web.json_response({"error": str(e)}, status=code)

    async def handle_get_job(request):
        jid = request.match_info["id"]
        rec = await asyncio.to_thread(jobs.get_job, jid)
        if not rec:
            return web.json_response({"error": "Job not found"}, status=404)
        if comfy_client and rec.get("state") not in ("completed", "failed", "cancelled"):
            try:
                rec = await jobs.reconcile_submission_gap(jid, comfy_client)
            except Exception:
                pass
        return web.json_response(rec)

    async def handle_get_job_by_request(request):
        rec = await asyncio.to_thread(jobs.find_by_request, request.match_info["request_id"])
        if not rec:
            return web.json_response({"error": "Job not found"}, status=404)
        if comfy_client and rec.get("state") not in ("completed", "failed", "cancelled"):
            try:
                rec = await jobs.reconcile_submission_gap(rec["job_id"], comfy_client)
            except Exception:
                pass
        return web.json_response(rec)

    async def handle_cancel_by_request(request):
        req_id = request.match_info["request_id"]
        rec = await asyncio.to_thread(jobs.reserve_cancellation, req_id)
        if rec.get("state") in ("completed", "failed", "cancelled"):
            return web.json_response(rec)
        if comfy_client is None:
            return web.json_response({"error": "Lab queue bridge is unavailable; job retained", "job_id": rec["job_id"]}, status=503)
        try:
            return web.json_response(await jobs.request_cancel(rec["job_id"], comfy_client))
        except Exception:
            return web.json_response({"error": "Cancellation acknowledgement unavailable; job retained", "job_id": rec["job_id"]}, status=503)

    async def handle_cancel_job(request):
        if comfy_client is None:
            return web.json_response({"error": "Lab queue bridge is unavailable"}, status=503)
        jid = request.match_info["id"]
        try:
            rec = await jobs.request_cancel(jid, comfy_client)
            return web.json_response(rec)
        except KeyError:
            return web.json_response({"error": "Job not found"}, status=404)

    async def handle_assemble_project(request):
        pid = request.match_info["id"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        take_ids = body.get("accepted_take_ids", [])
        proj = await asyncio.to_thread(projects.get_project, pid)
        if not proj:
            return web.json_response({"error": "Project not found"}, status=404)

        takes_map = {t["take_id"]: t for t in proj.get("takes", [])}
        clip_paths = []
        overlaps = []
        if output_root is None:
            return web.json_response({"error": "Canonical output directory is unavailable"}, status=503)
        for tid in take_ids:
            t = takes_map.get(tid)
            if not t or not t.get("output_file"):
                return web.json_response({"error": f"Take {tid} has no output file"}, status=400)
            continuation = t.get("effective_settings", {}).get("continuation") or {}
            parent_id = t.get("parent_take_id") or continuation.get("source_take_id")
            if continuation.get("type") == "generated" and not parent_id:
                return web.json_response({"error": "Generated continuation lacks its parent take identity"}, status=400)
            if parent_id and (not clip_paths or parent_id != take_ids[len(clip_paths) - 1]):
                return web.json_response({"error": "A continuation must immediately follow its parent take"}, status=400)
            try:
                p = await asyncio.to_thread(owned_video, output_root, t["output_file"])
                overlap = t.get("overlap_frames")
                if overlap is None:
                    raise ValueError("Each take must declare overlap_frames explicitly (0 for independent clips)")
                if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0:
                    raise ValueError("Invalid overlap_frames")
            except (ValueError, FileNotFoundError) as err:
                return web.json_response({"error": str(err)}, status=400)
            clip_paths.append(str(p))
            overlaps.append(overlap)

        export_id = f"export_{os.urandom(6).hex()}"
        out_dir = services["storage_root"] / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / f"{pid}_{export_id}.mp4")
        try:
            from .assembly import assemble_sequence
            res = await asyncio.to_thread(assemble_sequence, clip_paths, out_path, overlap_frames=overlaps)
            relative = pathlib.Path(out_path).relative_to(output_root).as_posix()
            return web.json_response({
                "export_id": export_id,
                "output_file": relative,
                "assembly": res
            }, status=201)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_import_qwen_image(request):
        try:
            body = await request.json()
            fname = body.get("image_filename")
            pid = body.get("project_id")
            role = body.get("as_role", "reference")
            alias = body.get("alias")
            if output_root is None:
                return web.json_response({"error": "Canonical image directory is unavailable"}, status=503)
            qwen_path = owned_path(output_root / "images", fname)
            if not qwen_path.name.startswith("qwen_studio_") or qwen_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                raise ValueError("Only saved Qwen images may be imported")
            if pid and not projects.get_project(pid):
                raise ValueError("Project not found")
            rec = await asyncio.to_thread(projects.import_qwen_image, str(qwen_path), pid, role, alias)
            return web.json_response({"asset": rec}, status=201)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_upload_asset(request):
        reader = await request.multipart()
        field = await reader.next()
        if not field or not field.filename:
            return web.json_response({"error": "Upload a media file"}, status=400)
        suffix = pathlib.Path(field.filename).suffix.lower()
        kinds = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
                 ".mp4": "video", ".mov": "video", ".webm": "video", ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".m4a": "audio"}
        if suffix not in kinds:
            return web.json_response({"error": "Unsupported asset format"}, status=400)
        pid = request.query.get("project_id")
        if pid and not projects.get_project(pid):
            return web.json_response({"error": "Project not found"}, status=404)
        temporary = services["storage_root"] / ("upload_" + uuid.uuid4().hex + suffix)
        size = 0
        limit_mb = {"image": 25, "audio": 100, "video": 500}[kinds[suffix]]
        try:
            with temporary.open("wb") as stream:
                while chunk := await field.read_chunk():
                    size += len(chunk)
                    if size > limit_mb * 1024 * 1024:
                        return web.json_response({"error": f"Asset exceeds {limit_mb} MB"}, status=413)
                    await asyncio.to_thread(stream.write, chunk)
            rec = await asyncio.to_thread(assets.register_asset, str(temporary), kinds[suffix], field.filename, pid)
            if pid:
                project = await asyncio.to_thread(projects.get_project, pid)
                project.setdefault("assets", []).append({"asset_id": rec["asset_id"], "kind": rec["kind"],
                    "server_path": rec["server_path"], "alias": temporary.stem})
                await asyncio.to_thread(projects.save_project, pid, project, project["revision"])
            return web.json_response({"asset": rec}, status=201)
        finally:
            temporary.unlink(missing_ok=True)

    async def handle_get_asset(request):
        rec = await asyncio.to_thread(assets.get_asset, request.match_info["id"])
        if not rec:
            return web.json_response({"error": "Asset not found"}, status=404)
        return web.json_response({"asset": rec})

    async def handle_asset_file(request):
        rec = await asyncio.to_thread(assets.get_asset, request.match_info["id"])
        if not rec:
            return web.json_response({"error": "Asset not found"}, status=404)
        return web.FileResponse(owned_path(services["storage_root"], rec["server_path"]))

    async def handle_resolve_asset(request):
        if input_root is None:
            return web.json_response({"error": "Canonical input directory is unavailable"}, status=503)
        rec = await asyncio.to_thread(assets.get_asset, request.match_info["id"])
        if not rec:
            return web.json_response({"error": "Asset not found"}, status=404)
        source = owned_path(services["storage_root"], rec["server_path"])
        if rec["kind"] == "image":
            try:
                body = await request.json() if request.can_read_body else {}
                if not isinstance(body, dict):
                    raise ValueError("Image resolve options must be an object")
                from .images import resolve_image
                resolved = await asyncio.to_thread(resolve_image, source, input_root, rec["asset_id"], body)
                return web.json_response({**resolved, "asset_id": rec["asset_id"], "kind": "image", "metadata": rec.get("metadata", {})})
            except (ValueError, OSError) as error:
                return web.json_response({"error": str(error)}, status=400)
        filename = "h3_studio_kf_lab_" + rec["asset_id"] + source.suffix
        target = owned_path(input_root, filename, require_file=False)
        input_root.mkdir(parents=True, exist_ok=True)
        def copy_input():
            part = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
            try:
                shutil.copy2(source, part)
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
        await asyncio.to_thread(copy_input)
        return web.json_response({"asset_id": rec["asset_id"], "filename": filename, "kind": rec["kind"]})

    # Context endpoints
    async def handle_context_usage(request):
        usage = await asyncio.to_thread(contexts.get_disk_usage)
        if output_root:
            def runtime_usage():
                directory = output_root / "h3_lab_contexts"
                files = list(directory.glob("*.safetensors")) if directory.is_dir() else []
                return len(files), sum(path.stat().st_size for path in files if path.is_file())
            count, size = await asyncio.to_thread(runtime_usage)
            usage["count"] += count
            usage["total_bytes"] += size
            usage["total_mb"] = round(usage["total_bytes"] / (1024 * 1024), 2)
        return web.json_response(usage)

    async def handle_context_metadata(request):
        token = request.match_info["id"]
        if output_root is None or not re.fullmatch(r"[a-f0-9]{12}", token):
            return web.json_response({"error": "Invalid or unavailable context token"}, status=400)
        try:
            path = owned_path(output_root / "h3_lab_contexts", token + ".json")
            metadata = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
            owned_path(output_root / "h3_lab_contexts", token + ".safetensors")
            return web.json_response(metadata)
        except FileNotFoundError:
            return web.json_response({"error": "Preserved AV context not found"}, status=404)

    async def handle_media_frame(request):
        try:
            if output_root is None:
                return web.json_response({"error": "Canonical output root unavailable"}, status=503)
            body = await request.json()
            source = await asyncio.to_thread(owned_video, output_root, body.get("output_file") or "video/" + str(body.get("filename", "")))
            index = body.get("frame_index", 0)
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise ValueError("frame_index must be a nonnegative integer")
            pid = body.get("project_id")
            if pid and not projects.get_project(pid):
                raise ValueError("Project not found")
            from .assembly import get_ffmpeg_path
            temporary = services["storage_root"] / ("frame_" + uuid.uuid4().hex + ".png")
            try:
                cmd = [get_ffmpeg_path(), "-nostdin", "-v", "error", "-i", str(source),
                       "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", str(temporary)]
                await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=True, timeout=90)
                if not temporary.is_file():
                    raise ValueError("Requested frame does not exist")
                rec = await asyncio.to_thread(assets.register_asset, str(temporary), "image", source.stem + "_frame.png", pid)
                if pid:
                    project = projects.get_project(pid)
                    project.setdefault("assets", []).append({"asset_id": rec["asset_id"], "server_path": rec["server_path"],
                        "kind": "image", "role": body.get("as_role", "reference"), "alias": body.get("alias", "frame")})
                    await asyncio.to_thread(projects.save_project, pid, project, project["revision"])
                return web.json_response({"asset": rec}, status=201)
            finally:
                temporary.unlink(missing_ok=True)
        except (ValueError, FileNotFoundError, subprocess.SubprocessError) as err:
            return web.json_response({"error": str(err)}, status=400)

    async def handle_import_video(request):
        try:
            if output_root is None:
                return web.json_response({"error": "Canonical output root unavailable"}, status=503)
            body = await request.json()
            source = await asyncio.to_thread(owned_video, output_root,
                body.get("output_file") or "video/" + str(body.get("filename", "")), probe=False)
            if source.stat().st_size > 500 * 1024 * 1024:
                raise ValueError("Continuation source exceeds 500 MB")
            from .assembly import inspect_media
            from fractions import Fraction
            info = await asyncio.to_thread(inspect_media, str(source))
            video = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
            if not video or Fraction(video.get("r_frame_rate", "0/1")) != 24:
                raise ValueError("Continuation source must be canonical 24 fps video")
            frames = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
            if not 39 < frames <= 362:
                raise ValueError("Continuation source must contain between 40 and 362 frames")
            pid = body.get("project_id")
            if pid and not projects.get_project(pid):
                raise ValueError("Project not found")
            metadata = {"fps": 24, "width": video["width"], "height": video["height"], "frame_count": frames,
                        "source": "h3_video", "source_output": source.relative_to(output_root).as_posix()}
            asset = await asyncio.to_thread(assets.register_asset, str(source), "video", source.name, pid, metadata)
            if pid:
                project = await asyncio.to_thread(projects.get_project, pid)
                if not any(item.get("asset_id") == asset["asset_id"] for item in project.get("assets", [])):
                    project.setdefault("assets", []).append({"asset_id": asset["asset_id"], "kind": "video",
                        "server_path": asset["server_path"], "alias": "continuation_" + asset["asset_id"][:8], "metadata": metadata})
                    await asyncio.to_thread(projects.save_project, pid, project, project["revision"])
            return web.json_response({"asset": asset, "metadata": metadata}, status=201)
        except (ValueError, FileNotFoundError, subprocess.SubprocessError) as error:
            return web.json_response({"error": str(error)}, status=400)

    async def handle_media_export(request):
        try:
            if output_root is None:
                return web.json_response({"error": "Canonical output root unavailable"}, status=503)
            body = await request.json()
            source = await asyncio.to_thread(owned_video, output_root, body.get("output_file") or "video/" + str(body.get("filename", "")))
            format_name = body.get("format")
            if format_name not in ("wav", "muted_mp4"):
                raise ValueError("format must be wav or muted_mp4")
            from .assembly import get_ffmpeg_path
            name = uuid.uuid4().hex + (".wav" if format_name == "wav" else ".mp4")
            destination = services["storage_root"] / "exports" / name
            destination.parent.mkdir(exist_ok=True)
            cmd = [get_ffmpeg_path(), "-nostdin", "-v", "error", "-i", str(source)]
            cmd += ["-vn", "-c:a", "pcm_s16le"] if format_name == "wav" else ["-an", "-c:v", "copy"]
            cmd.append(str(destination))
            try:
                await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=True, timeout=180)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return web.json_response({"download_url": "/h3_studio/lab/exports/" + name, "filename": name}, status=201)
        except (ValueError, FileNotFoundError, subprocess.SubprocessError) as err:
            return web.json_response({"error": str(err)}, status=400)

    async def handle_export_file(request):
        try:
            return web.FileResponse(owned_path(services["storage_root"] / "exports", request.match_info["name"]))
        except (ValueError, FileNotFoundError):
            return web.json_response({"error": "Export not found"}, status=404)

    async def handle_context_purge(request):
        cid = request.match_info["id"]
        if output_root and re.fullmatch(r"[a-f0-9]{12}", cid):
            if comfy_client is None:
                return web.json_response({"error": "Queue visibility is required before purging a runtime context"}, status=503)
            try:
                queue = await comfy_client.get_queue()
            except Exception:
                return web.json_response({"error": "Queue inspection unavailable; context retained"}, status=503)
            if cid in json.dumps(queue.get("queue_running", []) + queue.get("queue_pending", [])):
                return web.json_response({"error": "Context is referenced by a queued or running prompt"}, status=409)
            if jobs.is_context_leased(cid):
                return web.json_response({"error": "Context belongs to an active job"}, status=409)
            def accepted_reference():
                for item in projects.list_projects():
                    project = projects.get_project(item["project_id"])
                    accepted = set(project.get("accepted_take_ids", []))
                    for take in project.get("takes", []):
                        if take.get("take_id") in accepted and cid in json.dumps(take):
                            return True
                return False
            if await asyncio.to_thread(accepted_reference):
                return web.json_response({"error": "Context belongs to an accepted take or its lineage"}, status=409)
            for suffix in (".safetensors", ".json"):
                path = owned_path(output_root / "h3_lab_contexts", cid + suffix, require_file=False)
                await asyncio.to_thread(path.unlink, missing_ok=True)
            return web.json_response({"ok": True})
        await asyncio.to_thread(contexts.purge_context, cid)
        return web.json_response({"ok": True})

    # Register routes
    routes = [
        ("POST", "/h3_studio/lab/media/frame", handle_media_frame),
        ("POST", "/h3_studio/lab/media/import_video", handle_import_video),
        ("POST", "/h3_studio/lab/media/export", handle_media_export),
        ("GET", "/h3_studio/lab/exports/{name}", handle_export_file),
        ("POST", "/h3_studio/lab/assets", handle_upload_asset),
        ("GET", "/h3_studio/lab/assets/{id}", handle_get_asset),
        ("GET", "/h3_studio/lab/assets/{id}/file", handle_asset_file),
        ("POST", "/h3_studio/lab/assets/{id}/resolve", handle_resolve_asset),
        ("GET", "/h3_studio/lab/capabilities", handle_capabilities),
        ("GET", "/h3_studio/lab/projects", handle_list_projects),
        ("POST", "/h3_studio/lab/projects", handle_create_project),
        ("GET", "/h3_studio/lab/projects/{id}", handle_get_project),
        ("POST", "/h3_studio/lab/projects/{id}", handle_save_project),
        ("POST", "/h3_studio/lab/projects/{id}/duplicate", handle_duplicate_project),
        ("GET", "/h3_studio/lab/projects/{id}/export", handle_export_project),
        ("POST", "/h3_studio/lab/projects/{id}/assemble", handle_assemble_project),
        ("POST", "/h3_studio/lab/projects/import", handle_import_project),
        ("POST", "/h3_studio/lab/jobs", handle_submit_job),
        ("GET", "/h3_studio/lab/jobs/by_request/{request_id}", handle_get_job_by_request),
        ("POST", "/h3_studio/lab/jobs/by_request/{request_id}/cancel", handle_cancel_by_request),
        ("GET", "/h3_studio/lab/jobs/{id}", handle_get_job),
        ("POST", "/h3_studio/lab/jobs/{id}/cancel", handle_cancel_job),
        ("POST", "/h3_studio/lab/import_qwen_image", handle_import_qwen_image),
        ("GET", "/h3_studio/lab/contexts/usage", handle_context_usage),
        ("GET", "/h3_studio/lab/contexts/{id}", handle_context_metadata),
        ("POST", "/h3_studio/lab/contexts/{id}/purge", handle_context_purge),
    ]

    for method, path, handler in routes:
        if hasattr(router, "add_route"):
            router.add_route(method, path, handler)
        elif hasattr(router, method.lower()):
            getattr(router, method.lower())(path)(handler)

    return services
