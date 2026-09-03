import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def remove_duplicates():
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            DELETE FROM user_scan_history
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM user_scan_history
                GROUP BY user_id, product_id
            );
        """))
        await db.commit()
        print("Duplicates removed from user_scan_history!")

if __name__ == "__main__":
    asyncio.run(remove_duplicates())
