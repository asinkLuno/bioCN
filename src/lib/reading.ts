/**
 * Typed bridge to the Rust `settings` and `reader` modules.
 *
 * Shapes here mirror `src-tauri/src/settings.rs`, `analyzer.rs` and `reading.rs`
 * field for field; the offsets are UTF-16 because that is what a DOM `Range`
 * uses.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

/** `normal` spans are the sentence's punctuation and untouched words. */
export type Role = "normal" | "subject" | "predicate" | "object";

export interface Segment {
  text: string;
  /** UTF-16 offset of the span within its *sentence*. */
  start: number;
  role: Role;
}

export interface ColoredSentence {
  text: string;
  /** UTF-16 offset of the sentence within its paragraph. */
  start: number;
  segments: Segment[];
}

export interface ColoredParagraph {
  text: string;
  sentences: ColoredSentence[];
}

export interface NoteRef {
  /** UTF-16 offset of the marker within its paragraph text. */
  start: number;
  /** UTF-16 length of the marker run. */
  len: number;
  /** The footnote id this marker points at. */
  target: string;
}

/** One footnote's body, pulled out of the reading flow by the Rust parser. */
export interface NoteBody {
  /** The footnote's id, which `NoteRef.target` names. */
  id: string;
  /** The footnote's paragraphs, joined with spaces. */
  text: string;
}

export interface ParagraphView {
  /** The HTML tag this paragraph renders as: `"h1"`…`"h6"`, or `"p"`. */
  kind: string;
  colored: ColoredParagraph;
  /** Footnote markers whose characters sit in the paragraph text. */
  noteRefs: NoteRef[];
}

export interface ChapterSummary {
  index: number;
  title: string | null;
  paragraphs: number;
}

export interface BookView {
  id: string;
  title: string;
  chapters: ChapterSummary[];
}

export interface ChapterView {
  index: number;
  title: string | null;
  paragraphs: ParagraphView[];
  /** Footnotes in document order, keyed by `NoteRef.target`. */
  footnotes: NoteBody[];
}

/**
 * The `models-missing` prefix the analyzer uses when no weights are installed.
 *
 * A prefix rather than an error type because a command error crosses the IPC
 * bridge as a string; the reader turns it into the "download a model" prompt
 * instead of showing the raw message.
 */
export const MODEL_MISSING = "models-missing";

/** The message an IPC error carries: Tauri rejects with a string. */
export function message(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  return "出了点问题。";
}

/** A `book-missing` / `chapter-missing` / `models-missing` prefix, or `null`. */
export function commandPrefix(error: unknown): string | null {
  const match = /^([a-z-]+):/.exec(message(error));
  return match ? match[1] : null;
}

export const bookOpen = (id: string) => invoke<BookView>("book_open", { id });

export const bookChapter = (id: string, index: number) =>
  invoke<ChapterView>("book_chapter", { id, index });

/**
 * How far a whole-book coloring job has got.
 *
 * One job at a time (`AnalyzerState` holds a single one), so this is the whole
 * of the job's state: a book either has a job or it does not.
 */
export interface ColoringProgress {
  book: string;
  /** Sentences analyzed so far, and how many the chapters in flight have. */
  done: number;
  total: number;
  /** Chapters finished, and how many were uncolored when the job started. */
  chapters: number;
  chaptersTotal: number;
  /** Set once the job is over — finished, failed or cancelled. */
  finished: boolean;
  /** The user stopped it; what it colored stays colored, and it is not a failure. */
  cancelled: boolean;
  /** A failure that stopped the job; what it colored stays colored. */
  error: string | null;
}

/** The event the backend emits for every change of a coloring job. */
export const COLORING_EVENT = "coloring://progress";

/**
 * Colors every chapter of `id` that has none yet, in the background.
 *
 * Resolves as soon as the job is accepted — the work takes hours, and the app
 * must stay usable meanwhile. Progress arrives on `COLORING_EVENT`, and the
 * book cannot be opened until the job is done with it.
 */
export const colorizeBook = (id: string) => invoke<void>("colorize_book", { id });

/** The running job, for a menu that opened after it started. */
export const coloringProgress = () =>
  invoke<ColoringProgress | null>("coloring_progress");

/** Stops the running job after the chapter it is in. */
export const cancelColorizeBook = () => invoke<void>("colorize_book_cancel");

/**
 * Forgets a book's coloring: the cached analyses of its sentences and its
 * chapter markers, under every model variant. The next read or whole-book
 * pass re-analyzes from scratch.
 *
 * Refused while this book is being colored; a refusal arrives as a rejection.
 */
export const clearBookCache = (id: string) => invoke<void>("clear_book_cache", { id });

/**
 * Whether `id` has coloring to clear: any chapter marker, under any weights —
 * the same scope `clearBookCache` works on. Asked when a book menu opens, so
 * the 清除着色缓存 item can be honest about an empty cache.
 */
export const bookHasColoring = (id: string) => invoke<boolean>("book_has_coloring", { id });

/**
 * Whether `id` has coloring under the *active* weights — the only slice
 * exporting reads. Asked when a book menu opens, so 导出着色后的图书 can be
 * honest about a book that would export plain.
 */
export const bookHasActiveColoring = (id: string) =>
  invoke<boolean>("book_has_active_coloring", { id });

/** Subscribes to coloring progress. Returns the unsubscribe function. */
export async function onColoringProgress(
  handler: (progress: ColoringProgress) => void,
) {
  return listen<ColoringProgress>(COLORING_EVENT, (event) => handler(event.payload));
}
