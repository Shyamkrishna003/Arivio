import asyncio
import httpx

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1") as client:
        # Login
        r = await client.post("/auth/login", json={"email": "test@test.com", "password": "password123"})
        token = r.json()["access_token"]
        print("Token:", token[:20])

        # POST allergy
        r2 = await client.post(
            "/profile/allergies", 
            json={"allergen": "peanuts", "allergy_type": "allergy"},
            headers={"Authorization": f"Bearer {token}"}
        )
        print("Status:", r2.status_code)
        print("Response:", r2.text)

asyncio.run(main())
