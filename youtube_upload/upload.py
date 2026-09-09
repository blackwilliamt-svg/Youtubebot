"""Uploads a finished compilation to YouTube as a Short."""
import logging

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from youtube_upload.oauth import load_credentials

log = logging.getLogger("meme_pipeline.youtube_upload")


class UploadError(Exception):
    pass


def upload_video(
    file_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "private",
) -> dict:
    """
    privacy_status: 'private' | 'unlisted' | 'public'. Defaults to
    'private' so nothing goes live without you flipping it in YouTube
    Studio (or passing privacy_status='public' explicitly from the
    dashboard once you're ready to trust it unattended).
    """
    creds = load_credentials()
    if creds is None:
        raise UploadError("YouTube not connected yet -- visit /youtube/authorize in the dashboard first")

    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": "23",  # Comedy
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    url = f"https://youtube.com/shorts/{video_id}"
    log.info("Uploaded video %s -> %s", video_id, url)
    return {"video_id": video_id, "url": url}
