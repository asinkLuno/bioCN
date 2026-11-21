//! The 主谓宾 analyzer, wired to the models the user installed.
//!
//! Loading the three ONNX sessions takes seconds and several hundred megabytes,
//! so it happens once, on the first chapter that needs it, and is dropped when
//! the weights change. Everything the reader asks for afterwards is served from
//! [`AnalyzerState::colored`].
//!
//! Two locks, for two different races:
//!
//! * `installing` is held by `models_install` / `models_remove` for the whole
//!   download and by the load path while it maps the weight files, so a session
//!   can never be built over a half-written variant directory.
//! * `slot` holds the analyzer itself, and the load happens while it is held —
//!   a second request arriving during the load waits for the first instead of
//!   mapping the same three files a second time.
//!
//! Analyses live in [`crate::cache`], not in a map here: the app exists to
//! color books, so a chapter that was colored is colored forever (for those
//! weights). A chapter's worth of sentences is one lookup, then one analysis per
//! sentence that missed, then one transaction — the granularity is what keeps a
//! thousand-sentence chapter from paying a thousand round trips.

use std::collections::HashMap;
use std::future::Future;
use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, Runtime};

use crate::analysis::engine::Sessions;
use crate::analysis::{BioCNResources, Segment, analyze_sentence};
use crate::cache::AnalysisCache;

type Result<T> = std::result::Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}

/// The sentences of a chapter's paragraphs — the progress unit, because that
/// is what the model processes and what the cache is keyed by. Splitting a
/// chapter twice (here for the bar, inside `colored` for the analysis) is
/// noise next to the hours of inference it weights.
fn sentences_in(paragraphs: &[crate::reader::Paragraph]) -> usize {
    paragraphs
        .iter()
        .map(|paragraph| crate::text::split(&paragraph.text).len())
        .sum()
}

/// What the main menu's progress bar reads.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ColoringProgress {
    pub book: String,
    /// Sentences analyzed so far, and how many the chapters in flight have.
    pub done: usize,
    pub total: usize,
    /// Chapters finished, and how many were not colored when the job started.
    pub chapters: usize,
    pub chapters_total: usize,
    /// Set once the job is over — whether it finished, failed or was cancelled.
    pub finished: bool,
    /// The user stopped it. Not an error: what was colored so far is kept, and
    /// saying "完成" after a cancel would be a lie.
    pub cancelled: bool,
    /// A failure that stopped the job; the paragraphs already colored stay.
    pub error: Option<String>,
}

#[derive(Default)]
pub struct AnalyzerState {
    slot: tauri::async_runtime::Mutex<Option<Arc<Analyzer>>>,
    installing: tauri::async_runtime::Mutex<()>,
    /// Serializes install/remove. Separate from `installing`, which is held for
    /// the *whole* download: taking that one here would block every chapter
    /// behind a download, while this one is only there so two operations cannot
    /// write the same `staging/` file or delete a `.part` another is appending
    /// to.
    operation: tauri::async_runtime::Mutex<()>,
    /// The one whole-book job. Coloring a book is hours of work that must not
    /// compete with itself (two jobs on one book would pay for the same
    /// sentences twice), and the menu's progress bar shows this, so there is
    /// only ever one.
    job: std::sync::Mutex<Option<Job>>,
}

/// A whole-book coloring, as far as the UI is concerned.
struct Job {
    book: String,
    progress: ColoringProgress,
    /// Flipped by `colorize_book_cancel`; the worker checks it between
    /// chapters, which is also between IPC commands, so cancelling is prompt.
    cancelled: Arc<std::sync::atomic::AtomicBool>,
}

