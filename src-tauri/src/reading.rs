//! Reading a book from the shelf: EPUB -> chapters -> colored paragraphs.
//!
//! The shelf stores an EPUB file per book; the reader asks for one chapter at a
//! time. Parsing is the expensive part (a 23k-paragraph novel takes a few
//! hundred milliseconds), so the last parsed book is kept in memory: paging
//! through a chapter list must not re-unzip the whole book for every page, and
//! a reader is only ever in one book.
//!
//! Only one book is kept. Opening a second book drops the first, which costs
//! one re-parse when the user hops back — better than holding every book the
//! session has ever opened.

use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};

use crate::analyzer::{AnalyzerState, ColoredParagraph};
use crate::library::Library;
use crate::reader::{Chapter, NoteBody, Paragraph};

type Result<T> = std::result::Result<T, String>;

/// Shown when a book has no usable title anywhere (OPF and file name both
/// empty). One shared constant so the fallback word cannot drift between the
/// reader, the coloring job and the index.
pub(crate) const DEFAULT_TITLE: &str = "无题";

#[derive(Default)]
pub struct ReadingState {
    book: Mutex<Option<(String, Arc<Vec<Chapter>>)>>,
    /// Held while a book is being parsed. Without it, two commands that arrive
    /// together (a chapter click while the TOC is still loading) both miss the
    /// cache and unzip the same EPUB twice, and the second result can overwrite
    /// the first after the reader has already been served from it.
    parsing: Mutex<()>,
}

/// One row of the chapter list. Paragraphs are counted but not sent: the list
/// is a table of contents, and shipping the text would send a whole book over
/// the IPC bridge to render a sidebar.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChapterSummary {
    pub index: usize,
    pub title: Option<String>,
    pub paragraphs: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BookView {
    pub id: String,
    pub title: String,
    pub chapters: Vec<ChapterSummary>,
}

/// One paragraph of a chapter as the reader lays it out: what kind of block it
/// came from, its colored sentences, and the footnote markers in its text.
///
/// The analyzer owns [`ColoredParagraph`]; the block kind and the markers are
/// the reader's, so they ride along here rather than being pushed into the
/// analyzer's shape — one decision in one place.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ParagraphView {
    /// The HTML tag this paragraph renders as (`"h1"`…`"h6"`, or `"p"`).
    pub kind: &'static str,
    pub colored: ColoredParagraph,
    /// Footnote markers whose characters sit in the paragraph text.
    pub note_refs: Vec<crate::reader::NoteRef>,
}

/// A chapter as the reader shows it: laid-out paragraphs and the footnotes
/// their markers point at.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChapterView {
    pub index: usize,
    pub title: Option<String>,
    pub paragraphs: Vec<ParagraphView>,
    /// Footnotes in document order, keyed by [`crate::reader::NoteRef::target`].
    pub footnotes: Vec<NoteBody>,
}

