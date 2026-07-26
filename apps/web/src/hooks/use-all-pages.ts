import type { Page } from "@friday/contracts";
import { paginate } from "@friday/sdk";

/** Read every cursor page so collection views never mistake an old first page
 * for the current state of a run. */
export async function loadAllPages<T>(
  fetchPage: (cursor: string | undefined) => Promise<Page<T>>,
): Promise<Page<T>> {
  const items: T[] = [];
  for await (const item of paginate(fetchPage)) items.push(item);
  return { items, next_cursor: null };
}