impl AnalyzerState {
    /// Runs a model install/remove to completion, one at a time.
    pub async fn while_installing_model<T>(&self, work: impl Future<Output = T>) -> T {
        let _operation = self.operation.lock().await;
        work.await
    }
    /// Drops the sessions so the next analysis rebuilds from disk.
    ///
    /// Called after installing or removing weights: the loaded sessions map the
    /// old files, and on Windows the old directory cannot even be deleted while
    /// they hold it.
    ///
    /// A chapter already being analysed holds its own `Arc<Analyzer>` and keeps
    /// running: `models.rs` unloads *before* touching the directory, and a
    /// chapter that started earlier is reading a mapping the OS keeps valid,
    /// but on Windows its files resist deletion until the last reference drops.
    /// Installing while a chapter is loading is therefore allowed to fail and
    /// be retried rather than blocking every read behind a download — see
    /// `models::commit`, whose `remove_dir_all` is best-effort for that reason.
    ///
    /// The disk cache is not touched: it is keyed by the weights fingerprint, so
    /// what this variant colored is still there when it is activated again.
    pub async fn unload(&self) {
        *self.slot.lock().await = None;
    }

    /// Whether sessions are currently mapped. Only for tests and diagnostics:
    /// callers that want to *use* the analyzer go through `analyzer`.
    #[cfg(test)]
    async fn is_loaded(&self) -> bool {
        self.slot.lock().await.is_some()
    }

    /// Runs `work` while holding the install gate. `models.rs` wraps its file
    /// work in this; see the module docs for what it protects against.
    pub async fn while_installing<T>(&self, work: impl Future<Output = T>) -> T {
        let _gate = self.installing.lock().await;
        work.await
    }

    async fn analyzer<R: Runtime>(&self, app: &AppHandle<R>) -> Result<Arc<Analyzer>> {
        let _gate = self.installing.lock().await;
        let mut slot = self.slot.lock().await;
        if let Some(analyzer) = slot.as_ref() {
            return Ok(analyzer.clone());
        }
        // Session loading is blocking file I/O and ORT graph optimization: it
        // must not run on the async runtime's worker threads.
        let dir = crate::models::installed_dir(app)?;
        let cache = app.state::<AnalysisCache>().inner().clone();
        let weights = crate::cache::weights(app)?;
        let built = tauri::async_runtime::spawn_blocking(move || {
            let resources = BioCNResources::load_dir(&dir).map_err(err)?;
            let sessions = Sessions::load(&dir).map_err(err)?;
            Ok::<_, String>(Analyzer {
                resources,
                sessions,
                cache,
                weights,
            })
        })
        .await
        .map_err(err)??;
        let analyzer = Arc::new(built);
        *slot = Some(analyzer.clone());
        Ok(analyzer)
    }

    /// One chapter, colored: from the cache where it is known, by the model
    /// where it is not. Returns what the reader renders, and marks the chapter
    /// colored on disk so a whole-book job can skip it.
    pub async fn colored<A: Runtime>(
        &self,
        app: &AppHandle<A>,
        book: String,
        chapter: usize,
        paragraphs: Vec<String>,
    ) -> Result<Vec<ColoredParagraph>> {
        // A cover page has nothing to analyze at all, and loading three ONNX
        // sessions to learn that is absurd. It still gets its marker: otherwise
        // the whole-book job would see it as work forever.
        let prepared = PreparedChapter::new(paragraphs);

        let cache = app.state::<AnalysisCache>().inner().clone();
        let weights = crate::cache::weights(app)?;
        // Ask the cache before touching the model. A colored chapter — the
        // common case after a whole-book job, or on the second read of anything
        // — costs a query and never loads a session.
        let lookup = {
            let cache = cache.clone();
            let weights = weights.clone();
            let sentences = prepared.sentences.clone();
            let book = book.clone();
            tauri::async_runtime::spawn_blocking(move || {
                let cached = cache.get(&weights, &sentences)?;
                // Fully known means fully colored: record it, so the chapter
                // counts as done for the whole-book job without a model load.
                // Compared per sentence, not by length: a repeated line is one
                // row, and a chapter that quotes itself is still fully cached.
                if sentences
                    .iter()
                    .all(|sentence| cached.contains_key(sentence))
                {
                    cache.mark_chapter(&weights, &book, chapter)?;
                }
                Ok::<_, String>(cached)
            })
            .await
            .map_err(err)?
        }?;
        // `lookup` is what the model would have to add to; when every sentence
        // is in it, the chapter is finished and the sessions stay unloaded.
        if prepared
            .sentences
            .iter()
            .all(|sentence| lookup.contains_key(sentence))
        {
            return Ok(assemble(prepared, &lookup));
        }

        let analyzer = self.analyzer(app).await?;
        tauri::async_runtime::spawn_blocking(move || {
            analyzer.colorize_book_chapter(book, chapter, prepared, lookup)
        })
        .await
        .map_err(err)?
    }
}

