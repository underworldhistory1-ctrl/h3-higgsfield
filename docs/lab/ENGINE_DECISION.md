# H3 Studio Lab — Continuation Engine Evaluation & Decision

## Decision Summary

- **Selected Candidate:** `seitanism/ComfyUI-H3-Motion-Context-MultiRef` (commit `361624fb406b63eb6694442eac6c895fc1533a70`)
- **Evaluated Alternative:** `tritant/ComfyUI_MiniMax_H3_Extender` (commit `67d3c127fcae80d2e56264fb64c450186b55fd78`)
- **Status:** Primary adapter implemented (`h3_lab/continuation.py` and `web/h3/graph-builder.js`). Ready for remote GPU validation on the authorized online test server.

---

## Comparison Matrix

| Evaluation Criteria | MultiRef Candidate (`seitanism`) | Extender Alternative (`tritant`) |
|---|---|---|
| **License** | GPL-3.0 (Adapter pattern used; no code copy into MIT repo) | Apache-2.0 |
| **Mask Architecture** | Separate video and audio latent masks | Monolithic / layout patches |
| **Core Monkeypatching** | None; uses registered ComfyUI custom node contracts | Invasively patches PackedLayout and reasserts patches |
| **Native Guide Interop** | Full compatibility with native `MiniMaxH3AddGuide` | Replaces conditioning pipeline |
| **Context Lengths** | Verified `39 + 51*k` (default 39 frames) | Variable window heuristic |
| **Audio Latent Handling** | Preserves 40Hz audio latents with feathering ticks | Video-first, audio secondary |

---

## Technical Rationale

1. **Isolation from ComfyUI Core**:
   `tritant` injects runtime monkeypatches into `MiniMaxH3.extra_conds` and layout logic that persist across workflow runs, potentially corrupting standard FL2VA / Ref2VA renders in the same ComfyUI process. `MultiRef` registers standalone nodes (`MiniMaxH3GeneratedAVMaskedContext`, `MiniMaxH3ExistingVideoMaskedContext`, `MiniMaxH3AssembleExtension`) that leave base ComfyUI nodes completely unpatched.

2. **Native Temporal Guide Compatibility**:
   `MultiRef` operates directly on target latent tensors while allowing native `MiniMaxH3AddGuide` nodes to chain positive conditioning independently. This enables combining image/video references with start/end/interior temporal keyframe guides.

3. **Licensing Isolation**:
   To preserve the MIT license of `minimax-h3-higgsfield`, MultiRef is treated as an external pinned dependency in `lab/dependencies.lock.json`. No GPL implementation code is copied into the lab codebase; our integration uses clean schema-driven graph builder bindings.

---

## Fallback & Isolation Plan

If online GPU test server evaluation reveals unforeseen tensor regressions with `MultiRef`, the fallback route is to test `tritant` in a clean, isolated virtualenv on the test server. Under no circumstances will both candidate engines be installed simultaneously in the same environment.
