import asyncio
from uploader.gdrive import upload_file


async def upload_to_gdrive(downloaded_file, ctx=None):

    result = await asyncio.to_thread(
        upload_file,
        downloaded_file
    )

    return result