/// One chapter's paragraphs, split into sentences, ready for a lookup.
///
/// Split once and kept next to the sentence list: the cache is keyed by
/// sentence text, and the renderer needs the same ranges back, so re-splitting
/// (or re-slicing the paragraph) at either end would be work done twice.
struct PreparedChapter {
    paragraphs: Vec<String>,
    split: Vec<Vec<std::ops::Range<usize>>>,
    /// Every sentence of the chapter, in reading order, across paragraphs.
    sentences: Vec<String>,
}

impl PreparedChapter {
    fn new(paragraphs: Vec<String>) -> Self {
        let split: Vec<Vec<std::ops::Range<usize>>> = paragraphs
            .iter()
            .map(|paragraph| crate::text::split(paragraph))
            .collect();
        let sentences = paragraphs
            .iter()
            .zip(split.iter())
            .flat_map(|(paragraph, ranges)| {
                ranges
                    .iter()
                    .map(move |range| paragraph[range.clone()].to_owned())
            })
            .collect();
        Self {
            paragraphs,
            split,
            sentences,
        }
    }
}

/// The model sessions plus where their answers are kept.
struct Analyzer {
    resources: BioCNResources,
    sessions: Sessions,
    cache: AnalysisCache,
    /// Rows under any other key are another model's answers, so every read and
    /// write names this one.
    weights: String,
}

impl Analyzer {
    /// Colors a chapter whose cache misses are still to be analyzed.
    ///
    /// `cached` is what the caller already looked up, so this neither re-queries
    /// nor re-splits: it analyzes the misses, writes them, and assembles.
    fn colorize_book_chapter(
        &self,
        book: String,
        chapter: usize,
        prepared: PreparedChapter,
        cached: HashMap<String, Option<Vec<Segment>>>,
    ) -> Result<Vec<ColoredParagraph>> {
        // Analyze what missed, without holding any lock over the model work.
        let mut fresh: Vec<(String, Option<Vec<Segment>>)> = Vec::new();
        let mut answers = cached;
        let mut failure: Option<String> = None;
        let mut failed = 0usize;
        let mut analyzed = 0usize;
        for sentence in &prepared.sentences {
            if answers.contains_key(sentence) {
                continue;
            }
            let answer = analyze_sentence(&self.resources, &self.sessions, sentence);
            analyzed += 1;
            match answer {
                Ok(segments) => {
                    fresh.push((sentence.clone(), segments.clone()));
                    answers.insert(sentence.clone(), segments);
                }
                Err(error) => {
                    // One odd paragraph must cost that paragraph its colours,
                    // not the chapter its text: the sentence renders plain and
                    // the page still appears. Nothing is written for it, and
                    // the chapter is therefore not marked colored, so a later
                    // attempt retries it — a model hiccup must not become a
                    // permanent "this sentence has no 主谓宾".
                    failed += 1;
                    failure.get_or_insert(err(error));
                    answers.insert(sentence.clone(), None);
                }
            }
        }

        // Everything failing means the model is broken, not that the prose is
        // unusual; the caller has to say so rather than show an uncolored book
        // and no reason. Nothing is written, so a retry starts clean.
        if failed > 0 && failed == analyzed {
            return Err(failure.unwrap_or_else(|| "分析失败".into()));
        }

        // One transaction, and the chapter only counts as colored when every
        // sentence in it is answered.
        self.cache
            .save_chapter(&self.weights, &book, chapter, failed == 0, &fresh)?;

        Ok(assemble(prepared, &answers))
    }
}

