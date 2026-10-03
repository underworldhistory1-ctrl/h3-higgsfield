"""Create disposable canvas-fit inputs from immutable owned image assets."""
import shutil
import uuid
from .paths import owned_path


def resolve_image(source, input_root, asset_id, options):
    from PIL import Image, ImageOps
    from pathlib import Path
    Path(input_root).resolve().mkdir(parents=True, exist_ok=True)
    fit = options.get("fit", "preserve")
    if fit not in ("preserve", "crop", "contain"):
        raise ValueError("fit must be preserve, crop or contain")
    width, height = options.get("width"), options.get("height")
    if fit != "preserve":
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 32 or value > 2048 or value % 32 for value in (width, height)):
            raise ValueError("Canvas dimensions must be multiples of 32 between 32 and 2048")
    try:
        original = Image.open(source)
    except Image.DecompressionBombError as error:
        raise ValueError("Image exceeds 50 million pixels") from error
    with original:
        if original.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("Only PNG, JPEG and WebP images are supported")
        if original.width * original.height > 50_000_000:
            raise ValueError("Image exceeds 50 million pixels")
        original.load()
        image = ImageOps.exif_transpose(original)
        if fit == "preserve":
            filename = f"h3_studio_kf_lab_{asset_id}{source.suffix}"
            target = owned_path(input_root, filename, require_file=False)
            part = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
            try:
                shutil.copy2(source, part)
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
            width, height = image.size
        else:
            filename = f"h3_studio_kf_lab_{asset_id}_{width}x{height}_{fit}.png"
            target = owned_path(input_root, filename, require_file=False)
            if fit == "crop":
                image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
            else:
                fitted = ImageOps.contain(image, (width, height), method=Image.Resampling.LANCZOS)
                image = Image.new("RGB", (width, height), "black")
                mask = fitted.getchannel("A") if "A" in fitted.getbands() else None
                image.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2), mask)
            part = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
            try:
                image.save(part, format="PNG")
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
    return {"filename": filename, "width": width, "height": height, "fit": fit}
