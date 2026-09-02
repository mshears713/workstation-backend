"""GET /api/v1/projects - the approved targets, for the ESP32.

Read-only and unauthenticated, like every other endpoint here: this is a
LAN-only backend and the payload is a list of short labels, nothing
sensitive. Editing the catalog is a file edit on this machine, not an API
call, so there is nothing to write.
"""

from fastapi import APIRouter, HTTPException, status

from app.api import projects_catalog

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("")
async def get_projects() -> dict:
    """Small flat JSON, sized for cJSON on a 320x240 screen.

    `version` is there so the device can tell "nothing changed" from "the
    list changed" without diffing it - bump it in config/projects.json when
    editing the lists.

    A broken catalog file returns 500 rather than a silently empty list: an
    empty selector and a misconfigured one look identical on the device, and
    only one of them is worth waking someone up for.
    """
    try:
        return projects_catalog.device_catalog()
    except projects_catalog.CatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"project catalog is misconfigured: {exc}",
        ) from exc
