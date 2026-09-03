import asyncio
from app.db.session import AsyncSessionLocal

# Import all models to ensure SQLAlchemy mapper registry is fully populated
import app.community.models
import app.ingredients.models
import app.users.models
import app.products.models

from app.personalization.router import get_suitability
from app.users.models import User
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        # get test user
        user = (await db.execute(select(User).where(User.email == "test@test.com"))).scalar_one()
        
        try:
            result = await get_suitability(product_id=5, current_user=user, db=db)
            print("Success!", result)
        except Exception as e:
            import traceback
            traceback.print_exc()

asyncio.run(main())
