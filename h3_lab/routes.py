"""HTTP API Routes for H3 Studio Lab under /h3_studio/lab/*.

Namespaced separately from legacy production routes.
"""

import asyncio
import json
import logging
import os
import pathlib
from aiohttp import web

from .assets import AssetService
from .capabilities import check_capabilities
from .contexts import ContextService
from .jobs import JobService
from .projects import ProjectService

_LOG = logging.getLogger("h3_lab.routes")


def create_lab_services(storage_root: str):
    root = pathlib.Path(storage_root) / "lab_storage"
    root.mkdir(parents=True, exist_ok=True)
    assets = AssetService(str(root))
    jobs = JobService(str(root), asset_service=assets)
    projects = ProjectService(str(root), asset_service=assets)
    contexts = ContextService(str(root))
    return {
        "assets": assets,
        "jobs": jobs,
        "projects": projects,
        "contexts": contexts,
        "storage_root": root,
    }


def register_lab_routes(app_or_routes, storage_root: str, folder_paths_mod=None, nodes_mod=None):
    services = create_lab_services(storage_root)
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
            return web.json_response({"job": job_rec, "is_duplicate": is_dup}, status=200 if is_dup else 201)
        except ValueError as e:
            code = getattr(e, "status_code", 400)
            return web.json_response({"error": str(e)}, status=code)

    async def handle_get_job(request):
        jid = request.match_info["id"]
        rec = await asyncio.to_thread(jobs.get_job, jid)
        if not rec:
            return web.json_response({"error": "Job not found"}, status=404)
        return web.json_response(rec)

    async def handle_cancel_job(request):
        jid = request.match_info["id"]
        try:
            rec = await asyncio.to_thread(jobs.request_cancel, jid)
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
        for tid in take_ids:
            t = takes_map.get(tid)
            if not t or not t.get("output_file"):
                return web.json_response({"error": f"Take {tid} has no output file"}, status=400)
            p = pathlib.Path(services["storage_root"]) / t["output_file"]
            if not p.is_file():
                p_alt = services["storage_root"].parent / "output" / t["output_file"]
                if p_alt.is_file():
                    p = p_alt
                else:
                    return web.json_response({"error": f"File {t['output_file']} not found on disk"}, status=404)
            clip_paths.append(str(p))

        export_id = f"export_{os.urandom(6).hex()}"
        out_dir = services["storage_root"] / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / f"{pid}_{export_id}.mp4")
        try:
            from .assembly import assemble_sequence
            res = await asyncio.to_thread(assemble_sequence, clip_paths, out_path)
            return web.json_response({
                "export_id": export_id,
                "output_file": f"exports/{pid}_{export_id}.mp4",
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
            output_dir = str(services["storage_root"].parent / "images")
            qwen_path = pathlib.Path(output_dir) / fname
            rec = await asyncio.to_thread(projects.import_qwen_image, str(qwen_path), pid, role, alias)
            return web.json_response({"asset": rec}, status=201)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    # Context endpoints
    async def handle_context_usage(request):
        usage = await asyncio.to_thread(contexts.get_disk_usage)
        return web.json_response(usage)

    async def handle_context_purge(request):
        cid = request.match_info["id"]
        await asyncio.to_thread(contexts.purge_context, cid)
        return web.json_response({"ok": True})

    # Register routes
    routes = [
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
        ("GET", "/h3_studio/lab/jobs/{id}", handle_get_job),
        ("POST", "/h3_studio/lab/jobs/{id}/cancel", handle_cancel_job),
        ("POST", "/h3_studio/lab/import_qwen_image", handle_import_qwen_image),
        ("GET", "/h3_studio/lab/contexts/usage", handle_context_usage),
        ("POST", "/h3_studio/lab/contexts/{id}/purge", handle_context_purge),
    ]

    for method, path, handler in routes:
        if hasattr(router, "add_route"):
            router.add_route(method, path, handler)
        elif hasattr(router, method.lower()):
            getattr(router, method.lower())(path)(handler)

    return services
