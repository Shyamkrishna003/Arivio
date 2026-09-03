import asyncio
import sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.products.models import Product
from app.users.models import User

async def main():
    async with async_session_maker() as db:
        result = await db.execute(select(Product))
        products = result.scalars().all()
        print("--- PRODUCTS ---")
        for p in products:
            print(f"ID: {p.id}, Name: {p.name}, Category: {p.category}")
            
        print("\n--- USERS ---")
        user_result = await db.execute(select(User))
        users = user_result.scalars().all()
        for u in users:
            print(f"ID: {u.id}, Email: {u.email}")

asyncio.run(main())
