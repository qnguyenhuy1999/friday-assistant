from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from starlette.testclient import TestClient

from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.worker_coordination import ClaimNextRun
from friday.application.workflow_execution_use_cases import StartWorkflowExecution
from friday.domain.identifiers import RunId, TaskId, WorkflowId
from friday.domain.run import Run
from friday.domain.task import Task
from friday.domain.workflow_execution import TaskWorkflowBinding

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _runtime_registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def test_workflow_revision_and_lifecycle_endpoints(client: TestClient) -> None:
    agent = client.post(
        "/v1/agents",
        json={"key": "workflow.target", "display_name": "Target", "description": ""},
    ).json()
    workflow = client.post(
        "/v1/workflows",
        json={"key": "workflow.registry", "display_name": "Registry"},
    ).json()
    revision_response = client.post(
        f"/v1/workflows/{workflow['id']}/revisions",
        json={
            "nodes": [
                {
                    "node_key": "start",
                    "target_agent_id": agent["id"],
                    "objective": "start",
                    "input_payload": {},
                    "expected_output_contract": "done",
                },
                {
                    "node_key": "finish",
                    "target_agent_id": agent["id"],
                    "objective": "finish",
                    "input_payload": {},
                    "expected_output_contract": "done",
                },
            ],
            "edges": [{"from": "start", "to": "finish"}],
            "source_kind": "operator",
        },
    )
    assert revision_response.status_code == 201
    revision = revision_response.json()
    assert revision["version"] == 1
    assert [node["node_key"] for node in revision["nodes"]] == ["finish", "start"]
    assert revision["edges"][0]["from"] == "start"

    listed = client.get(f"/v1/workflows/{workflow['id']}/revisions")
    fetched = client.get(f"/v1/workflows/{workflow['id']}/revisions/{revision['id']}")
    activated = client.post(f"/v1/workflows/{workflow['id']}/revisions/{revision['id']}/activate")
    disabled = client.post(f"/v1/workflows/{workflow['id']}/disable")
    archived = client.post(f"/v1/workflows/{workflow['id']}/archive")

    assert listed.status_code == fetched.status_code == 200
    assert listed.json()[0]["id"] == fetched.json()["id"] == revision["id"]
    assert activated.json()["active_revision_id"] == revision["id"]
    assert disabled.json()["status"] == "disabled"
    assert archived.json()["status"] == "archived"


def test_run_workflow_inspection_endpoints_return_frozen_execution_state(
    app: FastAPI, client: TestClient
) -> None:
    agent = client.post(
        "/v1/agents",
        json={"key": "inspect.agent", "display_name": "Inspect Agent", "description": ""},
    ).json()
    revision = client.post(
        f"/v1/agents/{agent['id']}/revisions",
        json={
            "instructions": "work",
            "runtime_kind": "claude_cli",
            "runtime_config": {},
            "source_kind": "operator",
        },
    ).json()
    client.post(f"/v1/agents/{agent['id']}/revisions/{revision['id']}/activate")

    workflow = client.post(
        "/v1/workflows",
        json={"key": "inspect.workflow", "display_name": "Inspect Workflow"},
    ).json()
    workflow_revision = client.post(
        f"/v1/workflows/{workflow['id']}/revisions",
        json={
            "nodes": [
                {
                    "node_key": "root",
                    "target_agent_id": agent["id"],
                    "objective": "run root",
                    "input_payload": {},
                    "expected_output_contract": "done",
                }
            ],
            "edges": [],
            "source_kind": "operator",
        },
    ).json()
    client.post(f"/v1/workflows/{workflow['id']}/revisions/{workflow_revision['id']}/activate")

    workflow_id = WorkflowId.parse(workflow["id"])
    task = Task.new(id=TaskId.new(), title="root", description="", created_at=NOW)
    task.start(NOW)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=NOW)
    run.start(NOW)
    factory = app.state.uow_factory
    with factory() as uow:
        uow.tasks.add(task)
        uow.runs.add(run)
        uow.task_workflow_bindings.bind(
            TaskWorkflowBinding.new(task_id=task.id, workflow_id=workflow_id, at=NOW)
        )
        uow.work_queue.enqueue(run.id, NOW, NOW)
        uow.commit()

    claim = ClaimNextRun(
        factory,
        _Clock(),
        worker_id="worker-a",
        lease_duration=timedelta(minutes=5),
        candidate_limit=10,
    ).execute()
    assert claim is not None

    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow_id, claim.worker_id, claim.claim_token, claim.claim_generation
    )

    inspected = client.get(f"/v1/runs/{run.id}/workflow")
    assert inspected.status_code == 200
    body = inspected.json()
    assert body["root_run_id"] == str(run.id)
    assert body["workflow_execution_id"] == str(execution.id)
    assert body["workflow_id"] == workflow["id"]
    assert body["workflow_revision_id"] == workflow_revision["id"]
    assert body["status"] == "running"
    assert "claim_token" not in body
    assert "approval" not in str(body).lower()

    nodes = client.get(f"/v1/runs/{run.id}/workflow/nodes")
    assert nodes.status_code == 200
    node_bodies = nodes.json()
    assert len(node_bodies) == 1
    assert node_bodies[0]["node_key"] == "root"
    assert node_bodies[0]["target_agent_id"] == agent["id"]
    assert node_bodies[0]["status"] == "dispatched"
    assert node_bodies[0]["child_run_id"] is not None
    assert "claim_token" not in node_bodies[0]


def test_run_workflow_inspection_returns_404_for_run_without_workflow(
    client: TestClient,
) -> None:
    missing_run_id = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/v1/runs/{missing_run_id}/workflow").status_code == 404
    assert client.get(f"/v1/runs/{missing_run_id}/workflow/nodes").status_code == 404
