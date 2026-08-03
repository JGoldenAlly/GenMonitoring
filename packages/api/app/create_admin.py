"""One-off CLI to create (or promote) an admin user.

Every /users endpoint requires an existing admin (`require_role("admin")`
at the router level -- see app/routers/users.py), which is the correct
posture for an ops tool with no self-serve registration, but it means
nobody can ever create the FIRST user through the API. Run this once,
directly in the running api container, to bootstrap that first account:

    docker exec -it genmonitoring-api python -m app.create_admin admin@example.com

You'll be prompted for a password (not taken as an argv value, so it
doesn't end up in shell history / `docker inspect` process listings). If a
user with that email already exists, this promotes them to role="admin"
and resets their password instead of erroring, so it's also safe to use
for "I'm locked out" recovery.
"""
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import User
from app.security import hash_password


async def _create_or_promote(email: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(email=email, password_hash=hash_password(password), role="admin", is_active=True)
            db.add(user)
            await db.commit()
            print(f"Created admin user {email!r}.")
            return
        user.password_hash = hash_password(password)
        user.role = "admin"
        user.is_active = True
        await db.commit()
        print(f"User {email!r} already existed -- promoted to admin, re-activated, and password reset.")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: python -m app.create_admin <email>", file=sys.stderr)
        sys.exit(1)
    email = sys.argv[1]
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)
    asyncio.run(_create_or_promote(email, password))


if __name__ == "__main__":
    main()
