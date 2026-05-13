import os
import uuid
import subprocess
from pathlib import Path
import boto3
from botocore.config import Config

_s3 = None


def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY"],
            aws_secret_access_key=os.environ["R2_SECRET_KEY"],
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _s3


def upload_clip(clip_path: Path, topic_slug: str) -> tuple[str, str]:
    """Upload clip and its thumbnail. Returns (clip_url, thumbnail_url)."""
    s3 = get_s3()
    bucket = os.environ["R2_BUCKET_NAME"]
    base_url = os.environ["R2_PUBLIC_URL"]
    clip_key = f"clips/{topic_slug}/{clip_path.name}"

    with open(clip_path, "rb") as f:
        s3.put_object(
            Bucket=bucket,
            Key=clip_key,
            Body=f,
            ContentType="video/mp4",
        )

    thumb_path = clip_path.with_suffix(".jpg")
    _extract_thumbnail(clip_path, thumb_path)
    thumb_key = f"thumbnails/{topic_slug}/{thumb_path.name}"

    with open(thumb_path, "rb") as f:
        s3.put_object(
            Bucket=bucket,
            Key=thumb_key,
            Body=f,
            ContentType="image/jpeg",
        )

    return f"{base_url}/{clip_key}", f"{base_url}/{thumb_key}"


def _extract_thumbnail(video_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-ss", "00:00:02",
            "-vframes", "1",
            "-q:v", "2",
            str(output_path),
        ],
        capture_output=True,
    )