/// Puts the answers back into the paragraph/sentence shape the reader renders.
fn assemble(
    prepared: PreparedChapter,
    answers: &HashMap<String, Option<Vec<Segment>>>,
) -> Vec<ColoredParagraph> {
    prepared
        .paragraphs
        .into_iter()
        .zip(prepared.split)
        .map(|(paragraph, ranges)| {
            let sentences = ranges
                .into_iter()
                .map(|range| {
                    let text = &paragraph[range.clone()];
                    // The splitter works in bytes; the frontend needs UTF-16
                    // offsets, counted from the paragraph start (the DOM
                    // range's base).
                    ColoredSentence {
                        start: crate::analysis::utf16::utf16_len(&paragraph[..range.start]),
                        segments: answers.get(text).cloned().flatten().unwrap_or_default(),
                        text: text.to_owned(),
                    }
                })
                .collect();
            ColoredParagraph {
                text: paragraph,
                sentences,
            }
        })
        .collect()
}

/// One sentence of a paragraph, ready to be rendered.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ColoredSentence {
    pub text: String,
    /// UTF-16 offset of the sentence within its paragraph, which is what a DOM
    /// `Range` needs.
    pub start: usize,
    /// 主谓宾 spans, each with its own UTF-16 offset within the *sentence*.
    pub segments: Vec<Segment>,
}

/// A paragraph as the reader renders it: its sentences, in order.
///
/// Returned per paragraph (rather than as one flat list) because that is the
/// unit the analyzer works in and the unit the page lays out.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ColoredParagraph {
    pub text: String,
    pub sentences: Vec<ColoredSentence>,
}

/// Colors a whole book in the background, one chapter at a time.
///
/// Deliberately a spawned task rather than a blocking command: the reader stays
/// usable for every other book meanwhile, and the caller only learns that the
/// job started. Progress is emitted on [`PROGRESS_EVENT`] and kept in
/// `AnalyzerState`, so a UI that mounts mid-job (or missed an event) can ask.
///
/// Returns whether the job was accepted. A refusal (one is already running) is
/// not an error the caller can fix by retrying, but it must not look like a
/// start either: the caller's command answers the button, and a silent no-op
/// would leave the user waiting for a bar that never appears.
pub async fn spawn_colorize_book<A: Runtime>(
    app: AppHandle<A>,
    book: String,
    title: String,
    todo: Vec<usize>,
    sentences_total: usize,
) -> bool {
    let state = app.state::<AnalyzerState>();
    let cancelled = match state.begin(&book, sentences_total, todo.len()) {
        Ok(cancelled) => cancelled,
        Err(running) => {
            log_conflict(&title, &running);
            return false;
        }
    };
    emit(&app);

    tauri::async_runtime::spawn(async move {
        // The release is a value that is dropped on every exit, panics
        // included: a worker that dies without saying so would leave its book
        // locked forever, because `coloring()` reads `finished`.
        let end = JobEnd {
            app: app.clone(),
            cancelled: cancelled.clone(),
            settled: false,
        };
        let outcome = run_book(&app, &book, &todo, &cancelled).await;
        end.settle(outcome);
    });
    true
}

/// Marks the job finished — on the normal path, and when the worker panics.
struct JobEnd<A: Runtime> {
    app: AppHandle<A>,
    cancelled: Arc<std::sync::atomic::AtomicBool>,
    settled: bool,
}

impl<A: Runtime> JobEnd<A> {
    fn settle(mut self, outcome: Outcome) {
        self.write(|progress| {
            progress.done = outcome.done.min(progress.total);
            progress.chapters = outcome.chapters;
            progress.cancelled = outcome.cancelled;
            progress.error = outcome.error;
        });
        self.settled = true;
    }

    /// Updates *this* job's progress, if it is still the one on screen.
    ///
    /// A finished job can be replaced by a new start, and that one's progress
    /// must not be overwritten by the tail of its predecessor: the cancel flag
    /// is the job's identity.
    fn write(&self, update: impl FnOnce(&mut ColoringProgress)) {
        let state = self.app.state::<AnalyzerState>();
        {
            let mut job = state
                .job
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            let Some(progress) = job
                .as_mut()
                .filter(|job| Arc::ptr_eq(&job.cancelled, &self.cancelled))
                .map(|job| &mut job.progress)
            else {
                return;
            };
            progress.finished = true;
            update(progress);
        }
        emit(&self.app);
    }
}

