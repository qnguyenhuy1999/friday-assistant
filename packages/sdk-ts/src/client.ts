import { FridayHttpClient } from "./http";
import { AgentsResource } from "./resources/agents";
import { ApprovalsResource } from "./resources/approvals";
import { ArtifactsResource } from "./resources/artifacts";
import { ConversationsResource } from "./resources/conversations";
import { DelegationsResource } from "./resources/delegations";
import { EventsResource } from "./resources/events";
import { HealthResource } from "./resources/health";
import { RunsResource } from "./resources/runs";
import { StepsResource } from "./resources/steps";
import { SchedulesResource } from "./resources/schedules";
import { SkillsResource } from "./resources/skills";
import { TasksResource } from "./resources/tasks";
import { ToolInvocationsResource } from "./resources/tool-invocations";
import { WorkflowsResource } from "./resources/workflows";
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
  readonly schedules;
  readonly conversations;
  readonly skills;
  readonly agents;
  readonly delegations;
  readonly workflows;
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
    this.schedules = new SchedulesResource(http);
    this.conversations = new ConversationsResource(http);
    this.skills = new SkillsResource(http);
    this.agents = new AgentsResource(http);
    this.delegations = new DelegationsResource(http);
    this.workflows = new WorkflowsResource(http);
  }
}
