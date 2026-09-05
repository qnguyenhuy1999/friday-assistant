import type {
  CreateEvaluationSuiteBody,
  CreateSkillBody,
  CreateSkillRevisionBody,
  EvaluateImprovementProposalBody,
  EvaluationRun,
  EvaluationSuite,
  ImprovementProposal,
  SkillPage,
  RequestRollbackBody,
  RunEvaluationBody,
  Skill,
  SkillCandidateEvaluation,
  SkillEvidenceSnapshot,
  SkillImprovementPolicy,
  SkillPromotion,
  SkillRollback,
  SkillRevision,
  SkillUsageRecord,
  SkillImprovementPolicyBody,
  ResolveSkillRequestBody,
} from "@friday/contracts";
import {
  validateSkillCandidateEvaluation,
  validateSkillEvidenceSnapshot,
  validateSkillEvaluationRun,
  validateSkillEvaluationSuite,
  validateSkillEvaluationSuites,
  validateSkillImprovementPolicy,
  validateSkillImprovementProposal,
  validateSkillImprovementProposals,
  validateSkillPromotion,
  validateSkillRollback,
  validateSkill,
  validateSkillPage,
  validateSkillRevision,
  validateSkillRevisions,
  validateSkillImprovementPolicyRun,
  validateSkillUsage,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";

export interface ListSkillsParams {
  limit?: number;
  cursor?: string;
}

export interface ListSkillRevisionsPageParams {
  limit?: number;
  beforeVersion?: number;
}

export class SkillsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(input: CreateSkillBody) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: "/v1/skills",
      body: input,
      validate: validateSkill,
    });
  }
  list(params: ListSkillsParams = {}) {
    return this.http.requestJson<SkillPage>({
      method: "GET",
      path: "/v1/skills",
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateSkillPage,
    });
  }
  get(skillId: string) {
    return this.http.requestJson<Skill>({
      method: "GET",
      path: `/v1/skills/${skillId}`,
      validate: validateSkill,
    });
  }
  getRevision(skillId: string, revisionId: string) {
    return this.http.requestJson<SkillRevision>({
      method: "GET",
      path: `/v1/skills/${skillId}/revisions/${revisionId}`,
      validate: validateSkillRevision,
    });
  }
  createRevision(skillId: string, input: CreateSkillRevisionBody) {
    return this.http.requestJson<SkillRevision>({
      method: "POST",
      path: `/v1/skills/${skillId}/revisions`,
      body: input,
      validate: validateSkillRevision,
    });
  }
  listRevisions(skillId: string) {
    return this.http.requestJson<SkillRevision[]>({
      method: "GET",
      path: `/v1/skills/${skillId}/revisions`,
      validate: validateSkillRevisions,
    });
  }
  listRevisionsPage(
    skillId: string,
    params: ListSkillRevisionsPageParams = {},
  ) {
    return this.http.requestJson<SkillRevision[]>({
      method: "GET",
      path: `/v1/skills/${skillId}/revisions`,
      query: {
        limit: params.limit,
        before_version: params.beforeVersion,
      },
      validate: validateSkillRevisions,
    });
  }
  activateRevision(skillId: string, revisionId: string) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: `/v1/skills/${skillId}/revisions/${revisionId}/activate`,
      validate: validateSkill,
    });
  }
  disable(skillId: string) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: `/v1/skills/${skillId}/disable`,
      validate: validateSkill,
    });
  }
  archive(skillId: string) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: `/v1/skills/${skillId}/archive`,
      validate: validateSkill,
    });
  }
  listUsage(skillId: string) {
    return this.http.requestJson<SkillUsageRecord[]>({
      method: "GET",
      path: `/v1/skills/${skillId}/usage`,
      validate: validateSkillUsage,
    });
  }
  getEvidenceSnapshot(snapshotId: string) {
    return this.http.requestJson<SkillEvidenceSnapshot>({
      method: "GET",
      path: `/v1/skills/evidence-snapshots/${snapshotId}`,
      validate: validateSkillEvidenceSnapshot,
    });
  }
  listProposals(skillId: string) {
    return this.http.requestJson<ImprovementProposal[]>({
      method: "GET",
      path: `/v1/skills/${skillId}/improvement-proposals`,
      validate: validateSkillImprovementProposals,
    });
  }
  getProposal(proposalId: string) {
    return this.http.requestJson<ImprovementProposal>({
      method: "GET",
      path: `/v1/skills/improvement-proposals/${proposalId}`,
      validate: validateSkillImprovementProposal,
    });
  }
  cancelProposal(proposalId: string) {
    return this.http.requestJson<ImprovementProposal>({
      method: "POST",
      path: `/v1/skills/improvement-proposals/${proposalId}/cancel`,
      validate: validateSkillImprovementProposal,
    });
  }
  evaluateProposal(proposalId: string, body: EvaluateImprovementProposalBody) {
    return this.http.requestJson<SkillCandidateEvaluation>({
      method: "POST",
      path: `/v1/skills/improvement-proposals/${proposalId}/evaluate`,
      body,
      validate: validateSkillCandidateEvaluation,
    });
  }
  getProposalEvaluation(proposalId: string) {
    return this.http.requestJson<SkillCandidateEvaluation>({
      method: "GET",
      path: `/v1/skills/improvement-proposals/${proposalId}/evaluation`,
      validate: validateSkillCandidateEvaluation,
    });
  }
  requestPromotion(proposalId: string) {
    return this.http.requestJson<SkillPromotion>({
      method: "POST",
      path: `/v1/skills/improvement-proposals/${proposalId}/request-promotion`,
      validate: validateSkillPromotion,
    });
  }
  getPromotion(promotionId: string) {
    return this.http.requestJson<SkillPromotion>({
      method: "GET",
      path: `/v1/skills/promotions/${promotionId}`,
      validate: validateSkillPromotion,
    });
  }
  executePromotion(promotionId: string) {
    return this.http.requestJson<SkillPromotion>({
      method: "POST",
      path: `/v1/skills/promotions/${promotionId}/approve`,
      validate: validateSkillPromotion,
    });
  }
  rejectPromotion(promotionId: string, body: ResolveSkillRequestBody) {
    return this.http.requestJson<SkillPromotion>({
      method: "POST",
      path: `/v1/skills/promotions/${promotionId}/reject`,
      body,
      validate: validateSkillPromotion,
    });
  }
  cancelPromotion(promotionId: string) {
    return this.http.requestJson<SkillPromotion>({
      method: "POST",
      path: `/v1/skills/promotions/${promotionId}/cancel`,
      validate: validateSkillPromotion,
    });
  }
  requestRollback(skillId: string, body: RequestRollbackBody) {
    return this.http.requestJson<SkillRollback>({
      method: "POST",
      path: `/v1/skills/${skillId}/request-rollback`,
      body,
      validate: validateSkillRollback,
    });
  }
  getRollback(rollbackId: string) {
    return this.http.requestJson<SkillRollback>({
      method: "GET",
      path: `/v1/skills/rollbacks/${rollbackId}`,
      validate: validateSkillRollback,
    });
  }
  executeRollback(rollbackId: string) {
    return this.http.requestJson<SkillRollback>({
      method: "POST",
      path: `/v1/skills/rollbacks/${rollbackId}/approve`,
      validate: validateSkillRollback,
    });
  }
  rejectRollback(rollbackId: string, body: ResolveSkillRequestBody) {
    return this.http.requestJson<SkillRollback>({
      method: "POST",
      path: `/v1/skills/rollbacks/${rollbackId}/reject`,
      body,
      validate: validateSkillRollback,
    });
  }
  cancelRollback(rollbackId: string) {
    return this.http.requestJson<SkillRollback>({
      method: "POST",
      path: `/v1/skills/rollbacks/${rollbackId}/cancel`,
      validate: validateSkillRollback,
    });
  }
  getImprovementPolicy(skillId: string) {
    return this.http.requestJson<SkillImprovementPolicy>({
      method: "GET",
      path: `/v1/skills/${skillId}/improvement-policy`,
      validate: validateSkillImprovementPolicy,
    });
  }
  putImprovementPolicy(skillId: string, body: SkillImprovementPolicyBody) {
    return this.http.requestJson<SkillImprovementPolicy>({
      method: "PUT",
      path: `/v1/skills/${skillId}/improvement-policy`,
      body,
      validate: validateSkillImprovementPolicy,
    });
  }
  runImprovementPolicyNow(skillId: string) {
    return this.http.requestJson<{ due: boolean }>({
      method: "POST",
      path: `/v1/skills/${skillId}/improvement-policy/run-now`,
      validate: validateSkillImprovementPolicyRun,
    });
  }
  createEvaluationSuite(skillId: string, body: CreateEvaluationSuiteBody) {
    return this.http.requestJson<EvaluationSuite>({
      method: "POST",
      path: `/v1/skills/${skillId}/evaluation-suites`,
      body,
      validate: validateSkillEvaluationSuite,
    });
  }
  listEvaluationSuites(skillId: string) {
    return this.http.requestJson<EvaluationSuite[]>({
      method: "GET",
      path: `/v1/skills/${skillId}/evaluation-suites`,
      validate: validateSkillEvaluationSuites,
    });
  }
  getEvaluationSuite(suiteId: string) {
    return this.http.requestJson<EvaluationSuite>({
      method: "GET",
      path: `/v1/skills/evaluation-suites/${suiteId}`,
      validate: validateSkillEvaluationSuite,
    });
  }
  runEvaluation(suiteId: string, body: RunEvaluationBody) {
    return this.http.requestJson<EvaluationRun>({
      method: "POST",
      path: `/v1/skills/evaluation-suites/${suiteId}/runs`,
      body,
      validate: validateSkillEvaluationRun,
    });
  }
  getEvaluationRun(runId: string) {
    return this.http.requestJson<EvaluationRun>({
      method: "GET",
      path: `/v1/skills/evaluation-runs/${runId}`,
      validate: validateSkillEvaluationRun,
    });
  }
}