impl<A: Runtime> Drop for JobEnd<A> {
    fn drop(&mut self) {
        if self.settled {
            return;
        }
        // The worker died mid-chapter. The paragraphs it wrote are real, so
        // this is a report, not a rollback — but the book must be openable
        // again.
        self.write(|progress| {
            progress.error = Some("着色任务意外中断".into());
        });
    }
}

/// What a finished job reports back to the state.
struct Outcome {
    done: usize,
    chapters: usize,
    /// Stopped by the user rather than run out or completed.
    cancelled: bool,
    error: Option<String>,
}

/// Colors the chapters in `todo`, in order.
///
/// `todo` is decided once, before the job starts, rather than by asking the
/// cache chapter by chapter: the bar's total has to be fixed when it appears,
/// and a book that gains chapters mid-job (a re-import) must not move the goal.
async fn run_book<A: Runtime>(
    app: &AppHandle<A>,
    book: &str,
    todo: &[usize],
    cancelled: &Arc<std::sync::atomic::AtomicBool>,
) -> Outcome {
    let mut done = 0usize;
    let mut chapters = 0usize;
    let state = app.state::<AnalyzerState>();

    let parsed = match crate::reading::parse_book(app, book).await {
        Ok(parsed) => parsed,
        Err(error) => {
            return Outcome {
                done,
                chapters,
                cancelled: false,
                error: Some(error),
            };
        }
    };

    let mut cancelled_midway = false;
    for index in todo {
        if cancelled.load(std::sync::atomic::Ordering::Relaxed) {
            cancelled_midway = true;
            break;
        }
        // A chapter the list no longer has (the job was resumed from a stale
        // view) is skipped rather than treated as a failure.
        let Some(chapter) = parsed.iter().find(|chapter| chapter.index == *index) else {
            continue;
        };
        let sentences = sentences_in(&chapter.paragraphs);
        let texts: Vec<String> = chapter
            .paragraphs
            .iter()
            .map(|paragraph| paragraph.text.clone())
            .collect();
        // The same entry point the reader uses, so a chapter colored here and a
        // chapter colored by opening it leave exactly the same rows behind —
        // and a cover page costs no model load in either path.
        let result = state.colored(app, book.to_owned(), *index, texts).await;
        if let Err(error) = result {
            // The sessions are the shared resource: a failure that is not about
            // one weird paragraph (those are cached as rejections) means the
            // model is unusable, and every later chapter would fail the same
            // way.
            return Outcome {
                done,
                chapters,
                cancelled: false,
                error: Some(error),
            };
        }
        chapters += 1;
        done += sentences;
        {
            let mut job = state
                .job
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            // Same identity check as the tail: a job cannot be replaced while
            // it runs, but relying on that here would make the guard a puzzle
            // for the next reader instead of a rule.
            if let Some(job) = job
                .as_mut()
                .filter(|job| Arc::ptr_eq(&job.cancelled, cancelled))
            {
                job.progress.done = done.min(job.progress.total);
                job.progress.chapters = chapters;
            }
        }
        emit(app);
    }

    Outcome {
        done,
        chapters,
        cancelled: cancelled_midway,
        error: None,
    }
}

fn emit<A: Runtime>(app: &AppHandle<A>) {
    let state = app.state::<AnalyzerState>();
    let progress = state
        .job
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .as_ref()
        .map(|job| job.progress.clone());
    if let Some(progress) = progress {
        let _ = app.emit(crate::PROGRESS_EVENT, progress);
    }
}

