#!/usr/bin/env python3
"""Run H3 Higgsfield locally with renders executed on Comfy Cloud."""

import argparse
import asyncio
import importlib.util
import json
import os
import pathlib
import re
import sys
import time
import types

import aiohttp
from aiohttp import web

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLOUD = "https://cloud.comfy.org"
BILLING = "https://api.comfy.org"
SAVE_NODE = "12"
PREFIX_RE = re.compile(r"^video/h3_studio_([0-9a-f]{12})$")
LOCAL_INPUT_RE = re.compile(r"^h3_studio_kf_[0-9a-f]{32}\.[a-z0-9]+$")
REQUIRED_MODELS = {
    "fl2va": (
        "UNETLoader",
        "unet_name",
        "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    ),
    "ref2va": (
        "UNETLoader",
        "unet_name",
        "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    ),
    "text_encoder": (
        "CLIPLoader",
        "clip_name",
        "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    ),
    "video_vae": ("VAELoader", "vae_name", "minimax_h3_video_vae_fp16.safetensors"),
    "audio_vae": ("VAELoader", "vae_name", "minimax_h3_audio_vae_fp32.safetensors"),
}
READINESS_NODES = (
    "UNETLoader",
    "MiniMaxH3SigmaShift",
    "CLIPLoader",
    "VAELoader",
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3ReferenceToVideo",
    "ConditioningZeroOut",
    "KSampler",
    "VAEDecode",
    "VAEDecodeAudio",
    "CreateVideo",
    "H3SaveVideo",
    "SaveVideo",
    "LoadImage",
    "LoadVideo",
    "GetVideoComponents",
    "LoadAudio",
    "LoraLoaderModelOnly",
    "SpectrumApplyMiniMaxH3",
    "MiniMaxH3MotionCache",
)
H3_LORA_RE = re.compile(r"minimax_h3|(^|[^a-z])h3[_-]", re.IGNORECASE)
MEDIA_INPUTS = {"LoadImage": "image", "LoadVideo": "file", "LoadAudio": "audio"}


def load_key(env_file):
    """API key from the environment, else from a dotenv file; never printed."""
    key = os.environ.get("COMFYUI_API_KEY") or os.environ.get("COMFY_API_KEY")
    if not key and env_file:
        for line in (
            pathlib.Path(env_file).expanduser().read_text(encoding="utf-8").splitlines()
        ):
            name, _, value = line.partition("=")
            if name.strip() in ("COMFYUI_API_KEY", "COMFY_API_KEY") and value.strip():
                key = value.strip().strip("'\"")
                break
    if not key or not key.startswith("comfyui-"):
        sys.exit(
            "Set COMFYUI_API_KEY or pass --env-file with COMFYUI_API_KEY=comfyui-…"
        )
    return key


def install_shims(data_dir):
    """Stand-ins for the ComfyUI modules the H3 extension imports."""
    output_dir, input_dir = data_dir / "output", data_dir / "input"
    for directory in (output_dir / "video", input_dir):
        directory.mkdir(parents=True, exist_ok=True)
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.__file__ = str(data_dir / "folder_paths.py")
    folder_paths.get_output_directory = lambda: str(output_dir)
    folder_paths.get_input_directory = lambda: str(input_dir)
    folder_paths.get_full_path = lambda category, name: None
    folder_paths.get_filename_list = lambda category: []
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(routes=web.RouteTableDef())
    )
    nodes = types.ModuleType("nodes")
    nodes.NODE_CLASS_MAPPINGS = {}
    saver = types.ModuleType("h3_studio_ext.h3_video_save")
    saver.H3SaveVideo = type("H3SaveVideo", (), {})
    sys.modules.update(
        {
            "folder_paths": folder_paths,
            "server": server,
            "nodes": nodes,
            "h3_studio_ext.h3_video_save": saver,
        }
    )
    return server.PromptServer.instance.routes


def load_extension():
    spec = importlib.util.spec_from_file_location(
        "h3_studio_ext", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["h3_studio_ext"] = module
    spec.loader.exec_module(module)
    return module


def enum_options(object_info, node, field):
    spec = object_info.get(node, {}).get("input", {}).get("required", {}).get(field)
    if not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    return (
        spec[1].get("options", [])
        if len(spec) > 1 and isinstance(spec[1], dict)
        else []
    )


def translate_graph(graph, uploaded):
    """Swap the local-only save node and point media loaders at Cloud uploads."""
    result = json.loads(json.dumps(graph))
    token = None
    for node_id, node in result.items():
        inputs = node.get("inputs", {})
        if node.get("class_type") == "H3SaveVideo":
            match = PREFIX_RE.match(str(inputs.get("filename_prefix", "")))
            if node_id != SAVE_NODE or not match:
                raise ValueError("Unexpected H3 save node in the submitted graph.")
            token = match.group(1)
            node["class_type"] = "SaveVideo"
            inputs.update(format="auto", codec="auto")
        field = MEDIA_INPUTS.get(node.get("class_type"))
        if field and inputs.get(field) in uploaded:
            inputs[field] = uploaded[inputs[field]]
    if not token:
        raise ValueError("The graph has no H3 Studio save node.")
    return result, token


def local_media_names(graph):
    names = []
    for node in graph.values():
        field = MEDIA_INPUTS.get(node.get("class_type"))
        value = node.get("inputs", {}).get(field) if field else None
        if isinstance(value, str) and LOCAL_INPUT_RE.match(value):
            names.append(value)
    return names


def append_line(path, line):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def find_output(outputs, preview):
    node = (outputs or {}).get(SAVE_NODE) or {}
    for key in ("videos", "images", "gifs", "video"):
        items = node.get(key)
        items = items if isinstance(items, list) else [items] if items else []
        for item in items:
            if isinstance(item, dict) and item.get("filename"):
                return item
    if (
        isinstance(preview, dict)
        and preview.get("filename")
        and str(preview.get("nodeId")) == SAVE_NODE
    ):
        return preview
    return None


class Bridge:
    def __init__(self, key, data_dir, vae_fix):
        self.key = key
        self.data_dir = data_dir
        self.vae_fix = vae_fix
        self.video_dir = data_dir / "output" / "video"
        self.input_dir = data_dir / "input"
        self.session = None
        self.object_info = {}
        self.object_info_at = 0
        self.system = {}
        self.jobs = {}
        self.uploads = {}
        self.downloads = {}

    @property
    def headers(self):
        return {"X-API-Key": self.key}

    async def start(self, app):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))
        await self.refresh_catalog()
        app["watcher"] = asyncio.create_task(self.watch_jobs())

    async def stop(self, app):
        app["watcher"].cancel()
        await self.session.close()

    async def cloud_json(self, method, path, **kwargs):
        async with self.session.request(
            method, CLOUD + path, headers=self.headers, **kwargs
        ) as response:
            text = await response.text()
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                body = {"error": text[:300]}
            return response.status, body

    async def refresh_catalog(self):
        status, info = await self.cloud_json("GET", "/api/object_info")
        if status == 200 and isinstance(info, dict):
            self.object_info, self.object_info_at = info, time.time()
        status, stats = await self.cloud_json("GET", "/api/system_stats")
        if status == 200:
            self.system = stats.get("system", {})

    async def catalog(self):
        if time.time() - self.object_info_at > 3600:
            try:
                await self.refresh_catalog()
            except aiohttp.ClientError:
                pass
        return self.object_info

    async def readiness(self, request):
        info = await self.catalog()
        models = {
            key: filename in enum_options(info, node, field)
            for key, (node, field, filename) in REQUIRED_MODELS.items()
        }
        available = {name: name in info for name in READINESS_NODES}
        available["H3SaveVideo"] = available["SaveVideo"]
        return web.json_response(
            {
                "models": models,
                "nodes": available,
                "quality": {"h3_vae_tile_fix": self.vae_fix},
                "backend": "comfy-cloud",
            }
        )

    async def loras(self, request):
        names = enum_options(await self.catalog(), "LoraLoaderModelOnly", "lora_name")
        return web.json_response(
            {"items": sorted(n for n in names if H3_LORA_RE.search(n))}
        )

    async def system_stats(self, request):
        version = self.system.get("comfyui_version", "unknown")
        return web.json_response(
            {
                "system": self.system,
                "devices": [
                    {"name": "cloud:Comfy Cloud · ComfyUI " + version, "type": "cloud"}
                ],
            }
        )

    async def upload(self, name):
        if name in self.uploads:
            return self.uploads[name]
        path = self.input_dir / name
        if not path.is_file():
            raise ValueError(
                "Uploaded reference is no longer on this computer: " + name
            )
        form = aiohttp.FormData()
        form.add_field("image", path.read_bytes(), filename=name)
        form.add_field("type", "input")
        status, body = await self.cloud_json("POST", "/api/upload/image", data=form)
        if status != 200 or not body.get("name"):
            raise ValueError(
                "Comfy Cloud rejected " + name + ": " + json.dumps(body)[:200]
            )
        cloud_name = (
            body["name"]
            if not body.get("subfolder")
            else body["subfolder"] + "/" + body["name"]
        )
        self.uploads[name] = cloud_name
        return cloud_name

    async def prompt(self, request):
        data = await request.json()
        graph = data.get("prompt")
        if not isinstance(graph, dict):
            return web.json_response({"error": "Missing prompt graph."}, status=400)
        try:
            uploaded = {
                name: await self.upload(name) for name in local_media_names(graph)
            }
            cloud_graph, token = translate_graph(graph, uploaded)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        status, body = await self.cloud_json(
            "POST",
            "/api/prompt",
            json={
                "prompt": cloud_graph,
                "client_id": data.get("client_id"),
                "extra_data": {"api_key_comfy_org": self.key},
            },
        )
        if status == 200 and body.get("prompt_id"):
            self.jobs[body["prompt_id"]] = {
                "token": token,
                "state": "submitted",
                "submitted": time.time(),
            }
            print(f"[h3-cloud] submitted {body['prompt_id']} token {token}", flush=True)
        return web.json_response(body, status=status)

    def local_file(self, prompt_id):
        return self.video_dir / f"h3_studio_{self.jobs[prompt_id]['token']}_00001_.mp4"

    def local_output(self, prompt_id):
        return {
            "filename": self.local_file(prompt_id).name,
            "subfolder": "video",
            "type": "output",
        }

    def ensure_download(self, prompt_id, remote):
        task = self.downloads.get(prompt_id)
        if not task:
            task = asyncio.create_task(self.download(prompt_id, remote))
            self.downloads[prompt_id] = task
        return task

    async def download(self, prompt_id, remote):
        job = self.jobs[prompt_id]
        job["state"] = "downloading"
        target = self.local_file(prompt_id)
        scratch = target.with_suffix(".part")
        params = {
            "filename": remote["filename"],
            "subfolder": remote.get("subfolder", ""),
            "type": remote.get("type", "output"),
        }
        try:
            async with self.session.get(
                CLOUD + "/api/view",
                params=params,
                headers=self.headers,
                allow_redirects=False,
            ) as response:
                location = response.headers.get("Location")
                if response.status != 302 or not location:
                    raise RuntimeError(
                        f"Cloud download returned HTTP {response.status}"
                    )
            async with self.session.get(location) as response:
                response.raise_for_status()
                stream = await asyncio.to_thread(open, scratch, "wb")
                try:
                    async for chunk in response.content.iter_chunked(1 << 20):
                        await asyncio.to_thread(stream.write, chunk)
                finally:
                    await asyncio.to_thread(stream.close)
            os.replace(scratch, target)
            job["state"] = "done"
            print(f"[h3-cloud] saved {target.name}", flush=True)
            asyncio.create_task(self.log_usage(prompt_id))
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            OSError,
            RuntimeError,
        ) as exc:
            job.update(
                state="failed", error="Saving the Cloud video failed: " + str(exc)[:200]
            )
            self.downloads.pop(prompt_id, None)
            if scratch.exists():
                scratch.unlink()
            print(f"[h3-cloud] {job['error']}", flush=True)

    async def log_usage(self, prompt_id):
        """Record execution time and billed GPU seconds or credits for each job."""
        await asyncio.sleep(8)
        record = {"prompt_id": prompt_id, "file": self.local_file(prompt_id).name}
        try:
            _, job = await self.cloud_json("GET", f"/api/jobs/{prompt_id}")
            start, end = job.get("execution_start_time"), job.get("execution_end_time")
            if start and end:
                record["exec_seconds"] = round((end - start) / 1000, 1)
            async with self.session.get(
                BILLING + "/customers/events",
                headers=self.headers,
                params={"limit": "30", "page": "1"},
            ) as response:
                events = (await response.json()).get("events", [])
            for event in events:
                params = event.get("params", {})
                if params.get("job_id") == prompt_id:
                    for field in ("gpu_seconds", "gpu_type", "credits_used"):
                        if params.get(field) is not None:
                            record[field] = params[field]
        except (aiohttp.ClientError, ValueError) as exc:
            record["usage_error"] = str(exc)[:120]
        await asyncio.to_thread(
            append_line, self.video_dir / ".h3-cloud-usage.jsonl", json.dumps(record)
        )
        print("[h3-cloud] usage " + json.dumps(record), flush=True)

    async def check_job(self, prompt_id):
        job = self.jobs[prompt_id]
        if job["state"] not in ("submitted", "running"):
            return
        status, body = await self.cloud_json("GET", f"/api/jobs/{prompt_id}")
        if status != 200:
            return
        state = body.get("status")
        if state == "in_progress":
            job["state"] = "running"
        elif state in ("completed", "success"):
            remote = find_output(body.get("outputs"), body.get("preview_output"))
            if remote:
                self.ensure_download(prompt_id, remote)
            else:
                job.update(
                    state="failed", error="Comfy Cloud finished without a saved video."
                )
        elif state in ("failed", "cancelled"):
            error = body.get("execution_error") or {}
            job.update(
                state="failed",
                error=error.get("exception_message")
                or body.get("error_message")
                or "Comfy Cloud job " + state,
            )

    async def watch_jobs(self):
        """Finish downloads even when no browser tab is open."""
        while True:
            await asyncio.sleep(5)
            for prompt_id in list(self.jobs):
                try:
                    await self.check_job(prompt_id)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    pass

    async def history_item(self, request):
        prompt_id = request.match_info["prompt_id"]
        job = self.jobs.get(prompt_id)
        if not job:
            return web.json_response({})
        if job["state"] == "done":
            return web.json_response(
                {
                    prompt_id: {
                        "outputs": {
                            SAVE_NODE: {"videos": [self.local_output(prompt_id)]}
                        },
                        "status": {
                            "status_str": "success",
                            "completed": True,
                            "messages": [],
                        },
                    }
                }
            )
        if job["state"] == "failed":
            return web.json_response(
                {
                    prompt_id: {
                        "outputs": {},
                        "status": {
                            "status_str": "error",
                            "completed": False,
                            "messages": [
                                ["execution_error", {"exception_message": job["error"]}]
                            ],
                        },
                    }
                }
            )
        return web.json_response({})

    async def history(self, request):
        return web.json_response({})

    async def queue(self, request):
        if request.method == "POST":
            status, body = await self.cloud_json(
                "POST", "/api/queue", json=await request.json()
            )
            return web.json_response(body, status=status)
        status, body = await self.cloud_json("GET", "/api/queue")
        if status != 200:
            return web.json_response(body, status=status)
        running = list(body.get("queue_running") or [])
        listed = {
            item[1]
            for item in running + list(body.get("queue_pending") or [])
            if isinstance(item, list) and len(item) > 1
        }
        for prompt_id, job in self.jobs.items():
            if job["state"] == "downloading" and prompt_id not in listed:
                running.append([0, prompt_id, {}, {}, []])
        body["queue_running"] = running
        return web.json_response(body)

    async def interrupt(self, request):
        status, body = await self.cloud_json("POST", "/api/interrupt", json={})
        return web.json_response(body, status=status)

    async def view(self, request):
        name, subfolder = (
            request.query.get("filename", ""),
            request.query.get("subfolder", ""),
        )
        base = (self.data_dir / "output").resolve()
        path = (base / subfolder / name).resolve()
        if (
            os.path.basename(name) != name
            or base not in path.parents
            or not path.is_file()
        ):
            return web.Response(status=404)
        return web.FileResponse(path)

    async def websocket(self, request):
        client = web.WebSocketResponse()
        await client.prepare(request)
        client_id = request.query.get("clientId", "")
        url = CLOUD.replace("https", "wss") + "/ws"
        try:
            upstream = await self.session.ws_connect(
                url, params={"clientId": client_id, "token": self.key}
            )
        except aiohttp.ClientError:
            await client.close()
            return client
        relay = asyncio.create_task(self.relay_up(client, upstream))
        try:
            async for message in upstream:
                if message.type == aiohttp.WSMsgType.BINARY:
                    await client.send_bytes(message.data)
                elif message.type == aiohttp.WSMsgType.TEXT:
                    text = await self.rewrite(message.data)
                    if text:
                        await client.send_str(text)
        except (ConnectionResetError, RuntimeError):
            pass
        finally:
            relay.cancel()
            await upstream.close()
            await client.close()
        return client

    async def relay_up(self, client, upstream):
        async for message in client:
            if message.type == aiohttp.WSMsgType.TEXT:
                await upstream.send_str(message.data)

    async def rewrite(self, text):
        """Hold the save node's result until the MP4 is in the local library."""
        try:
            message = json.loads(text)
        except ValueError:
            return text
        data = message.get("data") or {}
        prompt_id = data.get("prompt_id")
        if (
            message.get("type") != "executed"
            or prompt_id not in self.jobs
            or str(data.get("node")) != SAVE_NODE
        ):
            return text
        remote = find_output({SAVE_NODE: data.get("output") or {}}, None)
        if not remote:
            return None
        await self.ensure_download(prompt_id, remote)
        if self.jobs[prompt_id]["state"] != "done":
            return None
        data["output"] = {"videos": [self.local_output(prompt_id)]}
        return json.dumps(message)


