# CPU browser acceptance

Run `node tests/e2e/browser-acceptance.cjs` from the repository with Playwright Chromium, Python, aiohttp, Pillow and FFmpeg already available. The fixture binds only to 127.0.0.1:18768 and never loads model weights or contacts ComfyUI.

Optional environment variables:

- `H3_TEST_PYTHON`: Python executable (default `python`).
- `H3_TEST_PLAYWRIGHT`: Playwright package path when it is not on Node's module search path.
- `H3_TEST_SCREENSHOT`: destination for a full-page screenshot after the journeys pass.

The browser uses the real project, asset, Qwen import, job and cancellation routes. Only ComfyUI readiness, model availability and queue execution are mocked. It checks frame/keyframe graph submission, saved input restoration, nonempty Qwen media restoration, neutral custom references, timed audio/video guide bindings, confirmed cancellation, imported-video continuation graph assembly, request-id recovery after network response loss, and cancellation both before backend submission and after queue acceptance but before acknowledgement. Server-resolved frame fitting is verified from the actual saved input image dimensions. This is wiring acceptance, not a model render or visual quality check.



The image workspace has a separate acceptance script: `node tests/e2e/image-browser-acceptance.cjs`. It uses port 18769 and checks actual submitted Qwen create/edit graphs, zero seed preservation, valid generation epochs, and the disabled Generate button with an explicit standby reason. Set `H3_TEST_IMAGE_SCREENSHOT` to save its final standby view. It never executes the captured model workflow.


Image cancellation removes only the identified pending queue entry and verifies removal. Running images stay tracked because this version cannot safely interrupt a shared ComfyUI worker. The image acceptance script verifies this refusal, retained inputs after a lost response, recovery using the save-node token, and duplicate-click protection while readiness is pending.

`node tests/e2e/continuation-browser-acceptance.cjs` uses port18770 for the external continuation-source journey: real25fps MP4 normalization, project restore, storyboard plus timed image guide, source/mode/canvas guards and explicit re-encoding. The captured graph is never executed.
