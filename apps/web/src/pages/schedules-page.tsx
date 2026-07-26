import { useState, type FormEvent } from "react";
import {
  useCreateSchedule,
  useScheduleControl,
  useScheduleFires,
  useSchedules,
} from "../hooks/use-schedules";

function ScheduleRow({
  taskId,
  schedule,
  onInspect,
}: {
  taskId: string;
  schedule: {
    id: string;
    kind: string;
    cron: string | null;
    run_at: string | null;
    timezone: string;
    status: string;
    next_fire_at: string | null;
  };
  onInspect: (id: string) => void;
}) {
  const control = useScheduleControl(taskId, schedule.id);
  return (
    <li>
      <strong>{schedule.kind}</strong> — {schedule.status} — next:{" "}
      {schedule.next_fire_at ?? "none"}
      {schedule.kind === "cron"
        ? ` (${schedule.cron})`
        : ` (${schedule.run_at})`}
      <button onClick={() => onInspect(schedule.id)}>Inspect fires</button>
      {schedule.status === "active" && (
        <button
          disabled={control.isPending}
          onClick={() => control.mutate("pause")}
        >
          Pause
        </button>
      )}
      {schedule.status === "paused" && (
        <button
          disabled={control.isPending}
          onClick={() => control.mutate("resume")}
        >
          Resume
        </button>
      )}
      {(schedule.status === "active" || schedule.status === "paused") && (
        <button
          disabled={control.isPending}
          onClick={() => control.mutate("cancel")}
        >
          Cancel
        </button>
      )}
      {control.isError && <span role="alert"> Failed to update schedule.</span>}
    </li>
  );
}

export function SchedulesPage({
  taskId,
  onBack,
  onViewRun,
}: {
  taskId: string;
  onBack: () => void;
  onViewRun: (runId: string) => void;
}) {
  const schedules = useSchedules(taskId);
  const create = useCreateSchedule(taskId);
  const [kind, setKind] = useState<"once" | "cron">("once");
  const [runAt, setRunAt] = useState("");
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [timezone, setTimezone] = useState("UTC");
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const fires = useScheduleFires(taskId, inspectedId);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (kind === "once" && runAt)
      // datetime-local is wall time. Keep it raw; the API interprets it in
      // the selected IANA zone instead of accidentally applying browser TZ.
      create.mutate({ kind, run_at: runAt, timezone });
    if (kind === "cron" && cron.trim())
      create.mutate({ kind, cron: cron.trim(), timezone });
  }
  return (
    <section>
      <button onClick={onBack}>Back to tasks</button>
      <h2>Schedules</h2>
      <p>
        Runs are created at the scheduled time and still follow the normal
        approval path.
      </p>
      <form onSubmit={submit}>
        <label>
          Kind{" "}
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as "once" | "cron")}
          >
            <option value="once">One time</option>
            <option value="cron">Cron</option>
          </select>
        </label>
        {kind === "once" ? (
          <label>
            Run at{" "}
            <input
              aria-label="Run at"
              type="datetime-local"
              value={runAt}
              onChange={(event) => setRunAt(event.target.value)}
              required
            />
          </label>
        ) : (
          <label>
            Cron{" "}
            <input
              value={cron}
              onChange={(event) => setCron(event.target.value)}
              required
            />
          </label>
        )}
        <label>
          Timezone{" "}
          <input
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            required
          />
        </label>
        <button type="submit" disabled={create.isPending}>
          Create schedule
        </button>
      </form>
      {create.isError && <p role="alert">Failed to create schedule.</p>}
      {schedules.isLoading && <p>Loading schedules…</p>}
      {schedules.isError && <p role="alert">Failed to load schedules.</p>}
      <ul>
        {schedules.data?.items.map((schedule) => (
          <ScheduleRow
            key={schedule.id}
            taskId={taskId}
            schedule={schedule}
            onInspect={setInspectedId}
          />
        ))}
      </ul>
      {inspectedId && (
        <section>
          <h3>Schedule fires</h3>
          {fires.isLoading && <p>Loading fires…</p>}
          {fires.isError && <p role="alert">Failed to load fires.</p>}
          <ul>
            {fires.data?.items.map((fire) => (
              <li key={fire.id}>
                {fire.scheduled_for} →{" "}
                <button onClick={() => onViewRun(fire.run_id)}>
                  run {fire.run_id}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
