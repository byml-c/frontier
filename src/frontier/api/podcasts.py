from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import PodcastEpisode
from ..services.podcast_generation import PodcastGenerationError, podcast_audio_path

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


@router.get("/{episode_id}/audio", response_class=FileResponse)
def get_podcast_audio(
    episode_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> FileResponse:
    episode = session.get(PodcastEpisode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="播客不存在")
    try:
        path = podcast_audio_path(episode)
    except PodcastGenerationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "private, no-cache"},
    )
