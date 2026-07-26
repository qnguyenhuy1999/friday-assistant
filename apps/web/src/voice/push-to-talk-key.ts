const INTERACTIVE = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON"]);
export function isPushToTalkKey(event: KeyboardEvent): boolean {
  const target = event.target as {
    tagName?: string;
    isContentEditable?: boolean;
  } | null;
  return (
    event.code === "Space" &&
    !event.repeat &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey &&
    target !== null &&
    !target.isContentEditable &&
    !INTERACTIVE.has(target.tagName ?? "")
  );
}
