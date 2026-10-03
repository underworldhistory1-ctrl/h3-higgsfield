# Input lifetime and shared Qwen paths — 2026-10-03

Observed: failed workflow LoadImage references pointed to deleted temporary uploads; its asset_leases retained only the final guide. A delayed previous-job response could rewrite running state and clean the mutable global uploads array after a new generation started. Polling now captures generation epoch and run identity and checks them after asynchronous boundaries, including request recovery and queue/history fallback. Completion captures its own upload list before subsequent awaits and never drains a newer generation. Two browser regressions cover old terminal responses and delayed completion-library responses. Generate rejects recovery of a previous render before entering upload preparation.

Qwen remained available on the original server. V2 configured the same registered extra model paths, but profile readiness checked only its empty local models folder. Readiness now resolves model files through the registered folder_paths, retaining exact file-size validation and local-only checks for offline tooling. No weight files or original server files are changed.

Validation: 99 Python tests, 29 JavaScript contracts, browser video17/image7/continuation8 checks with no page errors. This incident fix does not claim a new GPU model render. Deployment affects only idle V2; original H3 and LTX remain untouched.
