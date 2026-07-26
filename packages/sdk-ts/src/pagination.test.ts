import { describe, expect, it, vi } from "vitest";
import { paginate } from "./pagination";
describe("paginate", () => {
  it("yields all items and forwards opaque cursors", async () => {
    const cursors: (string | undefined)[] = [];
    const items: number[] = [];
    for await (const item of paginate(async (cursor) => {
      cursors.push(cursor);
      return cursor
        ? { items: [3], next_cursor: null }
        : { items: [1, 2], next_cursor: "opaque" };
    }))
      items.push(item);
    expect(items).toEqual([1, 2, 3]);
    expect(cursors).toEqual([undefined, "opaque"]);
  });
  it("yields nothing and fetches once for an empty first page", async () => {
    const fetchPage = vi.fn(async () => ({ items: [], next_cursor: null }));
    const items: number[] = [];
    for await (const item of paginate<number>(fetchPage)) items.push(item);
    expect(items).toEqual([]);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
  it("stops as soon as the consumer breaks out of the loop", async () => {
    const fetchPage = vi.fn(async () => ({ items: [1, 2], next_cursor: "c" }));
    for await (const item of paginate<number>(fetchPage)) {
      expect(item).toBe(1);
      break;
    }
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
});
