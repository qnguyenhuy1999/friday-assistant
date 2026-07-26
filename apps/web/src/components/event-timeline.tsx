import type { RunEvent } from "@friday/contracts";
export function EventTimeline({ events }: { events: RunEvent[] }) {
  return (
    <ul aria-label="Event timeline">
      {events.map((e) => (
        <li key={e.event_id}>
          {e.occurred_at} — {e.type}
        </li>
      ))}
    </ul>
  );
}
