"""CPU-only V2 editing server with isolated storage and inference disabled.

This process never imports ComfyUI, torch or CUDA and never submits work to
the production server. Run the full V2 ComfyUI profile only when resources allow.
"""
import argparse
import asyncio
import json
import pathlib
import re
import sys
import uuid
from types import SimpleNamespace

from aiohttp import ClientSession, ClientTimeout, web

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from h3_lab.paths import owned_path
from h3_lab.routes import register_lab_routes

REASON = "V2 editing standby: inference is disabled while production renders use the shared GPU and memory."
READ_ONLY_PATHS = {"/system_stats", "/h3_studio/readiness", "/object_info"}
SAFE_NOOPS = {"session_resume", "session", "heartbeat", "session_close", "track", "register_job", "keep", "leave", "resume"}
LAB_SERVICES = web.AppKey("v2_lab_services", dict)


def isolated_directory(root):
    marker = root / ".h3-v2-standby-owned"
    if root.exists() and not marker.is_file() and any(root.iterdir()):
        raise ValueError("Refusing nonempty unmarked storage: choose a new isolated V2 directory")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("H3 V2 standalone editing storage\n", encoding="utf-8")


class ProductionReader:
    def __init__(self, url):
        if url != "http://127.0.0.1:8188":
            raise ValueError("Production reads are restricted to http://127.0.0.1:8188")
        self.url = url

    async def get(self, path):
        if path not in READ_ONLY_PATHS and not re.fullmatch(r"/object_info/[A-Za-z0-9_]+", path):
            raise ValueError("Production endpoint is not on the read-only allowlist")
        async with ClientSession(timeout=ClientTimeout(total=10)) as session:
            async with session.get(self.url + path, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError("Production read is currently unavailable")
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content) > 16 * 1024 * 1024:
                        raise ValueError("Production response exceeds the read limit")
                return json.loads(content)


