from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient


def test_agent_registry_paginates_beyond_the_first_page(app: FastAPI) -> None:
    with TestClient(app) as client:
        for index in range(3):
            created = client.post(
                "/v1/agents",
                json={
                    "key": f"pagination.agent-{index}",
                    "display_name": f"Agent {index}",
                    "description": "",
                },
            )
            assert created.status_code == 201

        first = client.get("/v1/agents?limit=2")
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None

        second = client.get(
            "/v1/agents",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["items"]) == 1
        assert second_body["next_cursor"] is None
        ids = {item["id"] for item in first_body["items"] + second_body["items"]}
        assert len(ids) == 3


def test_skill_registry_uses_stable_keyset_pages_and_collection_bound_cursors(
    app: FastAPI,
) -> None:
    with TestClient(app) as client:
        skill_ids = [
            client.post(
                "/v1/skills",
                json={
                    "key": f"pagination.skill-{index}",
                    "display_name": f"Skill {index}",
                    "description": "",
                },
            ).json()["id"]
            for index in range(7)
        ]

        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, str | int] = {"limit": 2}
            if cursor is not None:
                params["cursor"] = cursor
            response = client.get("/v1/skills", params=params)
            assert response.status_code == 200
            body = response.json()
            pages.append(body)
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert len(pages) == 4
        assert all(len(page["items"]) <= 2 for page in pages)
        items = [item for page in pages for item in page["items"]]
        assert [item["id"] for item in items] == list(dict.fromkeys(item["id"] for item in items))
        assert {item["id"] for item in items} == set(skill_ids)
        assert [(item["created_at"], item["id"]) for item in items] == sorted(
            (item["created_at"], item["id"]) for item in items
        )

        assert client.get("/v1/skills", params={"cursor": "not-a-cursor"}).status_code == 422
        first_cursor = pages[0]["next_cursor"]
        assert first_cursor is not None
        assert client.get("/v1/workflows", params={"cursor": first_cursor}).status_code == 422


def test_skill_registry_preserves_legacy_default_and_supports_explicit_pages(
    app: FastAPI,
) -> None:
    with TestClient(app) as client:
        for index in range(30):
            response = client.post(
                "/v1/skills",
                json={
                    "key": f"pagination.compatibility-{index}",
                    "display_name": f"Compatibility Skill {index}",
                    "description": "",
                },
            )
            assert response.status_code == 201

        legacy = client.get("/v1/skills")
        assert legacy.status_code == 200
        legacy_body = legacy.json()
        assert len(legacy_body["items"]) == 30
        assert legacy_body["next_cursor"] is None

        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(3):
            params: dict[str, str | int] = {"limit": 10}
            if cursor is not None:
                params["cursor"] = cursor
            response = client.get("/v1/skills", params=params)
            assert response.status_code == 200
            body = response.json()
            pages.append(body)
            cursor = body["next_cursor"]

        assert [len(page["items"]) for page in pages] == [10, 10, 10]
        assert pages[0]["next_cursor"] is not None
        assert pages[1]["next_cursor"] is not None
        assert pages[2]["next_cursor"] is None
        paged_items = [item for page in pages for item in page["items"]]
        assert [item["id"] for item in paged_items] == [item["id"] for item in legacy_body["items"]]
        assert [(item["created_at"], item["id"]) for item in paged_items] == sorted(
            (item["created_at"], item["id"]) for item in paged_items
        )


def test_skill_revision_history_is_bounded_newest_first_and_legacy_compatible(
    app: FastAPI,
) -> None:
    with TestClient(app) as client:
        skill = client.post(
            "/v1/skills",
            json={"key": "pagination.revisions", "display_name": "Revisions"},
        ).json()
        for version in range(1, 7):
            response = client.post(
                f"/v1/skills/{skill['id']}/revisions",
                json={
                    "instructions": f"revision {version}",
                    "source_kind": "operator",
                },
            )
            assert response.status_code == 201

        first = client.get(f"/v1/skills/{skill['id']}/revisions", params={"limit": 2})
        assert first.status_code == 200
        assert [item["version"] for item in first.json()] == [6, 5]

        second = client.get(
            f"/v1/skills/{skill['id']}/revisions",
            params={"limit": 2, "before_version": 5},
        )
        assert [item["version"] for item in second.json()] == [4, 3]

        third = client.get(
            f"/v1/skills/{skill['id']}/revisions",
            params={"limit": 2, "before_version": 3},
        )
        assert [item["version"] for item in third.json()] == [2, 1]

        legacy = client.get(f"/v1/skills/{skill['id']}/revisions")
        assert legacy.status_code == 200
        assert [item["version"] for item in legacy.json()] == [1, 2, 3, 4, 5, 6]


def test_workflow_revision_history_supports_bounded_newest_first_pages(
    app: FastAPI,
) -> None:
    with TestClient(app) as client:
        agent = client.post(
            "/v1/agents",
            json={
                "key": "pagination.workflow-agent",
                "display_name": "Workflow Agent",
                "description": "",
            },
        ).json()
        workflow = client.post(
            "/v1/workflows",
            json={
                "key": "pagination.workflow",
                "display_name": "Paginated Workflow",
            },
        ).json()

        for index in range(3):
            response = client.post(
                f"/v1/workflows/{workflow['id']}/revisions",
                json={
                    "nodes": [
                        {
                            "node_key": "root",
                            "target_agent_id": agent["id"],
                            "objective": f"revision {index + 1}",
                            "input_payload": {},
                            "expected_output_contract": "done",
                        }
                    ],
                    "edges": [],
                    "source_kind": "operator",
                },
            )
            assert response.status_code == 201

        first = client.get(
            f"/v1/workflows/{workflow['id']}/revisions",
            params={"limit": 2},
        )
        assert first.status_code == 200
        assert [item["version"] for item in first.json()] == [3, 2]

        second = client.get(
            f"/v1/workflows/{workflow['id']}/revisions",
            params={"limit": 2, "before_version": 2},
        )
        assert second.status_code == 200
        assert [item["version"] for item in second.json()] == [1]

        legacy = client.get(f"/v1/workflows/{workflow['id']}/revisions")
        assert legacy.status_code == 200
        assert [item["version"] for item in legacy.json()] == [1, 2, 3]
