import type { ApiErrorBody, WireValidator } from "@friday/contracts";
export interface FridayHttpClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  defaultTimeoutMs?: number;
}
export class FridayApiError extends Error {
  constructor(
    readonly status: number,
    readonly errorType: string,
    message: string,
    readonly details: Record<string, string>,
  ) {
    super(message);
    this.name = "FridayApiError";
  }
}
export class FridayNetworkError extends Error {
  constructor(
    message: string,
    readonly cause: unknown,
  ) {
    super(message);
    this.name = "FridayNetworkError";
  }
}
export interface FridayRequestOptions {
  method: "GET" | "POST";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  signal?: AbortSignal;
}
export interface FridayJsonRequestOptions extends FridayRequestOptions {
  /** Chosen by the resource operation; routes never determine response shape. */
  validate: WireValidator;
}
export class FridayHttpClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeout: number;
  constructor(options: FridayHttpClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    // Resolved per call, not captured: a client built at module-evaluation time
    // must still honour a `fetch` installed on the global afterwards (test
    // doubles, polyfills), which a captured reference would bypass.
    this.fetchImpl =
      options.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
    this.timeout = options.defaultTimeoutMs ?? 30_000;
  }
  async requestJson<T>(options: FridayJsonRequestOptions): Promise<T> {
    const url = new URL(`${this.baseUrl}${options.path}`);
    for (const [key, value] of Object.entries(options.query ?? {}))
      if (value !== undefined) url.searchParams.set(key, String(value));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    const externalAbort = () => controller.abort();
    options.signal?.addEventListener("abort", externalAbort);
    let response: Response;
    try {
      response = await this.fetchImpl(url.toString(), {
        method: options.method,
        headers:
          options.body === undefined
            ? undefined
            : { "Content-Type": "application/json" },
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: controller.signal,
      });
    } catch (cause) {
      if (options.signal?.aborted) throw cause;
      throw new FridayNetworkError(`Request to ${url} failed`, cause);
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", externalAbort);
    }
    if (!response.ok) {
      let body: ApiErrorBody | undefined;
      try {
        body = (await response.json()) as ApiErrorBody;
      } catch {
        /* opaque error */
      }
      const error = body?.error;
      throw new FridayApiError(
        response.status,
        error?.type ?? "unknown_error",
        error?.message ?? response.statusText,
        error?.details ?? {},
      );
    }
    if (response.status === 204) return undefined as T;
    const body: unknown = await response.json();
    options.validate(body, options.path);
    return body as T;
  }
  async requestVoid(options: FridayRequestOptions): Promise<void> {
    return this.requestJson<void>({ ...options, validate: () => undefined });
  }
  /** Raw JSON escape hatch for endpoints without a versioned wire contract. */
  async request<T>(options: FridayRequestOptions): Promise<T> {
    return this.requestJson<T>({ ...options, validate: () => undefined });
  }
}
