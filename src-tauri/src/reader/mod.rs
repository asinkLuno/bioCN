//! Chapter text out of an EPUB, paragraph by paragraph.
//!
//! The reader does not need the whole book at once, but it does need it in
//! spine order and it needs paragraph boundaries, because bioCN analyzes one
//! paragraph at a time: a sentence cannot be coloured without knowing where the
//! sentence is, and a sentence cannot be found without knowing where the
//! paragraph is.
//!
//! Upstream (`src/epub_parser.py`) takes the same view of an EPUB —
//! `p.get_text()` for every non-empty `<p>` — so the paragraph list here is
//! meant to line up with what the web version would have produced for the same
//! file. That is also why inline markup is flattened rather than preserved: the
//! paragraph *text* is what gets analyzed, and the offsets in the result index
//! into that text.
//!
//! What the flattening does keep is the structure a reader needs to lay the
//! chapter out the way the book meant it (the way EPUB.js gets for free by
//! handing the original XHTML to the browser): each paragraph remembers the
//! heading level of the block it came from ([`ParagraphKind`]), and the EPUB 3
//! footnote convention — `a[epub:type=noteref]` markers, whose bodies live in
//! `aside[epub:type=footnote]` — is pulled out of the flow into
//! [`Document::footnotes`], with each marker's characters pointed at by a
//! [`NoteRef`].
//!
//! Two details are worth knowing:
//!
//! * Offsets from the analyzer are UTF-16 code units, matching the DOM ranges
//!   the reader builds. Everything here is `String` (UTF-8), so callers convert
//!   with [`crate::analysis::utf16`] rather than assuming byte offsets.
//! * A spine document is XHTML, but real books are not always well-formed XML:
//!   they carry HTML entities (`&nbsp;`) or an `<html>` without a namespace.
//!   The parser is therefore lenient about entities and matches tags by local
//!   name, not by namespace URI.

use std::collections::HashMap;

use quick_xml::events::{BytesStart, Event};
use quick_xml::reader::Reader;
use quick_xml::XmlVersion;
use serde::Serialize;

/// Blocks whose text is not prose and must not reach the analyzer.
///
/// `<rt>` is furigana in ruby annotations and `<script>`/`<style>` are code;
/// putting any of them in a paragraph would produce nonsense sentences and,
/// worse, offsets that do not line up with what the reader displays.
const SKIPPED: &[&str] = &["script", "style", "rt", "rp", "svg", "math"];

/// Elements that are one paragraph on their own.
///
/// Deliberately short: `<li>`/`<td>`/`<blockquote>` usually wrap a `<p>`, so
/// they are containers below and emit nothing of their own when they do.
const PARAGRAPHS: &[&str] = &[
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "dt",
    "dd",
    "td",
    "th",
    "pre",
    "figcaption",
    "caption",
];

/// Elements that group content.
///
/// They exist as frames so their *own* text is not lost — a `<div>` holding
/// loose text between `<br>`s is a real paragraph — while a `<div>` that merely
/// wraps `<p>`s contributes nothing. Books rely on both shapes: one of the
/// sample EPUBs puts an entire chapter inside `<div class="body">`.
const CONTAINERS: &[&str] = &[
    "div",
    "section",
    "article",
    "blockquote",
    "ul",
    "ol",
    "dl",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "figure",
    "header",
    "footer",
    "aside",
    "nav",
    "main",
    "body",
    "html",
];

fn is_frame(name: &str) -> bool {
    PARAGRAPHS.contains(&name) || CONTAINERS.contains(&name)
}

/// What kind of block a paragraph came from, which is how the reader lays it
/// out. Only headings are distinguished: they are the one case where the block
/// tag changes how the *prose* reads, and the only structure daisyUI has no
/// class for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParagraphKind {
    Body,
    H1,
    H2,
    H3,
    H4,
    H5,
    H6,
}

impl ParagraphKind {
    /// The HTML tag the reader renders this paragraph as.
    pub fn tag(self) -> &'static str {
        match self {
            Self::Body => "p",
            Self::H1 => "h1",
            Self::H2 => "h2",
            Self::H3 => "h3",
            Self::H4 => "h4",
            Self::H5 => "h5",
            Self::H6 => "h6",
        }
    }
}

/// The block kind of a frame, from the tag the frame was opened by.
fn kind_of(name: &str) -> ParagraphKind {
    match name {
        "h1" => ParagraphKind::H1,
        "h2" => ParagraphKind::H2,
        "h3" => ParagraphKind::H3,
        "h4" => ParagraphKind::H4,
        "h5" => ParagraphKind::H5,
        "h6" => ParagraphKind::H6,
        _ => ParagraphKind::Body,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paragraph {
    /// Text with inline markup flattened and whitespace collapsed.
    pub text: String,
    /// The block this paragraph came from, which decides its element.
    pub kind: ParagraphKind,
    /// Footnote markers whose characters sit in this paragraph's text.
    pub note_refs: Vec<NoteRef>,
    /// Where each character of `text` came from in the chapter document.
    ///
    /// Always the same length as `text.chars()`. Empty means "nobody recorded
    /// it" — [`paragraphs`] does not fill it, and a paragraph that only a test
    /// constructs has no markup to point at. The exporter needs it because its
    /// whole job is putting spans back into the *right* text node: `text` is
    /// collapsed and entity-decoded, so searching the source for it would find
    /// the first look-alike instead.
    pub source: Vec<Placed>,
}

impl Paragraph {
    /// A paragraph known only by its text (tests, and callers that do not
    /// rewrite markup).
    pub fn text_only(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            kind: ParagraphKind::Body,
            note_refs: Vec::new(),
            source: Vec::new(),
        }
    }
}

/// One character of a paragraph, and the bytes of the document it came from.
///
/// One entry per character rather than one per markup event: a colored span
/// starts and ends *between* characters, and runs would have to be split again
/// at every boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Placed {
    pub ch: char,
    /// Byte range of the markup this character came from. [`SYNTHETIC`] in both
    /// ends for a character that is not in the markup at all (the space a
    /// `<br/>` becomes).
    pub from: u32,
    pub to: u32,
    /// Index into the document's list of noteref targets when this character is
    /// part of a footnote marker; [`NO_NOTE`] otherwise. Per character because a
    /// marker starts and ends *between* characters, exactly like a colored span
    /// does.
    pub note: u32,
}

