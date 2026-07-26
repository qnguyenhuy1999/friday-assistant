import type {
  Artifact,
  MarkFailedBody,
  MarkSucceededBody,
  RecordArtifactBody,
  RequestToolInvocationBody,
  ToolInvocation,
} from "../../index";

export const requestToolInvocationBodyExample: RequestToolInvocationBody = {
  tool_name: "shell.run",
  requested_input: { command: "git clone https://example.test/repo.git" },
};

export const toolInvocationExample: ToolInvocation = {
  invocation_id: "8f14e45f-ceea-467e-adde-3f4694a0aaaa",
  run_id: "8f14e45f-ceea-467e-adde-3f4694a05678",
  step_id: null,
  tool_name: "shell.run",
  status: "succeeded",
  requested_at: "2026-07-26T00:00:03Z",
  approval_request_id: null,
  output: { exit_code: 0 },
  output_set: true,
  failure: null,
};

export const markSucceededBodyExample: MarkSucceededBody = {
  output: { exit_code: 0 },
};

export const markFailedBodyExample: MarkFailedBody = {
  failure: {
    code: "tool_error",
    message: "clone failed",
    retryable: true,
    cause: "tool",
    details: null,
  },
};

export const recordArtifactBodyExample: RecordArtifactBody = {
  kind: "file",
  name: "repo",
  media_type: "inode/directory",
  location: "/tmp/repo",
};

export const artifactExample: Artifact = {
  artifact_id: "8f14e45f-ceea-467e-adde-3f4694a0bbbb",
  run_id: toolInvocationExample.run_id,
  step_id: null,
  kind: "file",
  name: "repo",
  media_type: "inode/directory",
  location: "/tmp/repo",
  created_at: "2026-07-26T00:00:04Z",
  size: null,
  checksum: null,
  metadata: null,
};
