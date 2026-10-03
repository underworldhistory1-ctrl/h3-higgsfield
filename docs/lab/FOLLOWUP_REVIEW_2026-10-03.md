# V2 follow-up review — 2026-10-03

Reviewed prompt parsing and mentions, mode switching, project hydration, sampling settings, guides, LoRA selection, jobs/cancellation and portable context imports.

Fixed reproducible defects:
- Repeated ordinary prose headings no longer enter structured parsing in guided Text/Frames/References prompts. Native structured prompts remain validated.
- Restored LoRA configuration is consumed once. Later user toggle/strength edits, including enabling a LoRA after an empty saved selection, survive connection checks and reach the graph. Missing-file messages now distinguish an actual missing name from a changed selection.
- Project drafts persist and restore both fitImages and fitFrames flags; older projects default to enabled.
- Context-bundle checksum verification uses bounded streaming SHA256, compatible with deployed Python 3.10 instead of Python 3.11-only hashlib.file_digest.

Validation: 93 Python tests, 29 JavaScript contract tests; existing 28 CPU browser journeys passed. Video browser suite additionally verifies fit flags and LoRA refresh/submission regressions with no page errors. Reviewers found no further high-confidence blockers within inspected scope. No GPU workflow executed by this review. Artistic accuracy and actual GPU execution remain separate from CPU wiring acceptance.

Deployment only targets the private V2 repository. Production H3 and LTX remain untouched.