impl Placed {
    pub(crate) fn synthetic(ch: char, note: u32) -> Self {
        Self {
            ch,
            from: SYNTHETIC,
            to: SYNTHETIC,
            note,
        }
    }

    /// Not in the markup at all, so there are no bytes to wrap in a span.
    pub fn is_synthetic(&self) -> bool {
        self.from == SYNTHETIC
    }
}

/// [`Placed::from`] of a character that is not in the document.
pub const SYNTHETIC: u32 = u32::MAX;

/// [`Placed::note`] of a character that is not a footnote marker.
pub const NO_NOTE: u32 = u32::MAX;

/// A footnote marker's characters, as the reader lays them out.
///
/// The offsets are UTF-16 and index into the paragraph text — the same space
/// the analyzer's sentence offsets use — so the frontend can put a button over
/// the marker's characters wherever they ended up after coloring.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NoteRef {
    pub start: usize,
    pub len: usize,
    /// The footnote `id` the marker points at, which [`Document::footnotes`]
    /// (serialized as the chapter's `footnotes`) is keyed by.
    pub target: String,
}

/// One footnote's body, pulled out of the reading flow.
///
/// Footnote text is prose to a human but not to the analyzer: it is reference
/// matter, it sits outside the reading order, and coloring it would count it
/// in the whole-book job. So an `aside[epub:type~=footnote]` is captured whole
/// and shipped separately, the way an EPUB.js footnote plugin shows it on
/// demand instead of in the flow.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NoteBody {
    /// The aside's `id`, or a generated `note-N` when the book left it out.
    pub id: String,
    /// The aside's paragraphs, joined with spaces.
    pub text: String,
}

/// A chapter document's prose, in reading order.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Document {
    pub paragraphs: Vec<Paragraph>,
    /// Footnotes in document order; [`NoteRef::target`] names one of these.
    pub footnotes: Vec<NoteBody>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chapter {
    /// Index in the spine, which is also the reader's page order.
    pub index: usize,
    /// The chapter's title from the table of contents, when it has one.
    pub title: Option<String>,
    pub paragraphs: Vec<Paragraph>,
    /// Footnotes pulled out of the flow, in document order.
    pub footnotes: Vec<NoteBody>,
}

/// Why a book could not be read.
#[derive(Debug)]
pub enum Error {
    /// Not a readable EPUB.
    Invalid(String),
    /// A chapter's bytes could not be read or decoded.
    Chapter { index: usize, reason: String },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Invalid(reason) => write!(f, "不是 EPUB 文件：{reason}"),
            Error::Chapter { index, reason } => {
                write!(f, "无法读取第 {index} 章：{reason}")
            }
        }
    }
}

impl std::error::Error for Error {}

/// Every chapter of the book, in spine order.
///
/// `reader` is consumed because [`rbook::Epub`] borrows it for the lifetime of
/// the parsed book; callers pass an open file (`Library::book_file`) or a
/// `Cursor` over bytes.
pub fn chapters<R: std::io::Read + std::io::Seek + 'static>(
    reader: R,
) -> Result<Vec<Chapter>, Error> {
    let book = rbook::Epub::read(reader).map_err(|error| Error::Invalid(error.to_string()))?;
    let titles = chapter_titles(&book);

    let spine = book.spine();
    let mut out = Vec::with_capacity(spine.len());
    for entry in spine.iter() {
        let index = entry.order();
        let Some(manifest) = entry.manifest_entry() else {
            // A spine entry pointing at a missing manifest item: skip it
            // rather than fail the whole book, since the rest still reads.
            continue;
        };
        let bytes = manifest.read_bytes().map_err(|error| Error::Chapter {
            index,
            reason: error.to_string(),
        })?;
        let html = decode(&bytes);
        let doc = document(&html);
        out.push(Chapter {
            index,
            title: titles.get(&index).cloned(),
            paragraphs: doc.paragraphs,
            footnotes: doc.footnotes,
        });
    }
    Ok(out)
}

/// Spine index -> chapter title, from the table of contents.
///
/// The TOC links by href and may point into the middle of a document
/// (`chapter.xhtml#s3`), so the fragment is dropped before matching. A book
/// without a TOC simply has no titles.
fn chapter_titles(book: &rbook::Epub) -> HashMap<usize, String> {
    let mut titles = HashMap::new();
    // `Href::path` is the resolved, percent-normalized path with the query and
    // fragment removed, so both sides of the comparison use the same form.
    let mut by_path: HashMap<&str, usize> = HashMap::new();
    for entry in book.spine().iter() {
        if let Some(manifest) = entry.manifest_entry() {
            by_path.insert(manifest.href().path().as_str(), entry.order());
        }
    }
    // `flatten` walks nested TOC entries depth-first, so a parent is seen before
    // its children and `or_insert` keeps the outermost title for a document —
    // which is the chapter heading, not the first section inside it.
    for root in book.toc() {
        for entry in root.flatten() {
            let Some(href) = entry.href() else { continue };
            let Some(index) = by_path.get(href.path().as_str()).copied() else {
                continue;
            };
            let label = entry.label().trim();
            if !label.is_empty() {
                titles.entry(index).or_insert_with(|| label.to_owned());
            }
        }
    }
    titles
}

