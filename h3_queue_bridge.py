"""Asynchronous bridge to this ComfyUI process, never an arbitrary remote host."""
from aiohttp import ClientSession, ClientTimeout


class ComfyQueueBridge:
    def __init__(self, port_getter, routes_getter=None):
        self.port_getter = port_getter
        self.routes_getter = routes_getter

    async def request(self, path, body=None):
        port = self.port_getter()
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise RuntimeError("ComfyUI has not started its HTTP listener")
        async with ClientSession(timeout=ClientTimeout(total=45)) as client:
            method = "GET" if body is None else "POST"
            async with client.request(method, f"http://127.0.0.1:{port}{path}", json=body) as response:
                if response.status >= 500:
                    raise RuntimeError("ComfyUI acknowledgement unavailable")
                if response.status >= 400:
                    raise ValueError((await response.text())[:600])
                if path in ("/queue", "/interrupt") and body is not None:
                    await response.read()
                    return {"ok": True}
                return await response.json()

    async def submit_prompt(self, spec, extra_data):
        workflow = spec.get("workflow")
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("A compiled workflow is required")
        return await self.request("/prompt", {"prompt": workflow,
                "client_id": spec.get("client_id"), "extra_data": {"extra_pnginfo": extra_data}})

    async def get_queue(self):
        return await self.request("/queue")

    async def get_history(self, prompt_id=None):
        return await self.request("/history" + ("/" + prompt_id if prompt_id else ""))

    async def delete_from_queue(self, prompt_ids):
        return await self.request("/queue", {"delete": prompt_ids})

    async def interrupt_owned(self, prompt_id):
        # Older ComfyUI versions ignore prompt_id and interrupt a foreign job.
        # Confirm this process implements targeted interruption before using it.
        import inspect
        supported = False
        for route in self.routes_getter() if self.routes_getter else []:
            if getattr(route, "path", None) == "/interrupt":
                try:
                    source = inspect.getsource(route.handler)
                    supported = "json_data.get('prompt_id')" in source and "currently_running" in source
                except (TypeError, OSError):
                    pass
        if not supported:
            raise ValueError("This ComfyUI version cannot safely interrupt a specific running prompt. Wait for completion.")
        return await self.request("/interrupt", {"prompt_id": prompt_id})