/// Colors every chapter of `id` that has none yet, in the background.
///
/// Returns as soon as the job is accepted: the app stays usable for every other
/// book, and the caller watches [`crate::PROGRESS_EVENT`] (or asks
/// [`coloring_progress`]) instead of awaiting hours of analysis behind one
/// command.
#[tauri::command]
pub async fn colorize_book<R: Runtime>(app: AppHandle<R>, id: String) -> Result<()> {
    if app.state::<AnalyzerState>().coloring(&id) {
        return Err("book-coloring: 这本书已经在着色了".into());
    }
    // The chapter list is walked here so the progress bar has a real total, and
    // so an unparseable book is an error at the button instead of a job that
    // silently colors nothing.
    let chapters = crate::reading::parse_book(&app, &id).await?;
    let title = app
        .state::<crate::library::Library>()
        .list()
        .ok()
        .and_then(|books| books.into_iter().find(|book| book.id == id))
        .map(|book| book.title)
        .unwrap_or_else(|| crate::reading::DEFAULT_TITLE.into());
    // Which chapters are left is decided here, not by the job: the totals shown
    // on the bar have to be right when it appears, and a chapter the reader
    // colored yesterday must not be counted as work to do.
    let weights = crate::cache::weights(&app)?;
    let colored = app
        .state::<AnalysisCache>()
        .colored_chapters(&weights, &id)?;
    let todo: Vec<usize> = chapters
        .iter()
        .map(|chapter| chapter.index)
        .filter(|index| !colored.contains(index))
        .collect();
    // Progress is over the work this job will actually do: a book half-colored
    // by reading should not open at 50 %.
    let sentences: usize = chapters
        .iter()
        .filter(|chapter| todo.contains(&chapter.index))
        .map(|chapter| sentences_in(&chapter.paragraphs))
        .sum();
    // A refusal has to reach the button: the shelf would otherwise show no bar
    // and no reason, and the user would press it again.
    if !spawn_colorize_book(app, id, title, todo, sentences).await {
        return Err("book-coloring: 另一本书正在着色全书中，等它结束再来".into());
    }
    Ok(())
}

/// The running (or last finished) job, for a menu that opened after it started
/// and missed the events.
#[tauri::command]
pub async fn coloring_progress<R: Runtime>(app: AppHandle<R>) -> Result<Option<ColoringProgress>> {
    Ok(app.state::<AnalyzerState>().progress())
}

/// Stops after the chapter in flight. What is already colored stays colored.
#[tauri::command]
pub async fn colorize_book_cancel<R: Runtime>(app: AppHandle<R>) -> Result<()> {
    app.state::<AnalyzerState>().cancel();
    Ok(())
}

/// Forgets a book's coloring: the cache rows its sentences keyed and its
/// chapter markers, under every weights. The next read or whole-book pass
/// analyzes from scratch, which is what the book menu's 清除缓存 promises.
///
/// Refused while *this* book is being colored: the job is writing exactly the
/// rows a clear would delete, and the bar would end over a book that shows no
/// coloring. Another book's job is left alone — its writes and this clear
/// touch disjoint markers, and a sentence row they share is recreated by the
/// job's next save at worst.
#[tauri::command]
pub async fn clear_book_cache<R: Runtime>(app: AppHandle<R>, id: String) -> Result<()> {
    if app.state::<AnalyzerState>().coloring(&id) {
        return Err("book-cache: 这本书正在着色全书中，先取消或等它结束再清除缓存".into());
    }
    // The book is the one that knows its sentences: collect them the same way
    // the analysis does — the split here is the key the rows live under.
    let chapters = crate::reading::parse_book(&app, &id).await?;
    let sentences: Vec<String> = chapters
        .iter()
        .flat_map(|chapter| {
            chapter.paragraphs.iter().flat_map(|paragraph| {
                crate::text::split(&paragraph.text)
                    .into_iter()
                    .map(move |range| paragraph.text[range].to_owned())
            })
        })
        .collect();
    app.state::<AnalysisCache>().clear_book(&id, &sentences)
}

/// Whether `id` has coloring to clear: any chapter marker, under any weights —
/// the same scope [`clear_book_cache`] works on.
///
/// The book menu greys 清除着色缓存 off when there is nothing to clear, and
/// answering that must not parse the whole book: the markers settle it with
/// one lookup, taken at menu-open so it is current when the item is clicked.
#[tauri::command]
pub async fn book_has_coloring<R: Runtime>(app: AppHandle<R>, id: String) -> Result<bool> {
    app.state::<AnalysisCache>().has_coloring(&id)
}

