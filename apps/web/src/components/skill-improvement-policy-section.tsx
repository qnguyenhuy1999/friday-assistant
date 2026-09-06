import type {
  EvaluationSuite,
  SkillImprovementPolicy,
  SkillImprovementPolicyBody,
} from "@friday/contracts";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  isNotFoundError,
  useRunSkillImprovementPolicyNow,
  useSaveSkillImprovementPolicy,
  useSkillImprovementPolicy,
} from "../hooks/use-skill-improvement";
import { useSkillEvaluationSuites } from "../hooks/use-skill-evaluations";

const GENERATOR_VERSION = "brain-candidate-generator-v2";
const COMPARISON_POLICY_VERSION = "comparison-v1";
const EMPTY_SUITE_LIST: EvaluationSuite[] = [];

type OptionalPolicyTimestamps = {
  created_at?: string;
  updated_at?: string;
};

type PolicyDraft = {
  enabled: boolean;
  minimum_usage_records: number | "";
  minimum_failures: number | "";
  minimum_harmful_feedback: number | "";
  evaluation_suite_id: string;
  cooldown_seconds: number | "";
  evidence_window_size: number | "";
};

const DEFAULT_DRAFT: PolicyDraft = {
  enabled: true,
  minimum_usage_records: 1,
  minimum_failures: 0,
  minimum_harmful_feedback: 0,
  evaluation_suite_id: "",
  cooldown_seconds: 3600,
  evidence_window_size: 20,
};

