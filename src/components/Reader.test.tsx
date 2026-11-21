import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Reader } from "./Reader";
import * as reading from "../lib/reading";

vi.mock("../lib/reading", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/reading")>()),
  bookOpen: vi.fn(),
  bookChapter: vi.fn(),
}));

const openMock = vi.mocked(reading.bookOpen);
const chapterMock = vi.mocked(reading.bookChapter);

const book: reading.BookView = {
  id: "dune",
  title: "Dune",
  chapters: [
    { index: 0, title: null, paragraphs: 0 },
    { index: 1, title: "One", paragraphs: 2 },
    { index: 2, title: "Two", paragraphs: 1 },
  ],
};

const chapter: reading.ChapterView = {
  index: 1,
  title: "One",
  paragraphs: [
    {
      kind: "p",
      colored: {
        text: "他走了。",
        sentences: [
          {
            text: "他走了。",
            start: 0,
            segments: [
              { text: "他", start: 0, role: "subject" },
              { text: "走", start: 1, role: "predicate" },
              { text: "了。", start: 2, role: "normal" },
            ],
          },
        ],
      },
      noteRefs: [],
    },
  ],
  footnotes: [],
};

/** The prose is split into spans, so its text is not one DOM node. */
async function prose() {
  return waitFor(() => {
    const article = document.querySelector("article");
    expect(article).not.toBeNull();
    return article!;
  });
}

function renderReader(overrides: Partial<Parameters<typeof Reader>[0]> = {}) {
  return render(
    <Reader
      id="dune"
      onClose={() => {}}
      onOpenSettings={() => {}}
      modelsOpen={false}
      {...overrides}
    />,
  );
}