/// Whether `id` has coloring under the *active* weights — the only slice
/// [`crate::export::export_book`] reads.
///
/// The book menu greys 导出着色后的图书 off without it: exporting a book with
/// no coloring under these weights would write the plain book back out, and a
/// variant left behind does not color anything the reader sees.
#[tauri::command]
pub async fn book_has_active_coloring<R: Runtime>(app: AppHandle<R>, id: String) -> Result<bool> {
    let weights = crate::cache::weights(&app)?;
    app.state::<AnalysisCache>().has_coloring_under(&weights, &id)
}

/// A job that lost the race is the UI's second guess, not something the user
/// can act on: the running one keeps going and its progress is the truth. The
/// command still fails, because a *silent* no-op would leave the button looking
/// pressed and nothing happening.
fn log_conflict(requested: &str, running: &str) {
    eprintln!("colorize: {requested:?} ignored, {running:?} is already coloring");
}

impl AnalyzerState {
    /// Claims the single job slot, or reports which book holds it.
    ///
    /// Check and replace under *one* lock: between a separate check and set,
    /// two commands arriving together (two windows, a menu and a retry) would
    /// both see no job and both start one — exactly the case the single-job
    /// rule exists to prevent.
    fn begin(
        &self,
        book: &str,
        sentences_total: usize,
        chapters_total: usize,
    ) -> std::result::Result<Arc<std::sync::atomic::AtomicBool>, String> {
        let mut job = self
            .job
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some(running) = job.as_ref()
            && !running.progress.finished
        {
            return Err(running.progress.book.clone());
        }
        let cancelled = Arc::new(std::sync::atomic::AtomicBool::new(false));
        *job = Some(Job {
            book: book.to_owned(),
            progress: ColoringProgress {
                book: book.to_owned(),
                done: 0,
                total: sentences_total,
                chapters: 0,
                chapters_total,
                finished: false,
                cancelled: false,
                error: None,
            },
            cancelled: cancelled.clone(),
        });
        Ok(cancelled)
    }

    /// The job as the menu's progress bar should show it, if there is one.
    pub fn progress(&self) -> Option<ColoringProgress> {
        self.job
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_ref()
            .map(|job| job.progress.clone())
    }

    /// Asks the running job to stop after the chapter in flight.
    pub fn cancel(&self) -> bool {
        let job = self
            .job
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        match job.as_ref() {
            Some(job) if !job.progress.finished => {
                job.cancelled
                    .store(true, std::sync::atomic::Ordering::Relaxed);
                true
            }
            _ => false,
        }
    }

