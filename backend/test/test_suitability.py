import asyncio
import json
import httpx

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1") as client:
        # Login
        r = await client.post("/auth/login", json={"email": "test@test.com", "password": "password123"})
        token = r.json()["access_token"]
        print("Token OK")

        # Scan a product (Nutella barcode)
        r = await client.post("/products/scan", params={"barcode": "3017620422003"})
        product = r.json()
        pid = product.get("id", "N/A")
        print(f"Product: {product.get('name', 'N/A')} (ID: {pid})")

        # Get suitability
        r = await client.get(
            f"/personalization/suitability/{pid}",
            headers={"Authorization": f"Bearer {token}"}
        )
        print(f"Status: {r.status_code}")
        print(f"Response: {r.text}")

asyncio.run(main())
