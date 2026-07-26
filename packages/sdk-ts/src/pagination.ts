import type { Page } from "@friday/contracts";
export async function* paginate<T>(
  fetchPage: (cursor: string | undefined) => Promise<Page<T>>,
): AsyncGenerator<T, void, void> {
  let cursor: string | undefined;
  let first = true;
  while (first || cursor !== undefined) {
    first = false;
    const page = await fetchPage(cursor);
    yield* page.items;
    cursor = page.next_cursor ?? undefined;
  }
}
