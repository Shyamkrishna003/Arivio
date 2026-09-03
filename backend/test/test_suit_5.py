import asyncio
import httpx
import traceback
import app.main # Import main to register all models

async def main():
    try:
        async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1") as client:
            r = await client.post("/auth/login", json={"email": "test@test.com", "password": "password123"})
            token = r.json()["access_token"]
            
            r2 = await client.get("/personalization/suitability/5", headers={"Authorization": f"Bearer {token}"})
            print(f"Status: {r2.status_code}")
            print(f"Response: {r2.text}")
    except Exception as e:
        traceback.print_exc()

asyncio.run(main())
