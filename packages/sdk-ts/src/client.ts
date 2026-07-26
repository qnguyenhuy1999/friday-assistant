import { FridayHttpClient } from "./http";
import { ApprovalsResource } from "./resources/approvals";
import { ArtifactsResource } from "./resources/artifacts";
import { EventsResource } from "./resources/events";
import { HealthResource } from "./resources/health";
import { RunsResource } from "./resources/runs";
import { StepsResource } from "./resources/steps";
import { TasksResource } from "./resources/tasks";
import { ToolInvocationsResource } from "./resources/tool-invocations";
export interface FridayClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  defaultTimeoutMs?: number;
}
export class FridayClient {
  readonly tasks;
  readonly runs;
  readonly steps;
  readonly approvals;
  readonly toolInvocations;
  readonly artifacts;
  readonly events;
  readonly health;
  constructor(options: FridayClientOptions) {
    const http = new FridayHttpClient(options);
    this.tasks = new TasksResource(http);
    this.runs = new RunsResource(http);
    this.steps = new StepsResource(http);
    this.approvals = new ApprovalsResource(http);
    this.toolInvocations = new ToolInvocationsResource(http);
    this.artifacts = new ArtifactsResource(http);
    this.events = new EventsResource(http, options.baseUrl.replace(/\/+$/, ""));
    this.health = new HealthResource(http);
  }
}
