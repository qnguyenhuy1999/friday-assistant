import { useState, type FormEvent } from "react";
import { useCreateTask, useStartRun, useTasks } from "../hooks/use-tasks";
export function TasksPage({
  onRunStarted,
  onViewSchedules = () => undefined,
}: {
  onRunStarted: (runId: string) => void;
  onViewSchedules?: (taskId: string) => void;
}) {
  const {
    data,
    isLoading,
    isError,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useTasks();
  const create = useCreateTask();
  const start = useStartRun();
  const [title, setTitle] = useState("");
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const value = title.trim();
    if (value)
      create.mutate({ title: value }, { onSuccess: () => setTitle("") });
  }
  return (
    <section>
      <h2>Tasks</h2>
      <form onSubmit={submit}>
        <label htmlFor="task-title">Title</label>
        <input
          id="task-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button type="submit" disabled={create.isPending}>
          Create task
        </button>
      </form>
      {create.isError && <p role="alert">Failed to create the task.</p>}
      {start.isError && <p role="alert">Failed to start the run.</p>}
      {isLoading && <p>Loading tasks…</p>}
      {isError && <p role="alert">Failed to load tasks.</p>}
      <ul>
        {data?.pages
          .flatMap((page) => page.items)
          .map((task) => (
            <li key={task.id}>
              {task.title} — {task.status}{" "}
              <button
                disabled={start.isPending}
                onClick={() =>
                  start.mutate(task.id, {
                    onSuccess: (r) => onRunStarted(r.run_id),
                  })
                }
              >
                Start run
              </button>
              <button onClick={() => onViewSchedules(task.id)}>
                Schedules
              </button>
            </li>
          ))}
      </ul>
      {hasNextPage && (
        <button
          type="button"
          disabled={isFetchingNextPage}
          onClick={() => void fetchNextPage()}
        >
          {isFetchingNextPage ? "Loading more…" : "Load more"}
        </button>
      )}
    </section>
  );
}
