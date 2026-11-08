"""Image resize Lambda function.

Downloads an image from the originals S3 bucket, creates a thumbnail with
Pillow, and uploads it to the resized S3 bucket.

The function is invoked by the Step Functions state machine and always
returns a result object with a ``status`` field ("SUCCEEDED" or "FAILED").
The state machine's Choice state uses that field to decide whether the
workflow ends in its Success or Fail state.
"""

import io
import logging
import os
import urllib.parse

import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

SOURCE_BUCKET = os.environ.get("SOURCE_BUCKET", "")
DESTINATION_BUCKET = os.environ.get("DESTINATION_BUCKET", "")
THUMBNAIL_SIZE = int(os.environ.get("THUMBNAIL_SIZE", "128"))
THUMBNAIL_PREFIX = os.environ.get("THUMBNAIL_PREFIX", "thumbnails/")
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))

STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"

# File extension -> (Pillow format name, Content-Type)
SUPPORTED_FORMATS = {
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".png": ("PNG", "image/png"),
    ".gif": ("GIF", "image/gif"),
    ".webp": ("WEBP", "image/webp"),
}

# Created once per execution environment and reused across invocations.
s3 = boto3.client("s3")


class ImageProcessingError(Exception):
    """An expected, non-retryable problem with the requested image."""


def parse_event(event):
    """Return (bucket, key) from any of the supported event shapes.

    Supported shapes:
      * API Gateway / manual input: {"bucket": "...", "key": "..."}
        ("bucket" is optional and defaults to the originals bucket)
      * EventBridge "Object Created" event from S3: {"detail": {...}}
      * Classic S3 event notification: {"Records": [{"s3": {...}}]}
    """
    if not isinstance(event, dict):
        raise ImageProcessingError("Event must be a JSON object.")

    if "detail" in event:  # EventBridge
        detail = event["detail"]
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
    elif event.get("Records"):  # S3 notification
        record = event["Records"][0]["s3"]
        bucket = record["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["object"]["key"])
    else:  # API Gateway / manual
        bucket = event.get("bucket") or SOURCE_BUCKET
        key = event.get("key")

    if not bucket or not key:
        raise ImageProcessingError("Both 'bucket' and 'key' are required.")
    return bucket, key


def get_format(key):
    """Return (Pillow format, Content-Type) for a key, or raise if unsupported."""
    extension = os.path.splitext(key)[1].lower()
    if extension not in SUPPORTED_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_FORMATS))
        raise ImageProcessingError(
            f"Unsupported file type '{extension or 'none'}'. Supported: {supported}"
        )
    return SUPPORTED_FORMATS[extension]


def build_thumbnail_key(key):
    """photos/cat.jpg -> thumbnails/photos/cat-thumb.jpg"""
    base, extension = os.path.splitext(key)
    return f"{THUMBNAIL_PREFIX}{base}-thumb{extension.lower()}"


def resize_image(image_bytes, size, image_format):
    """Resize image bytes so the longest side is at most ``size`` pixels.

    Keeps the aspect ratio, honours EXIF orientation, and returns
    (thumbnail_bytes, (width, height)).
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((size, size))

            # JPEG has no alpha channel or palette support.
            if image_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format=image_format)
            return buffer.getvalue(), image.size
    except (UnidentifiedImageError, OSError) as error:
        raise ImageProcessingError(f"File is not a valid image: {error}") from error


def lambda_handler(event, context):
    """Entry point: resize one image and report the outcome."""
    logger.info("Received event: %s", event)
    bucket = key = None

    try:
        if not DESTINATION_BUCKET:
            raise ImageProcessingError("DESTINATION_BUCKET is not configured.")

        bucket, key = parse_event(event)

        # Only read from the originals bucket, so the API cannot be used to
        # read arbitrary buckets the function might have access to.
        if SOURCE_BUCKET and bucket != SOURCE_BUCKET:
            raise ImageProcessingError(
                f"Images must come from the originals bucket '{SOURCE_BUCKET}'."
            )

        image_format, content_type = get_format(key)

        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            raise ImageProcessingError(
                f"Object s3://{bucket}/{key} could not be found."
            ) from error
        if head["ContentLength"] > MAX_IMAGE_BYTES:
            raise ImageProcessingError(
                f"Image is larger than the {MAX_IMAGE_BYTES} byte limit."
            )

        original = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        thumbnail, (width, height) = resize_image(original, THUMBNAIL_SIZE, image_format)

        thumbnail_key = build_thumbnail_key(key)
        s3.put_object(
            Bucket=DESTINATION_BUCKET,
            Key=thumbnail_key,
            Body=thumbnail,
            ContentType=content_type,
        )

        logger.info("Created s3://%s/%s (%dx%d)", DESTINATION_BUCKET, thumbnail_key, width, height)
        return {
            "status": STATUS_SUCCEEDED,
            "source": {"bucket": bucket, "key": key},
            "thumbnail": {
                "bucket": DESTINATION_BUCKET,
                "key": thumbnail_key,
                "width": width,
                "height": height,
            },
        }

    except ImageProcessingError as error:
        # Expected problem (bad input, missing file, not an image): report it
        # so the Choice state routes the workflow to the Fail state.
        logger.warning("Resize failed for %s/%s: %s", bucket, key, error)
        return {
            "status": STATUS_FAILED,
            "source": {"bucket": bucket, "key": key},
            "error": str(error),
        }
