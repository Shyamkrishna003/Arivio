import asyncio
from sqlalchemy import select
from app.db.session import async_session_maker
from app.products.models import Product

async def main():
    async with async_session_maker() as db:
        result = await db.execute(select(Product))
        products = result.scalars().all()
        for p in products:
            print(f"ID: {p.id}, Name: {p.name}, Category: {p.category}")

asyncio.run(main())
