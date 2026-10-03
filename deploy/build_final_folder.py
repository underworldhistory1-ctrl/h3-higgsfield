#!/usr/bin/env python3
"""Build one immutable, credential-free H3 Studio handoff folder and zip."""

import argparse
import hashlib
import json
import pathlib
import shutil
import zipfile


SOURCE_FILES = (
    "AGENTS.md", "START_HERE_AR.md", "LICENSE", "README.md", "install.sh", "install.ps1", "__init__.py", "h3_video_save.py", "qwen_image.py",
    "Dockerfile.salad", ".dockerignore",
    "web/index.html", "web/studio.js", "web/image.html", "web/image-studio.js",
    "deploy/bootstrap_h3_server.sh", "deploy/download_h3_models.sh",
    "deploy/download_h3_models.py", "deploy/download_qwen_image_models.py", "deploy/install_windows.py",
    "deploy/download_optional_loras.py", "deploy/make_h3_landing.py",
    "deploy/provision_h3.py", "deploy/activate_h3.py", "deploy/verify_h3_server.py",
    "deploy/backup_h3_library.py", "deploy/restore_h3_library.py",
    "deploy/build_final_folder.py", "deploy/CLOUD_BOOTSTRAP_AR.md", "deploy/SALAD_DEPLOYMENT.md",
    "deploy/salad/entrypoint.sh", "deploy/salad/nginx.conf.template",
    "scripts/verify_h3_video.py", "docs/UX_FLOW.md", "docs/GRAPH_MAP.md",
    "docs/FINAL_AUDIT_AR.md", "docs/COMPATIBILITY_MATRIX_AR.md", "docs/social-preview.png",
    "docs/demo/interface-references.png", "docs/demo/interface-motioncache.png",
    "docs/demo/spectrum-preview.jpg", "docs/demo/motioncache-preview.jpg",
    "docs/demo/spectrum-no-lora.mp4", "docs/demo/motioncache-no-lora.mp4",
    "workflows/h3_t2v_ui.json", "workflows/h3_t2v_api.json",
    "workflows/h3_t2v_smoke_ui.json", "workflows/h3_t2v_smoke_api.json",
    "tests/__init__.py", "tests/test_deployment_contract.py", "tests/test_image_ui_contract.py",
    "tests/test_qwen_downloader.py", "tests/test_qwen_image.py", "tests/test_qwen_routes.py",
)

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_FILES = tuple(dict.fromkeys((*SOURCE_FILES, *(SOURCE_ROOT / "deploy/v2_runtime_files.txt").read_text(encoding="utf-8").splitlines(), *(p.relative_to(SOURCE_ROOT).as_posix() for p in (SOURCE_ROOT / "tests").rglob("*") if p.suffix in {".py", ".cjs"}))))


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--library-backup", type=pathlib.Path)
    parser.add_argument("--update", action="store_true", help="Refresh an existing H3 Studio release without deleting it")
    args = parser.parse_args()
    source = pathlib.Path(__file__).resolve().parent.parent
    output = args.output.resolve()
    if output.exists():
        manifest_path = output / "MANIFEST_SHA256.json"
        if not args.update or not output.is_dir() or not manifest_path.is_file():
            parser.error(f"Output already exists; choose a new path or use --update: {output}")
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parser.error("Existing release manifest is unreadable")
        if existing.get("project") != "H3 Studio":
            parser.error("Existing folder is not a H3 Studio release")
    missing = [name for name in SOURCE_FILES if not (source / name).is_file()]
    if missing:
        parser.error("Missing source files: " + ", ".join(missing))
    if args.library_backup and not args.library_backup.is_file():
        parser.error(f"Library backup not found: {args.library_backup}")

    source_target = output / "source" / "h3-studio-src"
    source_target.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        target = source_target / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
        if target.suffix == ".sh" or name == "deploy/v2_runtime_files.txt":
            target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
    for original, target in (
        (source / "AGENTS.md", output / "AGENTS.md"),
        (source / "START_HERE_AR.md", output / "START_HERE_AR.md"),
        (source / "deploy/provision_h3.py", output / "provision_h3.py"),
        (source / "deploy/verify_h3_server.py", output / "verify_h3_server.py"),
    ):
        shutil.copy2(original, target)
    (output / "requirements-installer.txt").write_text("paramiko>=3.4,<5\n", encoding="utf-8")

    cloud_zip = output / "h3-cloud-setup.zip"
    with zipfile.ZipFile(cloud_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in SOURCE_FILES:
            archive.write(source_target / name, "h3-studio-src/" + name)
    with zipfile.ZipFile(cloud_zip) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Corrupt setup archive")

    if args.library_backup:
        shutil.copy2(args.library_backup, output / "library-backup.zip")
        with zipfile.ZipFile(args.library_backup) as archive:
            snapshot = json.loads(archive.read("snapshot.json"))
        (output / "LIBRARY_SNAPSHOT_AR.md").write_text(
            "# لقطة مكتبة الفيديوهات\n\n"
            f"عدد الفيديوهات المكتملة في اللقطة: **{snapshot.get('video_count', 0)}**. "
            f"وقت أخذها UTC: `{snapshot.get('created_at_utc', 'غير مسجل')}`.\n\n"
            "تحتوي اللقطة الفيديوهات وملفات الإعدادات/التوقيت والمفضلة؛ لا تحتوي أي رندر "
            "كان ما زال شغالًا وقت أخذها. لاستعادتها على خادم جديد أضف "
            "`--library-backup library-backup.zip` لأمر `provision_h3.py`. "
            "سيضيف الملفات الناقصة فقط، ولن يستبدل فيديو موجودًا مختلفًا.\n",
            encoding="utf-8",
        )
    files = sorted(p for p in output.rglob("*")
                   if p.is_file() and p.name != "MANIFEST_SHA256.json")
    manifest = {
        "project": "H3 Studio", "release": "2026-10-03-v2",
        "tested_gpu": "NVIDIA GeForce RTX 5090, 32 GB",
        "tested_comfy_revision": "3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251",
        "prepared_comfy_revision": "3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251",
        "h3_vae_tile_fix_pr": 16436,
        "qwen_image_profiles": ["int8", "bf16"],
        "qwen_model_revision": "9a44dbdb47cefd046be9c0a13476192f34c8db8e",
        "spectrum_revision": "5161f0457bc8c52535212d6783eee73f439e1537",
        "motioncache_revision": "bc2894102b2486661884371259a27080b0b137bf",
        "context_engine_revision": "361624fb406b63eb6694442eac6c895fc1533a70",
        "context_engine_vendored": False,
        "contains_model_weights": False,
        "files": {p.relative_to(output).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256(p)}
                  for p in files},
    }
    (output / "MANIFEST_SHA256.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    outer = output.with_suffix(".zip")
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(p for p in output.rglob("*") if p.is_file()):
            archive.write(file, output.name + "/" + file.relative_to(output).as_posix())
    with zipfile.ZipFile(outer) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Corrupt final archive")
    print(f"FINAL_FOLDER_OK files={len(files)} folder={output} archive={outer} archive_sha256={sha256(outer)}")


if __name__ == "__main__":
    main()
