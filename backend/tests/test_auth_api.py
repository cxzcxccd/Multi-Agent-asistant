"""买家与客服身份认证接口测试。"""

import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


def test_login_and_role_permissions() -> None:
    async def run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "buyer_a", "password": "buyer-a-demo"},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            refresh_token = login.json()["refresh_token"]
            headers = {"Authorization": f"Bearer {token}"}

            buyer = await client.get("/api/auth/buyer", headers=headers)
            staff = await client.get("/api/auth/staff", headers=headers)
            assert buyer.status_code == 200
            assert buyer.json()["buyer_id"] == "A"
            assert staff.status_code == 403

            refreshed = await client.post(
                "/api/auth/refresh",
                json={"refresh_token": refresh_token},
            )
            assert refreshed.status_code == 200
            assert refreshed.json()["refresh_token"] != refresh_token

            reused = await client.post(
                "/api/auth/refresh",
                json={"refresh_token": refresh_token},
            )
            assert reused.status_code == 401

            new_refresh_token = refreshed.json()["refresh_token"]
            logged_out = await client.post(
                "/api/auth/logout",
                json={"refresh_token": new_refresh_token},
            )
            assert logged_out.status_code == 204

            after_logout = await client.post(
                "/api/auth/refresh",
                json={"refresh_token": new_refresh_token},
            )
            assert after_logout.status_code == 401

    asyncio.run(run())


def test_protected_api_rejects_missing_and_invalid_credentials() -> None:
    async def run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get("/api/orders")
            invalid = await client.get(
                "/api/orders",
                headers={"Authorization": "Bearer invalid-token"},
            )
            bad_login = await client.post(
                "/api/auth/login",
                json={"username": "buyer_a", "password": "wrong-password"},
            )

            assert missing.status_code == 401
            assert invalid.status_code == 401
            assert bad_login.status_code == 401

    asyncio.run(run())
