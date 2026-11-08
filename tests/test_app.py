"""Unit tests for the resize Lambda. No AWS account needed: S3 is stubbed."""

import io
import os
import sys

import pytest
from PIL import Image

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["SOURCE_BUCKET"] = "originals-bucket"
os.environ["DESTINATION_BUCKET"] = "resized-bucket"
os.environ["THUMBNAIL_SIZE"] = "128"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "resize_image"))

import app  # noqa: E402


def make_image(size=(800, 600), mode="RGB", fmt="JPEG"):
    buffer = io.BytesIO()
    Image.new(mode, size, color="blue" if mode == "RGB" else None).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeS3:
    """Minimal in-memory stand-in for the boto3 S3 client."""

    def __init__(self, objects):
        self.objects = dict(objects)  # {(bucket, key): bytes}
        self.put_calls = []

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3({
        ("originals-bucket", "photos/cat.jpg"): make_image(),
        ("originals-bucket", "logo.png"): make_image((300, 1200), "RGBA", "PNG"),
        ("originals-bucket", "broken.jpg"): b"this is not an image",
    })
    monkeypatch.setattr(app, "s3", fake)
    return fake


# ---------------------------------------------------------------- parse_event
def test_parse_api_event_defaults_to_source_bucket():
    assert app.parse_event({"key": "a.jpg"}) == ("originals-bucket", "a.jpg")


def test_parse_eventbridge_event():
    event = {"detail": {"bucket": {"name": "b"}, "object": {"key": "x.png"}}}
    assert app.parse_event(event) == ("b", "x.png")


def test_parse_s3_notification_decodes_key():
    event = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "my+photo.jpg"}}}]}
    assert app.parse_event(event) == ("b", "my photo.jpg")


def test_parse_event_requires_key():
    with pytest.raises(app.ImageProcessingError):
        app.parse_event({})


# ---------------------------------------------------------------- helpers
def test_build_thumbnail_key():
    assert app.build_thumbnail_key("photos/cat.JPG") == "thumbnails/photos/cat-thumb.jpg"


def test_unsupported_extension():
    with pytest.raises(app.ImageProcessingError):
        app.get_format("notes.txt")


def test_resize_keeps_aspect_ratio():
    data, size = app.resize_image(make_image((800, 600)), 128, "JPEG")
    assert size == (128, 96)
    assert Image.open(io.BytesIO(data)).format == "JPEG"


# ---------------------------------------------------------------- handler
def test_handler_success(fake_s3):
    result = app.lambda_handler({"key": "photos/cat.jpg"}, None)
    assert result["status"] == "SUCCEEDED"
    assert result["thumbnail"]["key"] == "thumbnails/photos/cat-thumb.jpg"
    put = fake_s3.put_calls[0]
    assert put["Bucket"] == "resized-bucket"
    assert put["ContentType"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(put["Body"])).size) == 128


def test_handler_png_with_transparency(fake_s3):
    result = app.lambda_handler({"key": "logo.png"}, None)
    assert result["status"] == "SUCCEEDED"
    assert (result["thumbnail"]["width"], result["thumbnail"]["height"]) == (32, 128)


def test_handler_eventbridge_event(fake_s3):
    event = {"detail": {"bucket": {"name": "originals-bucket"}, "object": {"key": "photos/cat.jpg"}}}
    assert app.lambda_handler(event, None)["status"] == "SUCCEEDED"


@pytest.mark.parametrize("event", [
    {"key": "missing.jpg"},                              # object not found
    {"key": "broken.jpg"},                               # not a real image
    {"key": "notes.txt"},                                # unsupported type
    {"bucket": "someone-elses-bucket", "key": "a.jpg"},  # wrong bucket
    {},                                                  # no key
])
def test_handler_failures_return_failed_status(fake_s3, event):
    result = app.lambda_handler(event, None)
    assert result["status"] == "FAILED"
    assert result["error"]
    assert fake_s3.put_calls == []