describe("reader", () => {
  beforeEach(() => {
    openMock.mockResolvedValue(book);
    chapterMock.mockResolvedValue(chapter);
  });

  it("opens at the first chapter that has text", async () => {
    renderReader();
    // Chapter 0 is a cover page with no prose, so chapter 1 is what opens.
    expect(await prose()).toHaveTextContent("他走了。");
    await waitFor(() => expect(chapterMock).toHaveBeenCalledWith("dune", 1));
    // The counter is the spine position, not the index among readable
    // chapters: a book's cover page still occupies slot one.
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("colors subjects and predicates but leaves punctuation alone", async () => {
    const { container } = renderReader();
    await prose();
    expect(container.querySelector(".svo-subject")).toHaveTextContent("他");
    expect(container.querySelector(".svo-predicate")).toHaveTextContent("走");
    // `normal` spans are plain text: no class, no color.
    expect(container.querySelectorAll(".svo")).toHaveLength(2);
  });

  it("keeps the space between two sentences", async () => {
    // Sentences are trimmed, so the whitespace between them belongs to the
    // paragraph, not to either sentence: dropping it would run the words
    // together ("slept.She").
    chapterMock.mockResolvedValue({
      ...chapter,
      paragraphs: [
        {
          kind: "p",
          colored: {
            text: "He slept. She woke.",
            sentences: [
              {
                text: "He slept.",
                start: 0,
                // Segments cover the whole sentence, punctuation included.
                segments: [
                  { text: "He ", start: 0, role: "normal" },
                  { text: "slept", start: 3, role: "predicate" },
                  { text: ".", start: 8, role: "normal" },
                ],
              },
              { text: "She woke.", start: 10, segments: [] },
            ],
          },
          noteRefs: [],
        },
      ],
      footnotes: [],
    });
    renderReader();
    await prose();
    expect(await prose()).toHaveTextContent(/He slept\. She woke\./);
  });

  it("offers settings instead of a raw error when no model is installed", async () => {
    const onOpenSettings = vi.fn();
    chapterMock.mockRejectedValue(
      `${reading.MODEL_MISSING}: 尚未下载模型（设置 → 分析模型）`,
    );
    renderReader({ onOpenSettings });
    const user = userEvent.setup();

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/需要分析模型/)).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "打开设置" }));
    expect(onOpenSettings).toHaveBeenCalled();
  });

  it("retries the chapter after settings closes", async () => {
    chapterMock.mockRejectedValueOnce(
      `${reading.MODEL_MISSING}: 尚未下载模型（设置 → 分析模型）`,
    );
    const { rerender } = render(
      <Reader
        id="dune"
        onClose={() => {}}
        onOpenSettings={() => {}}
          modelsOpen={false}
      />,
    );
    await screen.findByRole("alert");

    rerender(
      <Reader
        id="dune"
        onClose={() => {}}
        onOpenSettings={() => {}}
          modelsOpen
      />,
    );
    rerender(
      <Reader
        id="dune"
        onClose={() => {}}
        onOpenSettings={() => {}}
          modelsOpen={false}
      />,
    );
    // The model was just downloaded, so the page must come back on its own.
    expect(await prose()).toHaveTextContent("他走了。");
  });

  it("keeps every character when the spans do not cover the sentence", async () => {
    // The spans come from the model; the sentence text is what the splitter
    // found. If the two disagree, the page must still show the sentence — a
    // dropped character is a wrong book, a missing color is not.
    chapterMock.mockResolvedValue({
      ...chapter,
      paragraphs: [
        {
          kind: "p",
          colored: {
            text: "他走了。",
            sentences: [
              {
                text: "他走了。",
                start: 0,
                // The period is not covered by any span.
                segments: [
                  { text: "他", start: 0, role: "subject" },
                  { text: "走", start: 1, role: "predicate" },
                ],
              },
            ],
          },
          noteRefs: [],
        },
      ],
      footnotes: [],
    });
    const { container } = renderReader();
    await prose();
    expect(await prose()).toHaveTextContent("他走了。");
    expect(container.querySelector(".svo-subject")).toHaveTextContent("他");
  });

  it("does not ask the new book for the old book's chapter number", async () => {
    const { rerender } = renderReader();
    await prose();
    await waitFor(() => expect(chapterMock).toHaveBeenCalledWith("dune", 1));

    // The second book's readable chapter is number 7 — not 1, which is where
    // the previous book was left. Asking for it would flash "no chapter 1".
    const other: reading.BookView = {
      id: "emma",
      title: "Emma",
      chapters: [
        { index: 0, title: null, paragraphs: 0 },
        { index: 7, title: "Seven", paragraphs: 1 },
      ],
    };
    openMock.mockResolvedValue(other);
    chapterMock.mockClear();
    rerender(
      <Reader
        id="emma"
        onClose={() => {}}
        onOpenSettings={() => {}}
          modelsOpen={false}
      />,
    );

    await waitFor(() => expect(chapterMock).toHaveBeenCalledWith("emma", 7));
    expect(chapterMock).not.toHaveBeenCalledWith("emma", 1);
    expect(screen.queryByText(/无法打开这一章/)).not.toBeInTheDocument();
  });

  it("lists every spine document, including ones with no text", async () => {
    const user = userEvent.setup();
    renderReader();
    await prose();

    await user.click(screen.getByRole("button", { name: "目录" }));
    const nav = screen.getByRole("navigation", { name: "章节目录" });
    // Hiding a text-less document would renumber the list away from the book.
    const rows = within(nav).getAllByRole("button");
    expect(rows).toHaveLength(3);
    expect(within(nav).getByText("无正文")).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /第 0 节/ })).toBeDisabled();
  });

  it("turns pages with the arrow keys and stops at the ends", async () => {
    chapterMock.mockResolvedValue({ ...chapter, index: 2, title: "Two" });
    renderReader();
    await prose();

    await userEvent.keyboard("{ArrowRight}");
    await waitFor(() => expect(chapterMock).toHaveBeenCalledWith("dune", 2));

    chapterMock.mockClear();
    // Chapter 2 is the last one: the right arrow must not wrap or overshoot.
    await userEvent.keyboard("{ArrowRight}");
    await waitFor(() => expect(chapterMock).not.toHaveBeenCalled());
  });

  it("lays a heading paragraph out as a heading", async () => {
    // The parser marks the block a paragraph came from; the reader must not
    // flatten a chapter title back into body text.
    chapterMock.mockResolvedValue({
      ...chapter,
      paragraphs: [
        { kind: "h2", colored: { text: "第二章", sentences: [] }, noteRefs: [] },
        { kind: "p", colored: { text: "正文。", sentences: [] }, noteRefs: [] },
      ],
      footnotes: [],
    });
    const { container } = renderReader();
    const article = await prose();
    expect(screen.getByRole("heading", { level: 2, name: "第二章" })).toBeInTheDocument();
    // The header also holds a `<p>` (the book title), so the body paragraph
    // is addressed inside the article only.
    expect(within(article).getByText("正文。")).toBeInTheDocument();
    expect(container.querySelector("article > h2")).not.toBeNull();
  });

  it("opens the footnote a marker points at", async () => {
    // The EPUB 3 shape: a noteref marker in the text, its body shipped as the
    // chapter's footnotes. Clicking the marker shows the body in a modal.
    chapterMock.mockResolvedValue({
      ...chapter,
      paragraphs: [
        {
          kind: "p",
          colored: {
            text: "他走了。①",
            sentences: [{ text: "他走了。", start: 0, segments: [] }],
          },
          noteRefs: [{ start: 4, len: 1, target: "fn1" }],
        },
      ],
      footnotes: [{ id: "fn1", text: "他死于一月。" }],
    });
    const user = userEvent.setup();
    renderReader();
    await prose();

    await user.click(screen.getByRole("button", { name: "①" }));
    expect(await screen.findByText("他死于一月。")).toBeVisible();
    // The backdrop's aria-label is also 关闭, so the action button is
    // addressed by its text, which only the one inside the box has.
    await user.click(screen.getByText("关闭"));
    await waitFor(() =>
      expect(screen.queryByText("他死于一月。")).not.toBeInTheDocument(),
    );
  });

  it("keeps an unresolvable marker as plain text", async () => {
    // A marker whose footnote never arrived (a missing aside, or one that
    // names another document) must not render a button that shows nothing.
    chapterMock.mockResolvedValue({
      ...chapter,
      paragraphs: [
        {
          kind: "p",
          colored: { text: "他走了。①", sentences: [] },
          noteRefs: [{ start: 4, len: 1, target: "gone" }],
        },
      ],
      footnotes: [],
    });
    renderReader();
    await prose();
    expect(screen.queryByRole("button", { name: "①" })).not.toBeInTheDocument();
    expect(await prose()).toHaveTextContent("他走了。①");
  });
});
