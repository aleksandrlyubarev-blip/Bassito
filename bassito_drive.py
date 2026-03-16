"""
Bassito Google Drive Uploader
==============================
Uses a Service Account for headless, unattended uploads.
No browser required — safe for 24/7 background operation.

Setup:
1. Go to Google Cloud Console → IAM → Service Accounts
2. Create a Service Account → download the JSON key
3. Save as 'service_account.json' next to this file
4. In Google Drive, share your target folder with the service account email
   (e.g., bassito-agent@your-project.iam.gserviceaccount.com) — give it Editor access
5. Set GOOGLE_DRIVE_FOLDER_ID in .env to the folder ID
"""

import os
import logging
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger("bassito.drive")

# ── Config ──────────────────────────────────────────────────────────
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_drive_service():
    """Build an authenticated Drive service using a Service Account."""
    sa_path = Path(SERVICE_ACCOUNT_FILE)
    if not sa_path.exists():
        raise FileNotFoundError(
            f"Service account key not found at '{sa_path}'. "
            f"Download it from Google Cloud Console → IAM → Service Accounts."
        )

    credentials = service_account.Credentials.from_service_account_file(
        str(sa_path), scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials)


def upload_to_drive(video_path: str, folder_id: str = None) -> str:
    """
    Upload a video file to Google Drive and return a shareable link.
    
    Args:
        video_path: Local path to the video file.
        folder_id: Optional Drive folder ID override. Falls back to env var.
    
    Returns:
        Shareable Google Drive URL.
    
    Raises:
        FileNotFoundError: If the video or service account key doesn't exist.
        RuntimeError: If the upload fails.
    """
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    target_folder = folder_id or DRIVE_FOLDER_ID
    if not target_folder:
        raise ValueError(
            "No Drive folder ID configured. Set GOOGLE_DRIVE_FOLDER_ID in .env "
            "or pass folder_id argument."
        )

    service = _get_drive_service()

    # Determine MIME type
    suffix = video.suffix.lower()
    mime_map = {
        ".mov": "video/quicktime",
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    mime_type = mime_map.get(suffix, "video/mp4")

    file_metadata = {
        "name": video.name,
        "parents": [target_folder],
    }

    media = MediaFileUpload(
        str(video),
        mimetype=mime_type,
        resumable=True,  # Supports large files with resume on failure
        chunksize=50 * 1024 * 1024,  # 50 MB chunks
    )

    logger.info(f"Uploading {video.name} ({video.stat().st_size / 1e6:.1f} MB) to Drive...")

    try:
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        ).execute()
    except Exception as e:
        raise RuntimeError(f"Drive upload failed: {e}")

    file_id = uploaded.get("id")
    web_link = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")

    # Make the file viewable by anyone with the link
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except Exception as e:
        logger.warning(f"Could not set public permissions (file still accessible to folder members): {e}")

    logger.info(f"Upload complete: {web_link}")
    return web_link