    /// Whether `book` is the one being colored. Opening it is refused while the
    /// job runs: the job and the reader would analyze the same chapter twice.
    pub fn coloring(&self, book: &str) -> bool {
        self.job
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_ref()
            .is_some_and(|job| job.book == book && !job.progress.finished)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::Role;

    /// The progress unit is the sentence, because that is what the model
    /// processes and what the cache is keyed by: a one-line paragraph heading
    /// and a two-thousand-character block must not weigh the same.
    #[test]
    fn progress_counts_sentences_not_paragraphs() {
        fn paragraph(text: &str) -> crate::reader::Paragraph {
            crate::reader::Paragraph::text_only(text.to_owned())
        }
        let paragraphs = vec![
            // A heading: no sentence-final punctuation, still its own run.
            paragraph("第一章"),
            paragraph("他走了。「好。」她没回头。"),
        ];
        // The heading is its own sentence run, and 「好。」 is a sentence of
        // its own inside the quote: the chapter weighs 4, not its paragraph
        // count of 2.
        assert_eq!(sentences_in(&paragraphs), 4);
        assert_eq!(sentences_in(&[]), 0);
    }

    /// The paragraph/offset contract: sentence offsets must be usable as DOM
    /// ranges, which means UTF-16 units counted from the paragraph start, not
    /// bytes and not per-sentence.
    #[test]
    fn sentence_offsets_are_utf16_from_the_paragraph_start() {
        let paragraph = "他走了。「好。」";
        let ranges = crate::text::split(paragraph);
        let starts: Vec<usize> = ranges
            .iter()
            .map(|range| crate::analysis::utf16::utf16_len(&paragraph[..range.start]))
            .collect();
        // "他走了。" is 4 UTF-16 units, so the second sentence starts at 4.
        assert_eq!(starts, [0, 4]);
    }

    /// The reader renders the gap between two sentences from the paragraph
    /// text, because each sentence is trimmed: `He slept. She woke.` must keep
    /// the space at offset 9, or the page runs the words together.
    #[test]
    fn the_gap_between_trimmed_sentences_is_the_paragraphs_own_space() {
        let paragraph = "He slept. She woke.";
        let sentences: Vec<(usize, &str)> = crate::text::split(paragraph)
            .into_iter()
            .map(|range| {
                (
                    crate::analysis::utf16::utf16_len(&paragraph[..range.start]),
                    &paragraph[range],
                )
            })
            .collect();
        assert_eq!(sentences, [(0, "He slept."), (10, "She woke.")]);
        let gap = &paragraph[sentences[0].0 + sentences[0].1.len()..sentences[1].0];
        assert_eq!(gap, " ");
    }

    #[test]
    fn assembling_puts_answers_back_under_their_sentences() {
        let paragraphs = vec!["他走了。天黑了。".to_string()];
        let spans = vec![Segment {
            text: "他".into(),
            start: 0,
            role: Role::Subject,
        }];
        let answers = HashMap::from([
            ("他走了。".to_string(), Some(spans.clone())),
            ("天黑了。".to_string(), None),
        ]);

        let colored = assemble(PreparedChapter::new(paragraphs), &answers);
        assert_eq!(colored.len(), 1);
        assert_eq!(colored[0].sentences.len(), 2);
        assert_eq!(colored[0].sentences[0].segments, spans);
        assert_eq!(colored[0].sentences[1].start, 4);
        assert!(
            colored[0].sentences[1].segments.is_empty(),
            "a rejected sentence is shown plain, not dropped"
        );
    }

    /// Unloading empties the slot, so the next analysis rebuilds from whatever
    /// weights are on disk then — and therefore cannot answer from the spans of
    /// the model that was unloaded.
    #[test]
    fn unloading_empties_the_slot() {
        let state = AnalyzerState::default();
        tauri::async_runtime::block_on(state.unload());
        assert!(!tauri::async_runtime::block_on(state.is_loaded()));
    }

    /// A finished job does not lock the book out; a running one does.
    #[test]
    fn only_a_running_job_blocks_its_book() {
        let state = AnalyzerState::default();
        assert!(!state.coloring("dune"));

        let cancelled = state
            .begin("dune", 10, 2)
            .expect("no job is running, so this one starts");
        assert!(state.coloring("dune"));
        assert!(!state.coloring("emma"));
        // One job at a time, and the refusal names the book in the way.
        assert_eq!(state.begin("emma", 4, 1).unwrap_err(), "dune");
        assert!(
            !cancelled.load(std::sync::atomic::Ordering::Relaxed),
            "a fresh job is not cancelled"
        );

        assert!(state.cancel(), "a running job can be cancelled");
        assert!(
            cancelled.load(std::sync::atomic::Ordering::Relaxed),
            "cancelling flips the flag the worker polls"
        );
        assert!(
            state
                .progress()
                .is_some_and(|progress| progress.book == "dune")
        );

        if let Some(job) = state
            .job
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_mut()
        {
            job.progress.finished = true;
        }
        assert!(!state.coloring("dune"));
        assert!(!state.cancel(), "a job that is over cannot be cancelled");
        // A finished job does not hold the slot forever: the next book can be
        // started, which is what makes coloring two books in a row possible.
        state.begin("emma", 4, 1).expect("the slot is free again");
        assert!(state.coloring("emma"));
    }
}
