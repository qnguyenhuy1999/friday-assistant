export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
