import type {
  CreateEvaluationSuiteBody,
  EvaluationSuite,
  JsonValue,
} from "@friday/contracts";
import { useRef, useState, type FormEvent } from "react";
import { useCreateSkillEvaluationSuite } from "../hooks/use-skill-evaluations";

const MAX_NAME_LENGTH = 256;
const MAX_DESCRIPTION_LENGTH = 4000;
const MAX_CASES = 100;
const MAX_CASE_INPUT_LENGTH = 32_000;
type EvaluationGradingKind =
  CreateEvaluationSuiteBody["cases"][number]["grading_kind"];

interface DraftCase {
  draftId: string;
  input: string;
  gradingKind: EvaluationGradingKind;
  exactValue: string;
  values: string[];
  schemaText: string;
  requiredTool: string;
  requiredInputKeys: string[];
}

function emptyDraftCase(draftId: string): DraftCase {
  return {
    draftId,
    input: "",
    gradingKind: "exact_match",
    exactValue: "",
    values: [""],
    schemaText: '{\n  "type": "object"\n}',
    requiredTool: "",
    requiredInputKeys: [],
  };
}

function isJsonObject(value: unknown): value is { [key: string]: JsonValue } {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expectedProperties(
  draft: DraftCase,
  caseNumber: number,
): Record<string, JsonValue> {
  switch (draft.gradingKind) {
    case "exact_match":
      return { value: draft.exactValue };
    case "contains_all":
    case "contains_none":
      return { values: draft.values };
    case "required_keys":
      return { required_keys: draft.values };
    case "json_schema": {
      let parsed: unknown;
      try {
        parsed = JSON.parse(draft.schemaText);
      } catch {
        throw new Error(`Case ${caseNumber} JSON schema must be valid JSON.`);
      }
      if (!isJsonObject(parsed)) {
        throw new Error(`Case ${caseNumber} JSON schema must be an object.`);
      }
      return { schema: parsed };
    }
    case "tool_proposal_shape": {
      const expected: Record<string, JsonValue> = {};
      if (draft.requiredTool !== "")
        expected.required_tool = draft.requiredTool;
      if (draft.requiredInputKeys.length > 0)
        expected.required_input_keys = draft.requiredInputKeys;
      return expected;
    }
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The server rejected this suite.";
}

function expectationLabel(kind: EvaluationGradingKind): string {
  switch (kind) {
    case "contains_all":
      return "Expected value";
    case "contains_none":
      return "Forbidden value";
    case "required_keys":
      return "Required key";
    default:
      return "Expected item";
  }
}

export function CreateEvaluationSuiteForm({
  skillId,
  onCreated,
}: {
  skillId: string;
  onCreated: (suite: EvaluationSuite) => void;
}) {
  const create = useCreateSkillEvaluationSuite(skillId);
  const nextDraftId = useRef(2);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [cases, setCases] = useState<DraftCase[]>([
    emptyDraftCase("draft-case-1"),
  ]);
  const [validationError, setValidationError] = useState<string | null>(null);

  function updateCase(draftId: string, patch: Partial<DraftCase>) {
    setCases((current) =>
      current.map((item) =>
        item.draftId === draftId ? { ...item, ...patch } : item,
      ),
    );
  }

  function addCase() {
    if (cases.length >= MAX_CASES) return;
    const draftId = `draft-case-${nextDraftId.current}`;
    nextDraftId.current += 1;
    setCases((current) => [...current, emptyDraftCase(draftId)]);
  }

  function removeCase(draftId: string) {
    if (cases.length === 1) return;
    setCases((current) => current.filter((item) => item.draftId !== draftId));
  }

  function moveCase(index: number, offset: -1 | 1) {
    const nextIndex = index + offset;
    if (nextIndex < 0 || nextIndex >= cases.length) return;
    setCases((current) => {
      const next = [...current];
      const item = next[index];
      const destination = next[nextIndex];
      if (!item || !destination) return current;
      next[index] = destination;
      next[nextIndex] = item;
      return next;
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedName = name.trim();
    if (!normalizedName) {
      setValidationError("Suite name is required.");
      return;
    }
    if (
      normalizedName.length > MAX_NAME_LENGTH ||
      description.length > MAX_DESCRIPTION_LENGTH ||
      cases.length < 1 ||
      cases.length > MAX_CASES
    ) {
      setValidationError("One or more fields exceed the supported length.");
      return;
    }
    if (
      cases.some(
        (item) =>
          item.input.length < 1 || item.input.length > MAX_CASE_INPUT_LENGTH,
      )
    ) {
      setValidationError(
        `Each case input must be between 1 and ${MAX_CASE_INPUT_LENGTH} characters.`,
      );
      return;
    }

    let bodyCases: CreateEvaluationSuiteBody["cases"];
    try {
      bodyCases = cases.map((item, index) => ({
        input: item.input,
        expected_properties: expectedProperties(item, index + 1),
        grading_kind: item.gradingKind,
      }));
    } catch (error) {
      setValidationError(errorMessage(error));
      return;
    }

    setValidationError(null);
    create.mutate(
      { name: normalizedName, description, cases: bodyCases },
      {
        onSuccess: (suite) => {
          setName("");
          setDescription("");
          setCases([emptyDraftCase("draft-case-1")]);
          setValidationError(null);
          onCreated(suite);
        },
      },
    );
  }

  return (
    <section aria-labelledby="create-evaluation-suite-heading">
      <h4 id="create-evaluation-suite-heading">Create evaluation suite</h4>
      <p>
        Suite editing is local until you explicitly create one. Persisted suites
        and cases are read-only in this operator surface.
      </p>
      <form onSubmit={submit} aria-label="Create evaluation suite">
        <label htmlFor="evaluation-suite-name">Suite name</label>
        <input
          id="evaluation-suite-name"
          value={name}
          maxLength={MAX_NAME_LENGTH}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <label htmlFor="evaluation-suite-description">Description</label>
        <textarea
          id="evaluation-suite-description"
          value={description}
          maxLength={MAX_DESCRIPTION_LENGTH}
          onChange={(event) => setDescription(event.target.value)}
        />

        <ol aria-label="Evaluation suite draft cases">
          {cases.map((item, index) => (
            <li key={item.draftId}>
              <fieldset aria-label={`Evaluation case ${index + 1}`}>
                <legend>Evaluation case {index + 1}</legend>
                <label htmlFor={`${item.draftId}-input`}>Case input</label>
                <textarea
                  id={`${item.draftId}-input`}
                  value={item.input}
                  maxLength={MAX_CASE_INPUT_LENGTH}
                  onChange={(event) =>
                    updateCase(item.draftId, { input: event.target.value })
                  }
                  required
                />
                <label htmlFor={`${item.draftId}-grading-kind`}>
                  Grading kind
                </label>
                <select
                  id={`${item.draftId}-grading-kind`}
                  value={item.gradingKind}
                  onChange={(event) =>
                    updateCase(item.draftId, {
                      gradingKind: event.target.value as EvaluationGradingKind,
                    })
                  }
                >
                  <option value="exact_match">Exact match</option>
                  <option value="contains_all">Contains all</option>
                  <option value="contains_none">Contains none</option>
                  <option value="json_schema">JSON Schema</option>
                  <option value="required_keys">Required keys</option>
                  <option value="tool_proposal_shape">
                    Tool proposal shape
                  </option>
                </select>

                {item.gradingKind === "exact_match" && (
                  <>
                    <label htmlFor={`${item.draftId}-exact-value`}>
                      Expected exact value
                    </label>
                    <input
                      id={`${item.draftId}-exact-value`}
                      value={item.exactValue}
                      onChange={(event) =>
                        updateCase(item.draftId, {
                          exactValue: event.target.value,
                        })
                      }
                    />
                  </>
                )}

                {(item.gradingKind === "contains_all" ||
                  item.gradingKind === "contains_none" ||
                  item.gradingKind === "required_keys") && (
                  <ol aria-label={`${expectationLabel(item.gradingKind)}s`}>
                    {item.values.map((value, valueIndex) => (
                      <li key={`${item.draftId}-value-${valueIndex}`}>
                        <label htmlFor={`${item.draftId}-value-${valueIndex}`}>
                          {expectationLabel(item.gradingKind)} {valueIndex + 1}
                        </label>
                        <input
                          id={`${item.draftId}-value-${valueIndex}`}
                          value={value}
                          onChange={(event) => {
                            const values = [...item.values];
                            values[valueIndex] = event.target.value;
                            updateCase(item.draftId, { values });
                          }}
                        />
                        <button
                          type="button"
                          onClick={() =>
                            updateCase(item.draftId, {
                              values: item.values.filter(
                                (_, currentIndex) =>
                                  currentIndex !== valueIndex,
                              ),
                            })
                          }
                          disabled={item.values.length === 1}
                          aria-label={`Remove item ${valueIndex + 1} from ${expectationLabel(item.gradingKind).toLowerCase()}s`}
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                    <button
                      type="button"
                      onClick={() =>
                        updateCase(item.draftId, {
                          values: [...item.values, ""],
                        })
                      }
                    >
                      Add {expectationLabel(item.gradingKind).toLowerCase()}
                    </button>
                  </ol>
                )}

                {item.gradingKind === "json_schema" && (
                  <>
                    <label htmlFor={`${item.draftId}-schema`}>
                      JSON Schema expectation
                    </label>
                    <textarea
                      id={`${item.draftId}-schema`}
                      value={item.schemaText}
                      onChange={(event) =>
                        updateCase(item.draftId, {
                          schemaText: event.target.value,
                        })
                      }
                    />
                  </>
                )}

                {item.gradingKind === "tool_proposal_shape" && (
                  <>
                    <p>
                      This evaluator validates proposal structure only. No
                      proposed tool is executed.
                    </p>
                    <label htmlFor={`${item.draftId}-required-tool`}>
                      Required tool (optional)
                    </label>
                    <input
                      id={`${item.draftId}-required-tool`}
                      value={item.requiredTool}
                      onChange={(event) =>
                        updateCase(item.draftId, {
                          requiredTool: event.target.value,
                        })
                      }
                    />
                    <ol aria-label="Required input keys">
                      {item.requiredInputKeys.map((value, valueIndex) => (
                        <li key={`${item.draftId}-input-key-${valueIndex}`}>
                          <label
                            htmlFor={`${item.draftId}-input-key-${valueIndex}`}
                          >
                            Required input key {valueIndex + 1} (optional)
                          </label>
                          <input
                            id={`${item.draftId}-input-key-${valueIndex}`}
                            value={value}
                            onChange={(event) => {
                              const requiredInputKeys = [
                                ...item.requiredInputKeys,
                              ];
                              requiredInputKeys[valueIndex] =
                                event.target.value;
                              updateCase(item.draftId, { requiredInputKeys });
                            }}
                          />
                          <button
                            type="button"
                            onClick={() =>
                              updateCase(item.draftId, {
                                requiredInputKeys:
                                  item.requiredInputKeys.filter(
                                    (_, currentIndex) =>
                                      currentIndex !== valueIndex,
                                  ),
                              })
                            }
                            aria-label={`Remove item ${valueIndex + 1} from required input keys`}
                          >
                            Remove
                          </button>
                        </li>
                      ))}
                      <button
                        type="button"
                        onClick={() =>
                          updateCase(item.draftId, {
                            requiredInputKeys: [...item.requiredInputKeys, ""],
                          })
                        }
                      >
                        Add required input key
                      </button>
                    </ol>
                  </>
                )}

                <p>
                  <button
                    type="button"
                    onClick={() => moveCase(index, -1)}
                    disabled={index === 0}
                    aria-label={`Move case ${index + 1} up`}
                  >
                    Move up
                  </button>{" "}
                  <button
                    type="button"
                    onClick={() => moveCase(index, 1)}
                    disabled={index === cases.length - 1}
                    aria-label={`Move case ${index + 1} down`}
                  >
                    Move down
                  </button>{" "}
                  <button
                    type="button"
                    onClick={() => removeCase(item.draftId)}
                    disabled={cases.length === 1}
                  >
                    Remove case
                  </button>
                </p>
              </fieldset>
            </li>
          ))}
        </ol>
        <button
          type="button"
          onClick={addCase}
          disabled={cases.length >= MAX_CASES}
        >
          Add case
        </button>
        <button type="submit" disabled={create.isPending}>
          Create evaluation suite
        </button>
      </form>
      {validationError && <p role="alert">{validationError}</p>}
      {create.isError && (
        <p role="alert">
          Failed to create evaluation suite: {errorMessage(create.error)}
        </p>
      )}
    </section>
  );
}
