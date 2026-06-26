"""Auth stub — for MVP we resolve a default user from a header.

When Clerk/Auth0 is wired up, swap this module. The rest of the code only
depends on ``get_current_user_id``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Header

# Sentinel user id for unauthenticated dev/testing. Stable so local dev sees
# the same per-user state across restarts.
DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user_id(
    x_user_id: str | None = Header(default=None),
) -> UUID:
    if not x_user_id:
        return DEV_USER_ID
    try:
        return UUID(x_user_id)
    except ValueError:
        return DEV_USER_ID
