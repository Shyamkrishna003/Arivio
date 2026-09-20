"""
Bounded reads of uploaded files.

Every upload endpoint used to do `raw = await file.read()` and check the length
afterwards. The check was correct and the limit was enforced — but only once
the entire body was already resident in memory, which means the limit never
protected the thing it was there to protect. A single request with a
multi-hundred-megabyte body is enough to exhaust a small instance's memory and
take the process down, and on a 512MB free tier that is not a large request.

Reading in chunks and stopping at the cap fixes that: an oversized upload is
abandoned after one chunk past the limit, so the peak cost of rejecting it is
the limit itself rather than whatever the client chose to send.

The cap is checked against bytes actually read, never against the
Content-Length header. A header is a claim by the client; it can be absent
under chunked transfer encoding, and it can simply be a lie. It is fine as an
early reject but useless as the only guard, so it is not used here at all.
"""

from fastapi import HTTPException, UploadFile, status

# Large enough that a normal photo is a handful of reads, small enough that
# the overshoot past the limit before we notice is negligible.
_CHUNK_SIZE = 64 * 1024


async def read_upload_capped(
    file: UploadFile,
    max_bytes: int,
    *,
    too_large_detail: str,
) -> bytes:
    """
    Read an upload, refusing anything over `max_bytes`.

    Raises HTTPException(422) with `too_large_detail` as soon as the limit is
    passed. 422 rather than 413 because that is what these endpoints already
    answered when the check came after the read — this changes when the limit
    is enforced, not what the client sees when it trips.
    """
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            # Nothing is retained: drop what we have before raising so an
            # oversized upload does not leave its partial body alive in the
            # exception's frame while the response is built.
            chunks.clear()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=too_large_detail,
            )
        chunks.append(chunk)

    return b"".join(chunks)