function formatTime(value: string | null | undefined) {
  if (value === null) return "Not recorded";
  if (value === undefined) {
    return "Not provided by the current policy API contract";
  }
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function formatError(error: unknown) {
  return error instanceof Error ? error.message : "Unknown server error";
}

function toDraft(policy: SkillImprovementPolicy): PolicyDraft {
  return {
    enabled: policy.enabled,
    minimum_usage_records: policy.minimum_usage_records,
    minimum_failures: policy.minimum_failures,
    minimum_harmful_feedback: policy.minimum_harmful_feedback,
    evaluation_suite_id: policy.evaluation_suite_id,
    cooldown_seconds: policy.cooldown_seconds,
    evidence_window_size: policy.evidence_window_size,
  };
}

function policyBody(draft: PolicyDraft): SkillImprovementPolicyBody {
  return {
    enabled: draft.enabled,
    minimum_usage_records: draft.minimum_usage_records as number,
    minimum_failures: draft.minimum_failures as number,
    minimum_harmful_feedback: draft.minimum_harmful_feedback as number,
    evaluation_suite_id: draft.evaluation_suite_id,
    cooldown_seconds: draft.cooldown_seconds as number,
    max_open_proposals: 1,
    evidence_window_size: draft.evidence_window_size as number,
    generator_version: GENERATOR_VERSION,
    comparison_policy_version: COMPARISON_POLICY_VERSION,
  };
}

function PolicyFacts({ policy }: { policy: SkillImprovementPolicy }) {
  const timestamps = policy as SkillImprovementPolicy &
    OptionalPolicyTimestamps;
  return (
    <dl>
      <dt>Skill ID</dt>
      <dd>{policy.skill_id}</dd>
      <dt>Enabled</dt>
      <dd>{policy.enabled ? "true" : "false"}</dd>
      <dt>Minimum usage records</dt>
      <dd>{policy.minimum_usage_records}</dd>
      <dt>Minimum failed usage records</dt>
      <dd>{policy.minimum_failures}</dd>
      <dt>Minimum harmful feedback records</dt>
      <dd>{policy.minimum_harmful_feedback}</dd>
      <dt>Evaluation suite ID</dt>
      <dd>{policy.evaluation_suite_id}</dd>
      <dt>Cooldown seconds</dt>
      <dd>{policy.cooldown_seconds}</dd>
      <dt>Maximum open proposals</dt>
      <dd>{String(policy.max_open_proposals)}</dd>
      <dt>Evidence window size</dt>
      <dd>{policy.evidence_window_size}</dd>
      <dt>Generator version</dt>
      <dd>{policy.generator_version}</dd>
      <dt>Comparison policy version</dt>
      <dd>{policy.comparison_policy_version}</dd>
      <dt>Created at</dt>
      <dd>{formatTime(timestamps.created_at)}</dd>
      <dt>Updated at</dt>
      <dd>{formatTime(timestamps.updated_at)}</dd>
      <dt>Last triggered at</dt>
      <dd>{formatTime(policy.last_triggered_at)}</dd>
    </dl>
  );
}

function numericValue(value: string): number | "" {
  return value === "" ? "" : Number(value);
}

export function SkillImprovementPolicySection({
  skillId,
}: {
  skillId: string;
}) {
  const policy = useSkillImprovementPolicy(skillId);
  const suites = useSkillEvaluationSuites(skillId);
  const suiteItems = suites.data ?? EMPTY_SUITE_LIST;
  const suiteProvenanceMismatch = suiteItems.some(
    (suite) => suite.skill_id !== skillId,
  );
  const knownSuiteIds = useMemo(
    () => suiteItems.map((suite) => suite.id),
    [suiteItems],
  );
  const savePolicy = useSaveSkillImprovementPolicy(skillId, knownSuiteIds);
  const runNow = useRunSkillImprovementPolicyNow(skillId);
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<"due" | "not-due" | null>(null);

  const policyNotFound = policy.isError && isNotFoundError(policy.error);
  const policyLoadError = policy.isError && !policyNotFound;
  const persistedPolicy = policy.data;
  const suiteSelectionReady = suites.isSuccess && !suiteProvenanceMismatch;
  const policyProvenanceMismatch =
    persistedPolicy !== undefined &&
    (persistedPolicy.skill_id !== skillId ||
      persistedPolicy.max_open_proposals !== 1 ||
      persistedPolicy.generator_version !== GENERATOR_VERSION ||
      persistedPolicy.comparison_policy_version !== COMPARISON_POLICY_VERSION ||
      (suites.isSuccess &&
        (suiteProvenanceMismatch ||
          !knownSuiteIds.includes(persistedPolicy.evaluation_suite_id))));

  useEffect(() => {
    if (draft !== null) return;
    if (persistedPolicy !== undefined) {
      setDraft(toDraft(persistedPolicy));
    } else if (policyNotFound) {
      setDraft(DEFAULT_DRAFT);
    }
  }, [draft, persistedPolicy, policyNotFound]);

  function updateDraft(patch: Partial<PolicyDraft>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
    setValidationError(null);
    setSaveMessage(null);
  }

  function validateDraft(value: PolicyDraft): string | null {
    const numericFields: Array<
      [string, number | "", (value: number) => boolean]
    > = [
      [
        "Minimum usage records",
        value.minimum_usage_records,
        (item) => item >= 0,
      ],
      [
        "Minimum failed usage records",
        value.minimum_failures,
        (item) => item >= 0,
      ],
      [
        "Minimum harmful feedback records",
        value.minimum_harmful_feedback,
        (item) => item >= 0,
      ],
      ["Cooldown seconds", value.cooldown_seconds, (item) => item >= 0],
      [
        "Evidence window size",
        value.evidence_window_size,
        (item) => item >= 1 && item <= 200,
      ],
    ];
    for (const [label, item, constraint] of numericFields) {
      if (
        item === "" ||
        !Number.isInteger(item) ||
        !Number.isFinite(item) ||
        !constraint(item)
      ) {
        return `${label} must satisfy the canonical integer constraint.`;
      }
    }
    if (!knownSuiteIds.includes(value.evaluation_suite_id)) {
      return "Select an evaluation suite belonging to this Skill.";
    }
    return null;
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (draft === null) return;
    const error = validateDraft(draft);
    if (error !== null) {
      setValidationError(error);
      return;
    }
    setValidationError(null);
    setSaveMessage(null);
    setRunMessage(null);
    savePolicy.mutate(policyBody(draft), {
      onSuccess: (saved) => {
        setDraft(toDraft(saved));
        setSaveMessage("Improvement policy saved.");
      },
    });
  }

  function requestProcessing() {
    setRunMessage(null);
    runNow.mutate(undefined, {
      onSuccess: (result) => setRunMessage(result.due ? "due" : "not-due"),
    });
  }

  return (
    <section aria-labelledby="skill-improvement-policy-heading">
      <h3 id="skill-improvement-policy-heading">Safe improvement policy</h3>
      <p>
        Friday freezes bounded evidence before its worker-owned, brain-only
        candidate loop. Policy configuration never grants Skill authority.
      </p>
      {policy.isLoading && <p>Loading Skill improvement policy...</p>}
      {policyLoadError && (
        <p role="alert">Failed to load Skill improvement policy.</p>
      )}
      {policyNotFound && (
        <p>No improvement policy is configured for this Skill.</p>
      )}
      {suites.isLoading && (
        <p>Loading evaluation suites for policy selection...</p>
      )}
      {suites.isError && (
        <p role="alert">
          Evaluation suites could not be loaded. Policy editing remains closed
          until an exact Skill suite can be verified.
        </p>
      )}
      {suiteProvenanceMismatch && (
        <p role="alert">Evaluation suite provenance could not be verified.</p>
      )}
      {persistedPolicy && policyProvenanceMismatch && (
        <>
          <p role="alert">
            Improvement policy provenance could not be verified. Policy editing
            is closed; the persisted values are shown below.
          </p>
          <PolicyFacts policy={persistedPolicy} />
        </>
      )}
      {persistedPolicy && !policyProvenanceMismatch && (
        <PolicyFacts policy={persistedPolicy} />
      )}

      {draft !== null &&
        !policyLoadError &&
        !policyProvenanceMismatch &&
        suiteSelectionReady && (
          <form onSubmit={submit} aria-label="Safe improvement policy editor">
            <label>
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) =>
                  updateDraft({ enabled: event.target.checked })
                }
              />
              Enabled
            </label>
            <label htmlFor="minimum-usage-records">Minimum usage records</label>
            <input
              id="minimum-usage-records"
              type="number"
              min={0}
              step={1}
              value={draft.minimum_usage_records}
              onChange={(event) =>
                updateDraft({
                  minimum_usage_records: numericValue(event.target.value),
                })
              }
            />
            <label htmlFor="minimum-failures">
              Minimum failed usage records
            </label>
            <input
              id="minimum-failures"
              type="number"
              min={0}
              step={1}
              value={draft.minimum_failures}
              onChange={(event) =>
                updateDraft({
                  minimum_failures: numericValue(event.target.value),
                })
              }
            />
            <label htmlFor="minimum-harmful-feedback">
              Minimum harmful feedback records
            </label>
            <input
              id="minimum-harmful-feedback"
              type="number"
              min={0}
              step={1}
              value={draft.minimum_harmful_feedback}
              onChange={(event) =>
                updateDraft({
                  minimum_harmful_feedback: numericValue(event.target.value),
                })
              }
            />
            <label htmlFor="improvement-evaluation-suite">
              Evaluation suite
            </label>
            <select
              id="improvement-evaluation-suite"
              value={draft.evaluation_suite_id}
              onChange={(event) =>
                updateDraft({ evaluation_suite_id: event.target.value })
              }
              disabled={
                suites.isLoading || suites.isError || suiteProvenanceMismatch
              }
            >
              <option value="">Select an evaluation suite</option>
              {suiteItems.map((suite: EvaluationSuite) => (
                <option key={suite.id} value={suite.id}>
                  {suite.name} ({suite.id}) — {suite.status},{" "}
                  {suite.cases.length} cases
                </option>
              ))}
            </select>
            <label htmlFor="cooldown-seconds">Cooldown seconds</label>
            <input
              id="cooldown-seconds"
              type="number"
              min={0}
              step={1}
              value={draft.cooldown_seconds}
              onChange={(event) =>
                updateDraft({
                  cooldown_seconds: numericValue(event.target.value),
                })
              }
            />
            <label htmlFor="evidence-window-size">Evidence window size</label>
            <input
              id="evidence-window-size"
              type="number"
              min={1}
              max={200}
              step={1}
              value={draft.evidence_window_size}
              onChange={(event) =>
                updateDraft({
                  evidence_window_size: numericValue(event.target.value),
                })
              }
            />
            <dl>
              <dt>Maximum open proposals</dt>
              <dd>1</dd>
              <dt>Generator version</dt>
              <dd>{GENERATOR_VERSION}</dd>
              <dt>Comparison policy version</dt>
              <dd>{COMPARISON_POLICY_VERSION}</dd>
            </dl>
            <p>
              Generator and comparison versions are code-owned and cannot be
              entered by the operator.
            </p>
            <button type="submit" disabled={savePolicy.isPending}>
              {savePolicy.isPending
                ? "Saving improvement policy..."
                : "Save improvement policy"}
            </button>
            {validationError && <p role="alert">{validationError}</p>}
            {savePolicy.isError && (
              <p role="alert">
                Failed to save improvement policy:{" "}
                {formatError(savePolicy.error)}
              </p>
            )}
            {saveMessage && <p>{saveMessage}</p>}
          </form>
        )}

      {persistedPolicy && !policyProvenanceMismatch && suiteSelectionReady && (
        <div>
          <button
            type="button"
            disabled={runNow.isPending}
            onClick={requestProcessing}
          >
            {runNow.isPending
              ? "Requesting improvement processing..."
              : "Request improvement processing"}
          </button>
          {runNow.isError && (
            <p role="alert">
              Failed to request improvement processing:{" "}
              {formatError(runNow.error)}
            </p>
          )}
          {runMessage === "due" && (
            <p>
              Improvement processing is due or already has active work. This
              does not prove that a candidate has been generated yet. Refresh
              proposals to observe durable results.
            </p>
          )}
          {runMessage === "not-due" && (
            <p>
              No improvement processing was due under the current persisted
              policy.
            </p>
          )}
        </div>
      )}
      <p>
        Evidence is observation. A candidate is a suggestion. Evaluation is
        evidence. A recommendation is not approval, and this surface cannot
        promote or roll back a Skill.
      </p>
    </section>
  );
}