def create_app(output_root, input_root, models_root, production_url="http://127.0.0.1:8188", *, reader=None):
    output = pathlib.Path(output_root).resolve()
    inputs = pathlib.Path(input_root).resolve()
    models = pathlib.Path(models_root).resolve()
    if output == inputs or output.is_relative_to(inputs) or inputs.is_relative_to(output):
        raise ValueError("V2 input and output must be separate directories")
    if any(root == models or root.is_relative_to(models) or models.is_relative_to(root) for root in (output, inputs)):
        raise ValueError("V2 storage must not overwrite the model disk")
    isolated_directory(output)
    isolated_directory(inputs)
    production = reader or ProductionReader(production_url)

    @web.middleware
    async def standby_policy(request, handler):
        path = request.path
        inference = path in ("/prompt", "/queue", "/interrupt") or path.startswith("/h3_studio/lab/jobs")
        if request.method != "GET" and inference:
            return web.json_response({"error": REASON, "standby": True, "inference_enabled": False}, status=403)
        return await handler(request)

    app = web.Application(middlewares=[standby_policy], client_max_size=501 * 1024 * 1024)
    # Routes consult class-name membership only; they instantiate no Comfy nodes.
    node_status = SimpleNamespace(NODE_CLASS_MAPPINGS={})
    folder_status = SimpleNamespace(get_full_path=lambda category, filename:
        str(owned_path(models / category, filename, require_file=False)))
    lab_routes = web.RouteTableDef()
    services = register_lab_routes(lab_routes, str(output), folder_paths_mod=folder_status,
        nodes_mod=node_status, output_root=output, input_root=inputs)
    app[LAB_SERVICES] = services

    async def capabilities(request):
        from h3_lab.capabilities import check_capabilities
        try:
            info = await production.get("/object_info")
            node_status.NODE_CLASS_MAPPINGS = {key: None for key in info}
        except Exception:
            node_status.NODE_CLASS_MAPPINGS = {}
        result = check_capabilities(folder_status, node_status)
        result.update(standby=True, inference_enabled=False, ready=False, guides_ready=False,
                      continuation_ready=False, reason=REASON)
        result["missing_reasons"].insert(0, REASON)
        return web.json_response(result)

    for route in lab_routes:
        if route.path != "/h3_studio/lab/capabilities":
            app.router.add_route(route.method, route.path, route.handler, **route.kwargs)
    app.router.add_get("/h3_studio/lab/capabilities", capabilities)

    async def proxy_read(request):
        try:
            result = await production.get(request.path)
            if request.path == "/h3_studio/readiness":
                result.update(standby=True, inference_enabled=False, ready=False, reason=REASON)
            return web.json_response(result)
        except Exception:
            return web.json_response({"error": "Production status is unavailable", "standby": True,
                "inference_enabled": False, "reason": REASON}, status=503)

    for path in READ_ONLY_PATHS:
        app.router.add_get(path, proxy_read)
    app.router.add_get("/object_info/{name}", proxy_read)

    async def queue(request):
        return web.json_response({"queue_running": [], "queue_pending": [], "standby": True,
                                  "inference_enabled": False, "reason": REASON})
    async def history(request):
        return web.json_response({})
    async def library(request):
        media_type = "images" if request.path.endswith("image_library") else "video"
        directory = output / ("images" if media_type == "images" else "video")
        extension = ".png" if media_type == "images" else ".mp4"
        prefix = "qwen_studio_" if media_type == "images" else "h3_studio_"
        records = []
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file() and path.name.startswith(prefix) and path.suffix.lower() == extension:
                    safe = owned_path(directory, path.name)
                    stat = safe.stat()
                    records.append({"filename": safe.name, "subfolder": directory.name, "type": "output",
                                    "bytes": stat.st_size, "modified": stat.st_mtime, "kept": True})
        return web.json_response({"items": records, "videos": records, "images": records if media_type == "images" else [],
                                  "files": records, "total": len(records), "standby": True})
    async def loras(request):
        directory = models / "loras"
        names = sorted(path.name for path in directory.glob("*.safetensors") if path.is_file())
        return web.json_response({"items": names, "loras": names, "standby": True})
    async def view(request):
        try:
            kind = request.query.get("type", "output")
            if kind not in ("input", "output"):
                raise ValueError("Unsupported V2 media storage")
            root = inputs if kind == "input" else output
            folder = request.query.get("subfolder", "")
            name = request.query.get("filename", "")
            relative = str(pathlib.PurePosixPath(folder) / name)
            path = owned_path(root, relative)
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".wav", ".mp3", ".flac", ".m4a"):
                raise ValueError("Only V2 media files are served")
            return web.FileResponse(path)
        except (ValueError, FileNotFoundError):
            return web.json_response({"error": "V2 media not found"}, status=404)
    async def websocket(request):
        socket = web.WebSocketResponse(heartbeat=30)
        await socket.prepare(request)
        await socket.send_json({"type": "status", "data": {"sid": "v2_standby_" + uuid.uuid4().hex,
            "status": {"exec_info": {"queue_remaining": 0}}, "standby": True, "inference_enabled": False}})
        async for message in socket:
            if message.type == web.WSMsgType.ERROR:
                break
        return socket
    async def image_readiness(request):
        return web.json_response({"ready": False, "standby": True, "inference_enabled": False, "reason": REASON,
            "profiles": {"int8": {"ready": False}, "bf16": {"ready": False}}, "nodes": {}})
    async def legacy_post(request):
        action = request.match_info["action"]
        if action in SAFE_NOOPS:
            return web.json_response({"ok": True, "recovered": [], "standby": True})
        if action == "discard":
            try:
                body = await request.json()
                name = body.get("filename", "")
                if not name.startswith("h3_studio_kf") or not services["assets"].can_discard_input(name) or services["jobs"].is_file_leased(name):
                    raise ValueError("Input is not disposable")
                path = owned_path(inputs, name)
                await asyncio.to_thread(path.unlink)
                return web.json_response({"ok": True})
            except (ValueError, FileNotFoundError):
                return web.json_response({"ok": False}, status=400)
        return web.json_response({"error": REASON, "standby": True, "inference_enabled": False}, status=403)

    app.router.add_get("/queue", queue)
    app.router.add_get("/history", history)
    app.router.add_get("/history/{id}", history)
    app.router.add_get("/h3_studio/library", library)
    app.router.add_get("/h3_studio/image_library", library)
    app.router.add_get("/h3_studio/loras", loras)
    app.router.add_get("/h3_studio/image_readiness", image_readiness)
    app.router.add_get("/view", view)
    app.router.add_get("/ws", websocket)
    app.router.add_post("/h3_studio/{action}", legacy_post)
    async def root(request):
        raise web.HTTPFound("/extensions/h3_studio_v2/index.html")
    app.router.add_get("/", root)
    app.router.add_static("/extensions/h3_studio_v2/", REPOSITORY / "web", show_index=False, follow_symlinks=False)
    app.router.add_static("/extensions/h3_studio/", REPOSITORY / "web", show_index=False, follow_symlinks=False)
    return app


def main():
    parser = argparse.ArgumentParser(description=REASON)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--models-root", required=True)
    parser.add_argument("--production-url", default="http://127.0.0.1:8188", choices=["http://127.0.0.1:8188"])
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8190)
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535 or options.port in (8188, 8189, 7860):
        parser.error("Choose an unused V2 port, separate from production and LTX")
    app = create_app(options.output_root, options.input_root, options.models_root, options.production_url)
    web.run_app(app, host=options.host, port=options.port)


if __name__ == "__main__":
    main()