/// Parsed chapters of `id`, reusing the cached book when it is the same one.
fn chapters<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReadingState,
    id: &str,
) -> Result<Arc<Vec<Chapter>>> {
    if let Ok(cache) = state.book.lock()
        && let Some((cached_id, chapters)) = cache.as_ref()
        && cached_id == id
    {
        return Ok(chapters.clone());
    }
    // Only one parse at a time, and the cache is re-checked under the gate: the
    // wait above may have been spent waiting for the very book we want. A
    // poisoned gate is recovered rather than propagated: one unparseable book
    // panicking must not make the reader unable to open any book again.
    let _parsing = state
        .parsing
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Ok(cache) = state.book.lock()
        && let Some((cached_id, chapters)) = cache.as_ref()
        && cached_id == id
    {
        return Ok(chapters.clone());
    }
    let library = app.state::<Library>();
    let path = library
        .book_file(id)
        .ok_or_else(|| format!("book-missing: 书库里没有这本书（{id}）"))?;
    let file = std::fs::File::open(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let parsed = Arc::new(crate::reader::chapters(file).map_err(|e| e.to_string())?);
    if let Ok(mut cache) = state.book.lock() {
        *cache = Some((id.to_owned(), parsed.clone()));
    }
    Ok(parsed)
}

fn title_of<R: Runtime>(app: &AppHandle<R>, id: &str) -> String {
    app.state::<Library>()
        .list()
        .ok()
        .and_then(|books| books.into_iter().find(|book| book.id == id))
        .map(|book| book.title)
        .unwrap_or_else(|| DEFAULT_TITLE.into())
}

/// Every chapter of `id`, parsed (from memory when it is the cached book).
///
/// Public because coloring a whole book walks the same chapters the reader
/// does: the two must agree on what a chapter is, or the progress bar would
/// count paragraphs the reader never shows.
pub async fn parse_book<R: Runtime>(app: &AppHandle<R>, id: &str) -> Result<Arc<Vec<Chapter>>> {
    let app = app.clone();
    let id = id.to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<ReadingState>();
        chapters(&app, &state, &id)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Opens a book: its title and chapter list, nothing else.
///
/// This is the first command the reader sends, and it is also where the cost of
/// parsing is paid once.
#[tauri::command]
pub async fn book_open<R: Runtime>(app: AppHandle<R>, id: String) -> Result<BookView> {
    // A book being colored in the background is refused here rather than by the
    // reader: the job already holds it, and two passes over the same chapters
    // would pay for every sentence twice. The shelf asks first and explains.
    if app.state::<AnalyzerState>().coloring(&id) {
        return Err("book-coloring: 这本书正在着色全书中，请等它完成".into());
    }
    let chapters = parse_book(&app, &id).await?;
    Ok(BookView {
        title: title_of(&app, &id),
        chapters: chapters
            .iter()
            .map(|chapter| ChapterSummary {
                index: chapter.index,
                title: chapter.title.clone(),
                paragraphs: chapter.paragraphs.len(),
            })
            .collect(),
        id,
    })
}

/// Loads and colors one chapter.
///
/// The analysis is what needs the model, so this is also where
/// `models-missing` surfaces; the frontend turns it into "download a model in
/// settings" rather than showing a raw error.
#[tauri::command]
pub async fn book_chapter<R: Runtime>(
    app: AppHandle<R>,
    id: String,
    index: usize,
) -> Result<ChapterView> {
    let chapter = {
        // Parsing is blocking file work; the lookup is cheap but has to happen
        // where the (possibly cached) chapter list lives.
        let parsed = parse_book(&app, &id).await?;
        parsed
            .iter()
            .find(|chapter| chapter.index == index)
            .cloned()
            .ok_or_else(|| format!("chapter-missing: 这本书没有第 {index} 章"))?
    };

    let paragraphs: Vec<String> = chapter
        .paragraphs
        .iter()
        .map(|paragraph: &Paragraph| paragraph.text.clone())
        .collect();
    // Cached analyses are reused; only what this chapter has never had is
    // analyzed. Coloring it also marks it done for the whole-book job.
    let colored = app
        .state::<AnalyzerState>()
        .colored(&app, id.clone(), index, paragraphs)
        .await?;

    // The analysis is positional over the paragraph list, so zipping kinds and
    // markers back onto it cannot mix two paragraphs up.
    let paragraphs = chapter
        .paragraphs
        .iter()
        .zip(colored)
        .map(|(paragraph, colored)| ParagraphView {
            kind: paragraph.kind.tag(),
            colored,
            note_refs: paragraph.note_refs.clone(),
        })
        .collect();

    Ok(ChapterView {
        index: chapter.index,
        title: chapter.title,
        paragraphs,
        footnotes: chapter.footnotes.clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_chapter_list_row_counts_paragraphs_without_text() {
        let chapter = Chapter {
            index: 3,
            title: Some("第三章".into()),
            paragraphs: vec![Paragraph::text_only("一。"), Paragraph::text_only("二。")],
            footnotes: Vec::new(),
        };
        let summary = ChapterSummary {
            index: chapter.index,
            title: chapter.title.clone(),
            paragraphs: chapter.paragraphs.len(),
        };
        assert_eq!(summary.paragraphs, 2);
        // The summary is a TOC row: it must stay small however long the book is.
        let json = serde_json::to_string(&summary).expect("serialize");
        assert!(!json.contains('一'), "章节列表不该带正文：{json}");
    }

    #[test]
    fn a_chapter_view_carries_layout_and_footnotes() {
        let view = ChapterView {
            index: 0,
            title: None,
            paragraphs: vec![ParagraphView {
                kind: "h2",
                colored: ColoredParagraph {
                    text: "第二章".into(),
                    sentences: Vec::new(),
                },
                note_refs: vec![crate::reader::NoteRef {
                    start: 3,
                    len: 1,
                    target: "fn1".into(),
                }],
            }],
            footnotes: vec![NoteBody {
                id: "fn1".into(),
                text: "注。".into(),
            }],
        };
        let json = serde_json::to_string(&view).expect("serialize");
        // The frontend lays headings out by `kind` and the footnote buttons by
        // `noteRefs`; both must survive the bridge with camelCase intact.
        assert!(json.contains(r#""kind":"h2""#), "{json}");
        assert!(json.contains(r#""noteRefs""#), "{json}");
        assert!(json.contains(r#""footnotes""#), "{json}");
    }
}
