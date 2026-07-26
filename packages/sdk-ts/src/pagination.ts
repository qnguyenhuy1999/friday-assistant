import type { Page } from "@friday/contracts";
export async function* paginate<T>(
  fetchPage: (cursor: string | undefined) => Promise<Page<T>>,
): AsyncGenerator<T, void, void> {
  let cursor: string | undefined;
  const seenCursors = new Set<string>();
  let first = true;
  while (first || cursor !== undefined) {
    first = false;
    if (cursor !== undefined) {
      if (seenCursors.has(cursor))
        throw new Error(
          "Pagination response repeated a cursor; refusing an infinite loop.",
        );
      seenCursors.add(cursor);
    }
    const page = await fetchPage(cursor);
    yield* page.items;
    cursor = page.next_cursor ?? undefined;
  }
}
