import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import AsyncSessionLocal
from app.users.models import User, PrivacySetting

async def test_db():
    async with AsyncSessionLocal() as db:
        user = User(
            email="test2@test.com",
            username="testuser2",
            hashed_password="password123",
            full_name="Test User 2",
        )
        db.add(user)
        await db.flush()
        print(f"User ID: {user.id}")

asyncio.run(test_db())
