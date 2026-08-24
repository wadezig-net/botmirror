import os
import asyncio
from status_ui import render_status
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = [
    "https://www.googleapis.com/auth/drive.file"
]


BASE_DIR = "/root/botmirror"


def get_service():

    creds = Credentials.from_authorized_user_file(
        f"{BASE_DIR}/token.json",
        SCOPES
    )

    service = build(
        "drive",
        "v3",
        credentials=creds
    )

    return service



def upload_file(file_path):

    service = get_service()

    filename = os.path.basename(file_path)

    file_metadata = {
        "name": filename
    }


    media = MediaFileUpload(
        file_path,
        resumable=True
    )


    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name"
    ).execute()


    file_id = uploaded.get("id")


    # buat public link
    permission = {
        "type": "anyone",
        "role": "reader"
    }


    service.permissions().create(
        fileId=file_id,
        body=permission
    ).execute()


    link = (
        f"https://drive.google.com/file/d/{file_id}/view"
    )


    return {
        "name": filename,
        "id": file_id,
        "link": link
    }

async def upload_to_gdrive(downloaded_file, ctx):

    total_size = os.path.getsize(downloaded_file)

    if ctx:
        await render_status(
            ctx,
                "Upload",
                percent=0.0,
                processed=0,
                total=total_size
    )

    result = await asyncio.to_thread(
        upload_file,
        downloaded_file
    )

    if ctx:
        await render_status(
            ctx,
                "Upload",
                percent=100.0,
                processed=total_size,
                total=total_size
    )

    return {
        "link": result["link"],
        "file_id": result["id"],
        "guest_token": None
    }

def delete_from_gdrive(file_id):

    service = get_service()

    service.files().delete(
        fileId=file_id
    ).execute()

    return True