/// Decodes a chapter declaration's encoding, defaulting to UTF-8.
///
/// XHTML chapters are allowed to be in a legacy encoding (GB2312 shows up in
/// older Chinese books), and the parser cannot be handed bytes in an encoding
/// it was not told about.
pub(crate) fn decode(bytes: &[u8]) -> String {
    let bytes = bytes
        .strip_prefix([0xEF, 0xBB, 0xBF].as_slice())
        .unwrap_or(bytes);
    let Some(label) = declared_encoding(bytes) else {
        return String::from_utf8_lossy(bytes).into_owned();
    };
    let Some(encoding) = encoding_rs::Encoding::for_label(label.as_bytes()) else {
        return String::from_utf8_lossy(bytes).into_owned();
    };
    if encoding == encoding_rs::UTF_8 {
        return String::from_utf8_lossy(bytes).into_owned();
    }
    let (text, _, _) = encoding.decode(bytes);
    // The bytes are UTF-8 from here on, so a declaration still naming the
    // original encoding is a lie. It matters most on the way out: the exporter
    // writes this string back into the EPUB, and a reader that trusts the
    // declaration would decode UTF-8 as GBK and see mojibake.
    declare_utf8(&text)
}

/// Rewrites the `encoding` of an XML declaration to `utf-8`, if there is one.
fn declare_utf8(html: &str) -> String {
    // The declaration is ASCII and at the very start, so it is found by simple
    // search over the head of the document.
    let head = &html[..html.len().min(256)];
    let Some(start) = head.find("<?xml") else {
        return html.to_owned();
    };
    let Some(end) = head[start..].find("?>") else {
        return html.to_owned();
    };
    let declaration = &head[start..start + end];
    let Some(at) = declaration.find("encoding") else {
        return html.to_owned();
    };
    let rest = &declaration[at + "encoding".len()..];
    let Some(quoted) = rest.find(['"', '\'']) else {
        return html.to_owned();
    };
    let quote = &rest[quoted..quoted + 1];
    let Some(close) = rest[quoted + 1..].find(quote) else {
        return html.to_owned();
    };
    let value = start + at + "encoding".len() + quoted + 1;
    let mut out = String::with_capacity(html.len());
    out.push_str(&html[..value]);
    out.push_str(encoding_rs::UTF_8.name());
    out.push_str(&html[value + close..]);
    out
}

