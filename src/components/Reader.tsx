import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  MODEL_MISSING,
  bookChapter,
  bookOpen,
  commandPrefix,
  message,
  type BookView,
  type ChapterView,
  type ColoredSentence,
  type NoteBody,
  type ParagraphView,
  type Role,
} from "../lib/reading";
import { ModalBackdrop, useDialog } from "./dialog";

interface Props {
  id: string;
  /** Called when the reader closes, so the shelf can restore its scroll. */
  onClose: () => void;
  /** Opens settings, for the "download a model" prompt. */
  onOpenSettings: () => void;
  /** Whether settings is on screen above the page. */
  modelsOpen: boolean;
}

/**
 * The reading view: one chapter at a time, with a chapter list.
 *
 * Text arrives already split into sentences and colored spans, so this component
 * only lays markup out — it never measures or splits strings. Spans come from
 * the Rust side with UTF-16 offsets; rendering them directly keeps the DOM
 * identical to what those offsets describe, which is what a later scroll
 * position or annotation will rely on.
 */
export function Reader({ id, onClose, onOpenSettings, modelsOpen }: Props) {
  const [book, setBook] = useState<BookView | null>(null);
  const [chapter, setChapter] = useState<ChapterView | null>(null);
  const [index, setIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [missingModel, setMissingModel] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  // The footnote a marker opened, or none. One dialog for the whole chapter:
  // every marker click swaps its content instead of stacking dialogs.
  const [note, setNote] = useState<NoteBody | null>(null);
  // Bumped to re-ask for the chapter. Downloading a model in settings is the
  // common reason a chapter failed, and the reader must not sit on that error
  // until the user navigates away and back.
  const [attempt, setAttempt] = useState(0);
  const wasOpen = useRef(modelsOpen);
  const content = useRef<HTMLDivElement>(null);

  // Opening a book is the only parse that has to happen before anything shows.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // The previous book's chapter belongs to a book the page is no longer
    // showing, and `index` will point into the new one in a moment.
    setChapter(null);
    setIndex(null);
    bookOpen(id)
      .then((opened) => {
        if (cancelled) return;
        setBook(opened);
        // The first chapter with text: a book usually starts with a cover page
        // and a title page, and landing on those looks like a failure.
        const first =
          opened.chapters.find((entry) => entry.paragraphs > 0) ?? opened.chapters[0] ?? null;
        setIndex(first ? first.index : null);
        if (first === null || first === undefined) {
          setLoading(false);
          setError("这本书没有可阅读的正文。");
        }
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setLoading(false);
        setError(message(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    // `index === null` is the gap between opening a book and knowing its first
    // chapter. Requiring the loaded book to be this book is what keeps a
    // switch from asking for the previous book's chapter number: `index` is
    // cleared above, but the render that still carries the old value must not
    // fire a request either.
    if (index === null || book?.id !== id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    bookChapter(id, index)
      .then((loaded) => {
        if (cancelled) return;
        setChapter(loaded);
        setLoading(false);
        // A chapter that arrived means whatever failed before no longer does;
        // leaving the flag set would make the next trip to settings refetch a
        // chapter for no reason.
        setMissingModel(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setLoading(false);
        // A missing model is not a failure to show a raw error for: it is
        // something the user can fix, and the prompt says how.
        setMissingModel(commandPrefix(cause) === MODEL_MISSING);
        setError(message(cause));
      });
    return () => {
      cancelled = true;
    };
    // `book` enters as the primitive `book?.id`: the guard below is the only
    // use, and a dependency on the object would re-run this effect on every
    // parse that lands, for a fetch the previous run already decided against
    // (rerender-dependencies).
  }, [id, index, book?.id, attempt]);

  // Coming back from settings after a failed chapter is worth one more try:
  // "download a model" is the reason the prompt sent the user there.
  useEffect(() => {
    if (wasOpen.current && !modelsOpen && missingModel) setAttempt((n) => n + 1);
    wasOpen.current = modelsOpen;
  }, [modelsOpen, missingModel]);

  // A new chapter starts at the top, the way turning a page does. `scrollTop`
  // rather than `scrollTo`: the assignment is a no-op where smooth scrolling is
  // unavailable (old webviews, jsdom) instead of a crash.
  useEffect(() => {
    if (content.current) content.current.scrollTop = 0;
  }, [chapter]);

  const position = useMemo(() => {
    if (!book || index === null) return null;
    const at = book.chapters.findIndex((entry) => entry.index === index);
    if (at < 0) return null;
    return {
      at,
      total: book.chapters.length,
      previous: book.chapters[at - 1],
      next: book.chapters[at + 1],
    };
  }, [book, index]);

  // Arrow keys and Escape, but never while the user is typing or has a menu open.
  // The handler reads `position`/`tocOpen` through refs, so the listener is
  // attached once for the page's lifetime instead of on every chapter flip or
  // contents-panel toggle (rerender-dependencies / advanced-use-latest).
  const positionRef = useRef(position);
  const tocRef = useRef(tocOpen);
  useEffect(() => {
    positionRef.current = position;
    tocRef.current = tocOpen;
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      // Settings can be open above the page: Escape there dismisses settings,
      // not the book, and an arrow key must not turn the page behind a modal.
      if (document.querySelector("dialog[open]") !== null) return;
      if (event.key === "Escape") {
        if (tocRef.current) setTocOpen(false);
        else onClose();
      } else if (event.key === "ArrowRight") {
        const next = positionRef.current?.next;
        if (next) setIndex(next.index);
      } else if (event.key === "ArrowLeft") {
        const previous = positionRef.current?.previous;
        if (previous) setIndex(previous.index);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="flex min-h-dvh flex-col bg-base-100 text-base-content">
      <header className="sticky top-0 z-10 border-b border-base-content/10 bg-base-100/90 backdrop-blur">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-2 px-2 py-2 sm:px-4">
          <button type="button" className="btn btn-ghost btn-sm gap-1" onClick={onClose}>
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              className="size-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M11.5 5 6.5 10l5 5" />
            </svg>
            书架
          </button>
          <div className="min-w-0 flex-1 text-center">
            <p className="truncate text-sm font-medium">
              {/* `book` lags the switch (the parse is what sets it): naming the
                  old book while opening the new one would be a lie. */}
              {book?.id === id ? book.title : "正在打开…"}
            </p>
            {chapter?.title && (
              <p className="truncate text-xs text-base-content/55">{chapter.title}</p>
            )}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-expanded={tocOpen}
            onClick={() => setTocOpen((open) => !open)}
          >
            目录
          </button>
        </div>
      </header>

      {tocOpen && book && (
        <nav
          aria-label="章节目录"
          className="border-b border-base-content/10 bg-base-200/50"
        >
          <ul className="menu mx-auto w-full max-w-3xl max-h-[60vh] flex-nowrap overflow-y-auto px-2 py-2">
            {book.chapters.map((entry) => (
              <li key={entry.index}>
                <button
                  type="button"
                  className={entry.index === index ? "menu-active" : undefined}
                  // A spine document with no prose (a cover, a copyright page) is
                  // still listed: hiding it would renumber the chapter list away
                  // from the book's own numbering.
                  disabled={entry.paragraphs === 0}
                  onClick={() => {
                    setIndex(entry.index);
                    setTocOpen(false);
                  }}
                >
                  <span className="min-w-0 flex-1 truncate text-left">
                    {entry.title ?? `第 ${entry.index} 节`}
                  </span>
                  {entry.paragraphs === 0 && (
                    <span className="badge badge-ghost badge-sm">无正文</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      )}

      <main
        ref={content}
        className="mx-auto w-full max-w-3xl flex-1 px-5 py-6 sm:px-8 sm:py-10"
        aria-busy={loading}
      >
        {error !== null ? (
          <div role="alert" className="alert alert-error items-start">
            <div className="min-w-0 flex-1">
              <p className="font-medium">
                {missingModel ? "主谓宾着色需要分析模型" : "无法打开这一章"}
              </p>
              <p className="mt-1 text-sm break-words opacity-80">
                {missingModel
                  ? "在设置中下载一次；之后应用即可离线使用。"
                  : error}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button type="button" className="btn btn-sm" onClick={() => setAttempt((n) => n + 1)}>
                重试
              </button>
              {missingModel && (
                <button type="button" className="btn btn-sm btn-primary" onClick={onOpenSettings}>
                  打开设置
                </button>
              )}
            </div>
          </div>
        ) : loading || chapter === null ? (
          <div className="space-y-4">
            {Array.from({ length: 8 }, (_, line) => (
              <div
                key={line}
                className="skeleton h-4 rounded"
                // Ragged line lengths, so the placeholder looks like prose.
                style={{ width: `${[100, 96, 99, 88, 100, 94, 72, 100][line]}%` }}
              />
            ))}
          </div>
        ) : (
          <article className="flex flex-col gap-4 text-[17px] leading-[1.85] sm:text-lg">
            {chapter.paragraphs.map((paragraph, at) => (
              <Paragraph
                key={at}
                paragraph={paragraph}
                notes={chapter.footnotes}
                onNote={setNote}
              />
            ))}
          </article>
        )}
      </main>

      {note !== null && <NoteDialog note={note} onClose={() => setNote(null)} />}

      <footer className="sticky bottom-0 border-t border-base-content/10 bg-base-100/90 backdrop-blur">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-2 px-2 py-2">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={!position?.previous}
            onClick={() => position?.previous && setIndex(position.previous.index)}
          >
            上一章
          </button>
          <p className="flex-1 text-center text-xs text-base-content/50">
            {position ? `${position.at + 1} / ${position.total}` : ""}
          </p>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={!position?.next}
            onClick={() => position?.next && setIndex(position.next.index)}
          >
            下一章
          </button>
        </div>
      </footer>
    </div>
  );
}

/**
 * One paragraph, rendered as the element its block kind names.
 *
 * A heading paragraph (`kind: "h2"` and friends) becomes that heading; daisyUI
 * has no heading component, so the sizes below are plain Tailwind type scale,
 * with margins on top of the article's flex gap for chapter-title rhythm.
 */
const HEADING_CLASS: Record<string, string> = {
  h1: "mt-10 text-2xl font-bold leading-snug first:mt-0",
  h2: "mt-8 text-xl font-bold leading-snug first:mt-0",
  h3: "mt-6 text-lg font-semibold leading-snug first:mt-0",
  h4: "mt-6 text-base font-semibold first:mt-0",
  h5: "mt-6 text-base font-medium first:mt-0",
  h6: "mt-6 text-sm font-semibold first:mt-0",
};

function Paragraph({
  paragraph,
  notes,
  onNote,
}: {
  paragraph: ParagraphView;
  notes: NoteBody[];
  onNote: (note: NoteBody) => void;
}) {
  const body = <ParagraphBody paragraph={paragraph} notes={notes} onNote={onNote} />;
  const headingClass = HEADING_CLASS[paragraph.kind];
  if (headingClass === undefined) {
    return <p>{body}</p>;
  }
  // `kind` is the Rust parser's `ParagraphKind::tag()`, so only real tags land
  // here; the cast is what lets one component render any of them.
  const Tag = paragraph.kind as "h1";
  return <Tag className={headingClass}>{body}</Tag>;
}

/** A stretch of paragraph text that renders as exactly one node. */
interface Piece {
  /** UTF-16 offsets into the paragraph text. */
  start: number;
  end: number;
  /** `null` renders as plain text; a role renders as its colored span. */
  role: Role | null;
  /** A footnote target renders as a marker button; `null` otherwise. */
  target: string | null;
}

/**
 * One paragraph's text: its sentences, each split into role spans, with
 * footnote markers laid over both.
 *
 * The pieces come from one walk over cut points — every sentence and segment
 * boundary plus every marker edge. Each cut-to-cut stretch asks first whether a
 * marker covers it (markers win, so a marker inside a colored span still gets
 * its button) and otherwise colors by the segment at that position.
 *
 * `normal` spans are rendered as plain text with no color rather than given a
 * class: a class on every span of every paragraph is a lot of markup for no
 * visual difference.
 *
 * There is no color-switch prop: whether spans exist is decided by the analyzer
 * (coloring off returns paragraphs with no sentences), so this component
 * renders whatever it is given and the page never disagrees with the setting —
 * and markers still work with coloring off, because they do not depend on the
 * analyzer at all.
 */
function ParagraphBody({
  paragraph,
  notes,
  onNote,
}: {
  paragraph: ParagraphView;
  notes: NoteBody[];
  onNote: (note: NoteBody) => void;
}) {
  const text = paragraph.colored.text;
  const nodes: ReactNode[] = [];
  let at = 0;
  for (const piece of layout(paragraph)) {
    if (piece.start > at) nodes.push(text.slice(at, piece.start));
    const slice = text.slice(piece.start, piece.end);
    if (piece.target !== null) {
      const note = notes.find((entry) => entry.id === piece.target);
      nodes.push(
        note !== undefined ? (
          // A marker is a small ghost button raised above the line: daisyUI
          // has no footnote component, and `btn-ghost btn-xs` keeps it from
          // shouting inside prose.
          <sup key={piece.start}>
            <button
              type="button"
              className="btn btn-ghost btn-xs align-super px-0.5 text-primary"
              onClick={() => onNote(note)}
            >
              {slice}
            </button>
          </sup>
        ) : (
          // A marker the parser could not resolve (the aside is gone, or it
          // named another document) stays the text it always was.
          slice
        ),
      );
    } else if (piece.role !== null) {
      nodes.push(
        <span key={piece.start} className={roleClass(piece.role)}>
          {slice}
        </span>,
      );
    } else {
      nodes.push(slice);
    }
    at = piece.end;
  }
  // The sentences are trimmed and the analyzer may skip characters, so the
  // text is the authority: anything the walk did not cover is still shown.
  if (at < text.length) nodes.push(text.slice(at));
  return <>{nodes}</>;
}

/**
 * The paragraph text cut into renderable pieces: sentence spans, and footnote
 * markers winning over them.
 *
 * Adjacent stretches with the same appearance merge, so the common paragraph —
 * a few colored sentences, no markers — renders exactly like the previous
 * sentence-per-span walk did.
 */
function layout(paragraph: ParagraphView): Piece[] {
  const { text, sentences } = paragraph.colored;
  const cuts = new Set<number>([0, text.length]);
  for (const sentence of sentences) {
    cuts.add(sentence.start);
    cuts.add(sentence.start + sentence.text.length);
    for (const segment of sentence.segments) {
      cuts.add(sentence.start + segment.start);
      cuts.add(sentence.start + segment.start + segment.text.length);
    }
  }
  for (const ref of paragraph.noteRefs) {
    cuts.add(ref.start);
    cuts.add(ref.start + ref.len);
  }
  const ordered = [...cuts]
    .filter((cut) => cut >= 0 && cut <= text.length)
    .sort((a, b) => a - b);

  const pieces: Piece[] = [];
  for (let at = 0; at < ordered.length - 1; at++) {
    const start = ordered[at];
    const end = ordered[at + 1];
    if (start >= end) continue;
    const marker = paragraph.noteRefs.find(
      (ref) => ref.start <= start && end <= ref.start + ref.len,
    );
    const piece: Piece = marker
      ? { start, end, role: null, target: marker.target }
      : { start, end, role: roleAt(sentences, start), target: null };
    const previous = pieces[pieces.length - 1];
    if (
      previous !== undefined &&
      previous.role === piece.role &&
      previous.target === piece.target &&
      previous.end === start
    ) {
      previous.end = end;
    } else {
      pieces.push(piece);
    }
  }
  return pieces;
}

/** The role of the character at paragraph offset `at`, or `null` for plain. */
function roleAt(sentences: ColoredSentence[], at: number): Role | null {
  const sentence = sentences.find(
    (entry) => entry.start <= at && at < entry.start + entry.text.length,
  );
  if (sentence === undefined) return null;
  const segment = sentence.segments.find(
    (entry) =>
      sentence.start + entry.start <= at &&
      at < sentence.start + entry.start + entry.text.length,
  );
  // A `normal` segment is prose: no class, like the gaps between segments.
  return segment !== undefined && segment.role !== "normal" ? segment.role : null;
}

function roleClass(role: Role): string {
  // daisyUI has no 主谓宾 tokens, and these colors come from bioCN itself so the
  // desktop app reads like the web version: see `index.css`.
  return `svo svo-${role}`;
}

/**
 * One footnote, shown as a daisyUI modal.
 *
 * The dialog mounts only while a note is open, so `open` is constant and the
 * effect is just the `showModal` on the way up; Escape and the backdrop call
 * `onClose`, which unmounts it — the next marker click gets a fresh dialog.
 */
function NoteDialog({ note, onClose }: { note: NoteBody; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useDialog(ref, true);
  return (
    <dialog ref={ref} className="modal" onClose={onClose}>
      <div className="modal-box">
        <p className="whitespace-pre-wrap leading-relaxed text-sm text-base-content/80">
          {note.text}
        </p>
        <div className="modal-action">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
      <ModalBackdrop onClose={onClose} />
    </dialog>
  );
}