def build_app(key, data_dir, vae_fix):
    routes = install_shims(data_dir)
    load_extension()
    bridge = Bridge(key, data_dir, vae_fix)
    app = web.Application(client_max_size=512 << 20)
    replaced = {("GET", "/h3_studio/readiness"), ("GET", "/h3_studio/loras")}
    app.add_routes(
        [
            route
            for route in routes
            if (getattr(route, "method", ""), getattr(route, "path", ""))
            not in replaced
        ]
    )
    web_dir = ROOT / "web"
    app.add_routes(
        [
            web.get(
                "/", lambda request: web.HTTPFound("/extensions/h3_studio/index.html")
            ),
            web.get("/h3_studio/readiness", bridge.readiness),
            web.get("/h3_studio/loras", bridge.loras),
            web.get("/system_stats", bridge.system_stats),
            web.post("/prompt", bridge.prompt),
            web.get("/queue", bridge.queue),
            web.post("/queue", bridge.queue),
            web.post("/interrupt", bridge.interrupt),
            web.get("/history", bridge.history),
            web.get("/history/{prompt_id}", bridge.history_item),
            web.get("/view", bridge.view),
            web.get("/ws", bridge.websocket),
            web.static("/extensions/h3_studio", web_dir),
        ]
    )
    app.on_startup.append(bridge.start)
    app.on_cleanup.append(bridge.stop)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="dotenv file holding COMFYUI_API_KEY")
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / ".cloud-data"),
        help="Local library and upload folder",
    )
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument(
        "--accept-cloud-vae",
        action="store_true",
        help="Allow renders although Cloud's ComfyUI lacks the Sep 22 H3 VAE tile fix",
    )
    args = parser.parse_args()
    key = load_key(args.env_file)
    app = build_app(key, pathlib.Path(args.data_dir).resolve(), args.accept_cloud_vae)
    print(
        f"[h3-cloud] H3 Higgsfield: http://127.0.0.1:{args.port}/extensions/h3_studio/index.html",
        flush=True,
    )
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