/// The `encoding="…"` value in the XML declaration, if the document has one.
pub(crate) fn declared_encoding(xml: &[u8]) -> Option<String> {
    // The declaration is ASCII and must be the first thing in the document, so
    // it is searched byte-wise: the bytes after it are in whatever encoding the
    // declaration names, which by definition is not UTF-8.
    let head = &xml[..xml.len().min(256)];
    let start = find(head, b"<?xml")?;
    let declaration = &head[start..];
    let end = find(declaration, b"?>")?;
    let declaration = &declaration[..end];
    let at = find(declaration, b"encoding")? + "encoding".len();
    let quote_at =
        at + find(&declaration[at..], b"\"").or_else(|| find(&declaration[at..], b"'"))?;
    let quote = declaration[quote_at];
    let value = &declaration[quote_at + 1..];
    let value_end = find(value, &[quote])?;
    Some(String::from_utf8_lossy(&value[..value_end]).into_owned())
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

/// One chapter document's prose, in reading order.
///
/// The offsets recorded in each [`Paragraph`] are only meaningful for the
/// *same* string that was parsed, and they are the reason the exporter cannot
/// work by "find `text` in `html`": `text` is whitespace-collapsed and
/// entity-decoded, so two paragraphs can collapse to the same string, and a
/// phrase can appear inside another one.
pub fn document(html: &str) -> Document {
    let mut reader = Reader::from_str(html);
    let config = reader.config_mut();
    // Real chapters are XHTML, not well-formed XML: converters leave mismatched
    // end tags behind and hand-write `&nbsp;`, and refusing either would fail a
    // whole book over one bad tag. Text is never trimmed here because a space
    // between two inline elements is a space in the paragraph.
    config.check_end_names = false;
    config.allow_unmatched_ends = true;
    config.allow_dangling_amp = true;

    // The footnote targets a noteref's characters point at, discovered while
    // parsing (a marker usually comes pages before its body). Kept outside
    // `state` because a frame flush borrows the state mutably and reads the
    // targets at the same time.
    let mut targets: Vec<String> = Vec::new();
    let mut state = ParseState::default();
    // Open frames, innermost last. Text lands in the innermost frame, so a
    // `<p>` inside a `<div>` collects its own text and the `<div>` keeps only
    // what sits outside the `<p>`.
    let mut stack: Vec<Frame> = Vec::new();
    // Nesting depth of skipped elements: `> 0` means "do not collect text".
    let mut skipped = 0usize;

    // A malformed tail must not lose the paragraphs read so far, so the loop
    // stops at the first parse error rather than propagating it.
    loop {
        // `buffer_position` before and after the read brackets exactly the bytes
        // this event consumed — a start tag, a text run, or `&amp;` — which is
        // what a rewrite has to replace. It is the one thing quick-xml does not
        // hand back with the event.
        let begin = byte_position(&reader);
        let Ok(event) = reader.read_event() else {
            break;
        };
        let end = byte_position(&reader);
        let span = (begin < end && end <= html.len()).then_some(begin..end);
        match event {
            Event::Start(start) => {
                let name = local_name(start.local_name().as_ref());
                if SKIPPED.contains(&name.as_str()) {
                    skipped += 1;
                } else {
                    if is_frame(&name) {
                        // A child block ends the text before it: `<div>见<p>字</p>后</div>`
                        // is "见", "字", "后". The parent frame stays open, so the
                        // text after the child still belongs to it — that is what
                        // keeps the paragraphs in reading order. The text before
                        // an aside belongs to the flow, so this flush runs
                        // before the attributes below switch the sink.
                        if let Some(frame) = stack.last_mut() {
                            frame.flush_text(&mut sink(&mut state), &targets);
                        }
                    }
                    // Attributes are read for every element, framed or not: a
                    // noteref anchor is inline and never a frame.
                    open_element(&start, &name, &mut state, &mut targets);
                    if is_frame(&name) {
                        stack.push(Frame::new(name));
                    }
                }
            }
            Event::End(end_tag) => {
                let name = local_name(end_tag.local_name().as_ref());
                if SKIPPED.contains(&name.as_str()) {
                    skipped = skipped.saturating_sub(1);
                } else if is_frame(&name) {
                    // Pop down to the matching frame. A stray end tag for a
                    // frame that was never opened (`allow_unmatched_ends`) must
                    // not pop a frame that is still open — nor close a capture
                    // that never opened.
                    if let Some(position) = stack.iter().rposition(|frame| frame.name == name) {
                        while stack.len() > position {
                            close_innermost(&mut stack, &mut state, &targets);
                        }
                        close_element(&name, &mut state);
                    }
                } else {
                    // An inline element (`a`) has no frame to match; its own
                    // end tag is enough to close its capture state.
                    close_element(&name, &mut state);
                }
            }
            Event::Empty(empty) => {
                // `<br/>` ends the current line but not the paragraph; an empty
                // block element (`<p/>`) has no text and so emits nothing.
                let name = local_name(empty.local_name().as_ref());
                if name == "br"
                    && let Some(frame) = stack.last_mut()
                {
                    // A synthesized space: it is in the paragraph text but not
                    // in the markup, so it has no bytes to point at.
                    frame.push_placed(Placed::synthetic(' ', NO_NOTE));
                }
            }
            Event::Text(text) => {
                // `skipped` guards `<script>`/`<style>`/`<rt>`: their character
                // data is not prose even though it is text.
                if skipped == 0
                    && let Some(frame) = stack.last_mut()
                    && let Some(span) = span
                {
                    // `into_inner` rather than `xml_content`: the offsets are
                    // walked over these bytes, so normalizing `\r\n` here
                    // would shift every character after it. EOL normalization
                    // happens in `push_paragraph`, where the bytes no longer
                    // matter.
                    frame.push_str(&text.into_inner(), span, state.noteref.unwrap_or(NO_NOTE));
                }
            }
            Event::CData(cdata) => {
                if skipped == 0
                    && let Some(frame) = stack.last_mut()
                {
                    // Literal text, so nothing can be spliced into it: a `<span>`
                    // written between `<![CDATA[` and `]]>` would come out as
                    // visible text, not markup. The characters are still part of
                    // the paragraph — they have to be, or the offsets of
                    // everything after them would be wrong — but they are
                    // marked as having no bytes of their own, which is what
                    // tells the exporter to leave them alone.
                    let note = state.noteref.unwrap_or(NO_NOTE);
                    for ch in cdata.into_inner().chars() {
                        frame.push_placed(Placed::synthetic(ch, note));
                    }
                }
            }
            Event::GeneralRef(reference) => {
                // quick-xml splits `&…;` out of the text, so entities have to be
                // resolved here: numeric refs, XML's five, and the HTML ones a
                // hand-written chapter is likely to contain.
                if skipped == 0
                    && let Some(frame) = stack.last_mut()
                    && let Some(span) = span
                {
                    let name: &str = reference.as_ref();
                    let text = match reference.resolve_char_ref() {
                        Ok(Some(ch)) => ch.to_string(),
                        _ => match entity_text(name) {
                            Some(entity) => entity.to_owned(),
                            None => continue,
                        },
                    };
                    // The whole `&name;` is one span for every character it
                    // resolves to, so replacing any of them replaces the
                    // reference rather than leaving a dangling `&`.
                    frame.push_ref(&text, span, state.noteref.unwrap_or(NO_NOTE));
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    // A document that ends in the middle of a paragraph still has that text.
    while !stack.is_empty() {
        close_innermost(&mut stack, &mut state, &targets);
    }
    // ...and a document truncated inside a footnote keeps the note so far.
    finish_note(&mut state);
    Document {
        paragraphs: state.flow,
        footnotes: state.notes,
    }
}

/// The paragraphs of one chapter document, in reading order.
///
/// The exporter re-parses each chapter with this (rather than reusing the
/// reader's [`Chapter`]s) so that what it colors and what the reader shows are
/// produced by the *same* call — footnote asides are out of both.
pub fn paragraphs(html: &str) -> Vec<Paragraph> {
    document(html).paragraphs
}

/// Everything the event loop accumulates for one document.
#[derive(Default)]
struct ParseState {
    /// The reading flow: every paragraph not swallowed by a note capture.
    flow: Vec<Paragraph>,
    /// Footnotes captured so far, in document order.
    notes: Vec<NoteBody>,
    /// The open `aside[epub:type~=footnote]`, when one is being captured.
    note: Option<NoteCapture>,
    /// How many open `aside[epub:type~=footnotes]` *sections* enclose the
    /// reader. A section holds many notes; its own loose text between them is
    /// not prose and not one note, so it goes nowhere.
    sections: usize,
    /// The `a[epub:type~=noteref]` currently open, as an index into the
    /// document's target list; its characters are marked with it.
    noteref: Option<u32>,
    /// Counter for footnote ids a book did not give.
    generated: usize,
}

/// One open `aside[epub:type~=footnote]`.
struct NoteCapture {
    /// The aside's `id`, or a generated one.
    id: String,
    /// The aside's paragraph texts so far.
    texts: Vec<String>,
}

/// Where a finished paragraph goes: the reading flow, the note being captured,
/// or nowhere (a notes section's own loose text is not prose).
enum Sink<'a> {
    Flow(&'a mut Vec<Paragraph>),
    Note(&'a mut Vec<String>),
    Drop,
}

impl Sink<'_> {
    fn take(&mut self, paragraph: Paragraph) {
        match self {
            Sink::Flow(out) => out.push(paragraph),
            // A note keeps only its text: markers inside a note body (the
            // little back-links real books ship) are dropped with the paragraph
            // they were on.
            Sink::Note(texts) => texts.push(paragraph.text),
            Sink::Drop => {}
        }
    }
}

/// The sink a finished paragraph goes to right now.
fn sink(state: &mut ParseState) -> Sink<'_> {
    if let Some(note) = state.note.as_mut() {
        Sink::Note(&mut note.texts)
    } else if state.sections > 0 {
        Sink::Drop
    } else {
        Sink::Flow(&mut state.flow)
    }
}

/// Starts the capture state an element's attributes call for.
///
/// Only `aside[epub:type~=footnote]` captures a note and only
/// `a[epub:type~=noteref]` marks its characters as a marker; every other
/// element parses as before. `epub:type` values are space-separated term sets,
/// so membership is checked per term.
fn open_element(start: &BytesStart, name: &str, state: &mut ParseState, targets: &mut Vec<String>) {
    match name {
        "aside" => {
            let types = attribute(start, "type").unwrap_or_default();
            let terms: Vec<&str> = types.split_whitespace().collect();
            if terms.iter().any(|term| matches!(*term, "footnote" | "endnote")) {
                let id = attribute(start, "id").unwrap_or_else(|| {
                    let id = format!("note-{}", state.generated);
                    state.generated += 1;
                    id
                });
                state.note = Some(NoteCapture {
                    id,
                    texts: Vec::new(),
                });
            } else if terms
                .iter()
                .any(|term| matches!(*term, "footnotes" | "endnotes"))
            {
                state.sections += 1;
            }
        }
        "a" => {
            let is_marker = attribute(start, "type").is_some_and(|types| {
                types.split_whitespace().any(|term| term == "noteref")
            });
            if is_marker
                // Only a same-document target can be shown on demand: a marker
                // whose `href` names another spine document keeps its
                // characters as plain text.
                && let Some(fragment) = attribute(start, "href")
                    .and_then(|href| href.split_once('#').map(|(_, fragment)| fragment.to_owned()))
                && !fragment.is_empty()
            {
                targets.push(fragment);
                state.noteref = Some(u32::try_from(targets.len() - 1).unwrap_or(NO_NOTE));
            }
        }
        _ => {}
    }
}

/// Ends the capture state the element opened, if it is still open.
fn close_element(name: &str, state: &mut ParseState) {
    match name {
        "aside" => finish_note(state),
        "a" => state.noteref = None,
        _ => {}
    }
}

/// Closes the open note capture, or unwinds one notes section.
fn finish_note(state: &mut ParseState) {
    if let Some(capture) = state.note.take() {
        // The paragraphs of one aside join with a space, the way a `<br/>`
        // separated line does.
        let text = capture.texts.join(" ");
        if !text.is_empty() {
            state.notes.push(NoteBody {
                id: capture.id,
                text,
            });
        }
    } else {
        state.sections = state.sections.saturating_sub(1);
    }
}

/// The value of an attribute matched by local name, if the element has one.
///
/// `epub:type` arrives with whatever prefix the document bound to the epub
/// namespace, so the match goes through `local_name()`, which already strips
/// everything up to the first `:`; the only attributes read this way (`type`,
/// `id`, `href`) are ASCII, so the compare ignores case without dragging in
/// Unicode tables.
fn attribute(start: &BytesStart, local: &str) -> Option<String> {
    start.attributes().filter_map(Result::ok).find_map(|attr| {
        let is_match = attr
            .key
            .local_name()
            .as_ref()
            .eq_ignore_ascii_case(local);
        if is_match {
            attr.normalized_value(XmlVersion::Implicit1_0)
                .ok()
                .map(|value| value.into_owned())
        } else {
            None
        }
    })
}

fn close_innermost(stack: &mut Vec<Frame>, state: &mut ParseState, targets: &[String]) {
    if let Some(frame) = stack.pop() {
        frame.flush(&mut sink(state), targets);
    }
}

/// One open block element, plus the characters collected directly inside it.
struct Frame {
    name: String,
    /// The characters of the paragraph being collected, in reading order.
    chars: Vec<Placed>,
}

impl Frame {
    fn new(name: String) -> Self {
        Self {
            name,
            chars: Vec::new(),
        }
    }

    fn push_placed(&mut self, placed: Placed) {
        self.chars.push(placed);
    }

    /// Records a markup event's text, remembering where each character sits.
    ///
    /// `text` has to be the source bytes of `span` verbatim — the offsets are
    /// computed by walking it — which is why the parser hands over
    /// [`Event::Text`]'s own content instead of a normalized copy: entity
    /// references are separate events, so a text run is exactly its bytes.
    /// `note` marks every character as belonging to that footnote marker, when
    /// the run sits inside a noteref anchor.
    fn push_str(&mut self, text: &str, span: std::ops::Range<usize>, note: u32) {
        for (offset, ch) in text.char_indices() {
            let from = byte_in_document(span.start + offset);
            let to = byte_in_document(span.start + offset + ch.len_utf8());
            self.chars.push(Placed { ch, from, to, note });
        }
    }

    /// Records an entity reference's text.
    ///
    /// Every character it resolves to points at the whole `&name;`, because
    /// that is the unit a rewrite can replace: wrapping part of it would leave
    /// a dangling `&` in the document.
    fn push_ref(&mut self, text: &str, span: std::ops::Range<usize>, note: u32) {
        let from = byte_in_document(span.start);
        let to = byte_in_document(span.end);
        for ch in text.chars() {
            self.chars.push(Placed { ch, from, to, note });
        }
    }

    /// Ends the paragraph this frame has collected so far, keeping the frame
    /// open for whatever follows.
    fn flush_text(&mut self, sink: &mut Sink, targets: &[String]) {
        // A container's own text is a paragraph too (the `<div>` + `<br>` shape).
        // Indentation between nested blocks collapses to nothing and is dropped
        // by `push_paragraph`, so no "did I hold a child block" bookkeeping is
        // needed.
        if self.chars.is_empty() {
            return;
        }
        let kind = kind_of(&self.name);
        if let Some(paragraph) = push_paragraph(kind, std::mem::take(&mut self.chars), targets) {
            sink.take(paragraph);
        }
    }

    fn flush(mut self, sink: &mut Sink, targets: &[String]) {
        self.flush_text(sink, targets);
    }
}

/// Byte position in the document that the reader will resume from.
///
/// Before a read it is where the next event starts; after one it is just past
/// that event, so bracketing the two gives the event's own bytes.
fn byte_position<R: std::io::BufRead>(reader: &Reader<R>) -> usize {
    usize::try_from(reader.buffer_position()).unwrap_or(usize::MAX)
}

/// A document byte offset as [`Placed`] stores it.
///
/// A chapter larger than 4 GiB cannot be pointed at, and is not worth failing
/// over: it becomes [`SYNTHETIC`], which means "no bytes" and costs a span
/// rather than producing a wrong one.
fn byte_in_document(at: usize) -> u32 {
    u32::try_from(at).unwrap_or(SYNTHETIC)
}

/// Entities that XHTML chapters use and XML does not define.
/// Lowercase tag name (`xhtml:p` -> `p`); quick-xml has already stripped the
/// namespace prefix for us.
fn local_name(name: &str) -> String {
    name.to_ascii_lowercase()
}

/// Text for an entity reference: XML's five first, then the HTML ones prose uses.
fn entity_text(name: &str) -> Option<&'static str> {
    quick_xml::escape::resolve_predefined_entity(name).or_else(|| html_entity(name))
}

/// Collapses one paragraph's characters into its text.
///
/// Runs of whitespace collapse to one space, which is what the analyzer is
/// given; the surviving characters keep the markup bytes they came from, so the
/// exporter can put spans back where they belong. A collapsed space is charged
/// to the *first* character of the run that follows it, because a span boundary
/// must not fall between the space and the word it precedes.
///
/// The marker runs come out of the same walk: `kept` lines up with `text` one
/// character to one character, so counting `len_utf16` as it goes gives each
/// run's offset in the UTF-16 space the reader lays out in. `targets` turns a
/// run's `Placed::note` index into the footnote id it points at.
pub fn push_paragraph(
    kind: ParagraphKind,
    chars: Vec<Placed>,
    targets: &[String],
) -> Option<Paragraph> {
    let mut text = String::new();
    let mut kept: Vec<Placed> = Vec::with_capacity(chars.len());
    // Whitespace seen but not yet emitted: one run may end in spaces and the
    // next begin immediately, and the collapse owes exactly one space between
    // them.
    let mut pending_space = false;
    for placed in chars {
        let collapse = placed.ch.is_whitespace() || placed.ch == '\u{a0}';
        if collapse {
            pending_space = true;
            continue;
        }
        if pending_space && !text.is_empty() {
            // The space is not in the source, so it has no bytes of its own; it
            // is attributed to this character's span, keeping the two together.
            // It is not part of a footnote marker even when it sits between the
            // prose and one: the space precedes the marker, and charging it to
            // the marker would widen the run past the marker's own characters.
            text.push(' ');
            kept.push(Placed {
                ch: ' ',
                note: NO_NOTE,
                ..placed.clone()
            });
        }
        pending_space = false;
        text.push(placed.ch);
        kept.push(placed);
    }
    if text.is_empty() {
        return None;
    }
    Some(Paragraph {
        kind,
        note_refs: marker_runs(&kept, targets),
        text,
        source: kept,
    })
}

/// Groups a paragraph's characters into the footnote markers they belong to.
///
/// A marker's characters are contiguous, so one run of equal `Placed::note`
/// values is one marker; a synthetic or unmarked character closes the run.
fn marker_runs(kept: &[Placed], targets: &[String]) -> Vec<NoteRef> {
    let mut refs = Vec::new();
    let mut at = 0usize;
    let mut run: Option<(u32, usize)> = None;
    for placed in kept {
        match run {
            Some((index, _)) if index == placed.note => {}
            Some((index, start)) => {
                close_run(&mut refs, index, start, at, targets);
                run = (placed.note != NO_NOTE).then_some((placed.note, at));
            }
            None if placed.note != NO_NOTE => run = Some((placed.note, at)),
            None => {}
        }
        at += placed.ch.len_utf16();
    }
    if let Some((index, start)) = run {
        close_run(&mut refs, index, start, at, targets);
    }
    refs
}

fn close_run(
    refs: &mut Vec<NoteRef>,
    index: u32,
    start: usize,
    end: usize,
    targets: &[String],
) {
    // An index the parser never registered a target for is a parse that went
    // wrong in a way nothing else noticed; dropping the marker beats a button
    // that shows nothing.
    if let Some(target) = targets.get(index as usize) {
        refs.push(NoteRef {
            start,
            len: end - start,
            target: target.clone(),
        });
    }
}

/// HTML entities that XHTML chapters use and XML does not define.
///
/// `quick-xml` with the `escape-html` feature knows all 2231 of them, but the
/// crate's own comment warns that the table costs 10+ seconds of compile time,
/// and prose only ever uses these. Adding one here is cheaper than the feature.
fn html_entity(name: &str) -> Option<&'static str> {
    Some(match name {
        // `nbsp` is deliberately absent: it is a space, and a space in a
        // paragraph is collapsed to an ordinary one before analysis.
        "mdash" => "—",
        "ndash" => "–",
        "hellip" => "…",
        "lsquo" => "\u{2018}",
        "rsquo" => "\u{2019}",
        "ldquo" => "\u{201c}",
        "rdquo" => "\u{201d}",
        "middot" => "·",
        "bull" => "•",
        "copy" => "©",
        "reg" => "®",
        "trade" => "™",
        "deg" => "°",
        "times" => "×",
        "divide" => "÷",
        "shy" => "",
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texts(html: &str) -> Vec<String> {
        paragraphs(html)
            .into_iter()
            .map(|paragraph| paragraph.text)
            .collect()
    }

    #[test]
    fn takes_one_paragraph_per_block_element() {
        let html = "<html><body><p>第一段。</p><p>第二段。</p></body></html>";
        assert_eq!(texts(html), ["第一段。", "第二段。"]);
    }

    #[test]
    fn flattens_inline_markup_into_the_paragraph() {
        let html = r#"<body><p>他说：<em>走</em>吧<span class="x">。</span></p></body>"#;
        assert_eq!(texts(html), ["他说：走吧。"]);
    }

    #[test]
    fn collapses_source_indentation_but_keeps_spaces_between_words() {
        let html = "<body>\n  <p>\n    第一句。\n    第二句。\n  </p>\n</body>";
        assert_eq!(texts(html), ["第一句。 第二句。"]);
    }

    #[test]
    fn skips_script_style_and_ruby_annotations() {
        let html = r#"<body>
            <style>p { color: red }</style>
            <p>正文。</p>
            <p><ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby></p>
            <script>alert(1)</script>
        </body>"#;
        assert_eq!(texts(html), ["正文。", "漢字"]);
    }

    #[test]
    fn an_empty_or_whitespace_only_paragraph_is_dropped() {
        let html = "<body><p>　</p><p> </p><p>有字。</p></body>";
        assert_eq!(texts(html), ["有字。"]);
    }

    #[test]
    fn a_block_nested_in_a_block_still_ends_the_paragraph_before_it() {
        // Two blocks are two paragraphs even when one is inside the other, and
        // they come out in reading order rather than inner-first.
        let html = "<body><div>第一段<div>内层</div>第三段</div></body>";
        assert_eq!(texts(html), ["第一段", "内层", "第三段"]);
    }

    #[test]
    fn a_wrapper_element_around_paragraphs_adds_no_paragraph_of_its_own() {
        // The shape one of the sample books uses for whole chapters: everything
        // inside a single `<div>`, with the real paragraphs as `<p>` children.
        let html = r#"<body><div class="body"><p>一。</p><p>二。</p><p>三。</p></div></body>"#;
        assert_eq!(texts(html), ["一。", "二。", "三。"]);
    }

    #[test]
    fn a_br_separated_div_is_read_as_one_paragraph() {
        // The other common shape: no `<p>` at all, lines split by `<br/>`.
        let html = "<body><div>第一行<br/>第二行</div></body>";
        assert_eq!(texts(html), ["第一行 第二行"]);
    }

    #[test]
    fn a_list_item_holding_a_paragraph_does_not_duplicate_its_text() {
        let html = "<body><ul><li><p>条目一。</p></li><li>条目二。</li></ul></body>";
        assert_eq!(texts(html), ["条目一。", "条目二。"]);
    }

    #[test]
    fn resolves_html_entities_that_xml_does_not_define() {
        let html = "<body><p>a&mdash;b&hellip;&#65;&#x42;</p></body>";
        assert_eq!(texts(html), ["a—b…AB"]);
    }

    #[test]
    fn a_non_breaking_space_collapses_like_any_other_space() {
        // `&nbsp;` is a space to a reader; leaving U+00A0 in the paragraph would
        // hand the analyzer a character it never sees in the same place twice.
        let html = "<body><p>a&nbsp;b</p></body>";
        assert_eq!(texts(html), ["a b"]);
    }

    #[test]
    fn a_truncated_document_keeps_the_paragraph_it_was_reading() {
        // Malformed input is common; the text collected so far is still text.
        let html = "<body><p>完整。</p><p>被截断";
        assert_eq!(texts(html), ["完整。", "被截断"]);
    }

    #[test]
    fn decodes_a_legacy_declared_encoding() {
        // GBK bytes for 中文, with a declaration that says so.
        let mut bytes = br#"<?xml version="1.0" encoding="gbk"?><body><p>"#.to_vec();
        bytes.extend_from_slice(&[0xD6, 0xD0, 0xCE, 0xC4]);
        bytes.extend_from_slice(b"</p></body>");
        let html = decode(&bytes);
        assert_eq!(texts(&html), ["中文"]);
        // Decoding produces UTF-8, so the declaration must say so: the exporter
        // writes this string back into the EPUB, and a reader that trusted the
        // stale label would show mojibake.
        assert!(
            html.starts_with(r#"<?xml version="1.0" encoding="UTF-8"?>"#),
            "{html}"
        );
    }

    #[test]
    fn leaves_a_utf8_declaration_alone() {
        let html = decode(r#"<?xml version="1.0" encoding="utf-8"?><p>字。</p>"#.as_bytes());
        assert_eq!(html, r#"<?xml version="1.0" encoding="utf-8"?><p>字。</p>"#);
        // A document with no declaration at all is not given one.
        assert_eq!(decode(b"<p>x</p>"), "<p>x</p>");
    }

    #[test]
    fn reads_the_encoding_label_only_from_the_declaration() {
        assert_eq!(
            declared_encoding(br#"<?xml version="1.0" encoding="GB2312"?><html/>"#).as_deref(),
            Some("GB2312")
        );
        assert_eq!(declared_encoding(b"<html/>"), None);
        // A stray `encoding=` inside the body is not a declaration.
        assert_eq!(
            declared_encoding(br#"<html><p>encoding="gbk"</p></html>"#),
            None
        );
    }

    #[test]
    fn a_paragraph_with_only_markup_is_dropped() {
        let html = "<body><p><img src=\"a.png\"/></p><p>字。</p></body>";
        assert_eq!(texts(html), ["字。"]);
    }

    #[test]
    fn a_heading_carries_its_level() {
        let html = "<body><h2>第二章</h2><p>正文。</p></body>";
        let doc = document(html);
        let kinds: Vec<&'static str> = doc.paragraphs.iter().map(|p| p.kind.tag()).collect();
        assert_eq!(kinds, ["h2", "p"]);
    }

    #[test]
    fn a_footnote_aside_is_pulled_out_of_the_flow() {
        // The EPUB 3 shape: the marker is an `a[epub:type=noteref]` inside the
        // paragraph, the body is an `aside[epub:type=footnote]` after it.
        // `r##` because the hrefs contain `"#`.
        let html = r##"<body>
            <p>他走了。<a epub:type="noteref" href="#fn1">①</a></p>
            <aside epub:type="footnote" id="fn1"><p>他死于一月。</p></aside>
            <p>下一段。</p>
        </body>"##;
        let doc = document(html);
        assert_eq!(
            doc.paragraphs.iter().map(|p| p.text.as_str()).collect::<Vec<_>>(),
            ["他走了。①", "下一段。"],
            "脚注正文不能混进阅读流"
        );
        assert_eq!(
            doc.footnotes,
            [NoteBody { id: "fn1".into(), text: "他死于一月。".into() }]
        );
        // The marker's characters stay in the paragraph text and are pointed at
        // by a UTF-16 range, which is what the frontend needs to lay a button
        // over them.
        let marker = &doc.paragraphs[0].note_refs;
        assert_eq!(
            marker,
            &[NoteRef {
                start: 4,
                len: 1,
                target: "fn1".into()
            }][..]
        );
    }

    #[test]
    fn a_footnote_without_an_id_gets_a_generated_one() {
        let html = r#"<body><aside epub:type="footnote"><p>注。</p></aside></body>"#;
        let doc = document(html);
        assert_eq!(doc.footnotes[0].id, "note-0");
    }

    #[test]
    fn an_aside_without_epub_type_stays_in_the_flow() {
        // Not every aside is a footnote; only `epub:type` says so.
        let html = r#"<body><aside><p>侧栏文字。</p></aside></body>"#;
        let doc = document(html);
        assert_eq!(texts(html), ["侧栏文字。"]);
        assert!(doc.footnotes.is_empty());
    }

    #[test]
    fn a_noteref_without_a_fragment_keeps_plain_text() {
        // The href may point at another document; without a `#fragment` there
        // is nothing in this chapter to point at, so the characters stay plain.
        let html = r#"<body><p>他走了。<a epub:type="noteref" href="notes.xhtml">①</a></p></body>"#;
        let doc = document(html);
        assert_eq!(doc.paragraphs[0].text, "他走了。①");
        assert!(doc.paragraphs[0].note_refs.is_empty());
    }

    #[test]
    fn a_notes_section_holds_one_note_per_footnote_aside() {
        // Some books wrap their notes in a plural section: each inner aside is
        // one note, and the section's own loose text between them is not prose.
        let html = r#"<body>
            <aside epub:type="footnotes">
                <aside epub:type="footnote" id="n1"><p>注一。</p></aside>
                页脚文字
                <aside epub:type="footnote" id="n2"><p>注二。</p></aside>
            </aside>
        </body>"#;
        let doc = document(html);
        assert!(doc.paragraphs.is_empty(), "节内散落的文字不该进阅读流");
        assert_eq!(
            doc.footnotes.iter().map(|n| n.id.as_str()).collect::<Vec<_>>(),
            ["n1", "n2"]
        );
    }

    #[test]
    fn a_multi_paragraph_footnote_joins_with_spaces() {
        let html = r#"<body><aside epub:type="footnote" id="fn2"><p>一句。</p><p>两句。</p></aside></body>"#;
        let doc = document(html);
        assert_eq!(doc.footnotes[0].text, "一句。 两句。");
    }

    #[test]
    fn a_noteref_marker_survives_whitespace_collapse() {
        // The marker text and the prose around it may be separated by source
        // whitespace, which collapses; the range must still cover the marker
        // characters exactly.
        let html = r##"<body>
            <p>他走了
            <a epub:type="noteref" href="#fn1">1</a>。</p>
            <aside epub:type="footnote" id="fn1"><p>注。</p></aside>
        </body>"##;
        let doc = document(html);
        assert_eq!(doc.paragraphs[0].text, "他走了 1。");
        assert_eq!(
            doc.paragraphs[0].note_refs,
            [NoteRef { start: 4, len: 1, target: "fn1".into() }]
        );
    }
}
