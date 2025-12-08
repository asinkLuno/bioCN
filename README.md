# BioCN - Bionic Reading EPUB Processor

A minimalist EPUB processor that applies bionic reading formatting to both Chinese and English texts.

**Philosophy**: No fallbacks, no silent failures. If analysis fails, the whole process fails. Problems are exposed directly.

**Core Design**: Inspired by Linus Torvalds' principles of "Good Taste" - eliminating special cases and edge conditions through better data structures and simpler implementations.

## Features

### 🎯 Two Processing Modes
- **SVO Mode (Chinese)**: Uses HanLP/DDParser for dependency parsing to identify subject-verb-object relationships
- **Bold-Prefix Mode (English)**: Bolds the first third of each English word for faster reading

### 🎨 Rich Visual Interface
- Colorful TUI with progress indicators using `rich`
- Clear configuration display with panels
- Real-time processing feedback

### ⚡ Zero-Bullshit Design
- No silent failures - errors are exposed immediately
- No compatibility layers - dependencies must be properly installed
- No fallback modes - either works completely or fails cleanly

### 📚 EPUB-Aware Processing
- Preserves EPUB structure and metadata
- Adds "Bionic Reading" suffix to title and identifier
- Maintains all non-text content (images, styles, etc.)

## Installation

```bash
# Install with uv (recommended)
uv sync

# Or install with pip
pip install .
```

**Note for Chinese (SVO) Mode**: Requires `ddparser` and `paddlepaddle`:
```bash
pip install ddparser paddlepaddle
```

## Usage

The CLI accepts exactly 3 core parameters as requested:

```bash
biocn [--mode svo|bold-prefix] EPUB_PATH [--css-path CSS_PATH]
```

### Core Parameters

1. **`EPUB_PATH`** (required)
   - Path to input EPUB file
   - Must exist and be a valid EPUB file

2. **`--mode`** (optional, default: `svo`)
   - `svo`: Chinese subject-verb-object analysis (requires DDParser)
   - `bold-prefix`: English word prefix bolding

3. **`--css-path`** (optional)
   - Path to custom CSS file
   - If not provided, uses built-in default styles
   - If provided but file doesn't exist, raises `FileNotFoundError`

### Additional Options

- `--output, -o OUTPUT_PATH`: Custom output path (default: adds "_bionic" suffix)
- `--verbose, -v`: Show verbose output including tracebacks
- `--english`: Deprecated flag for backward compatibility (maps to `--mode bold-prefix`)

### Examples

#### Chinese Mode (SVO Analysis)
```bash
# Basic usage with default CSS
biocn book.epub

# With custom CSS
biocn book.epub --css-path custom_styles.css

# Specify output file
biocn book.epub --output bionic_book.epub
```

#### English Mode (Bold-Prefix)
```bash
# Process English text
biocn --mode bold-prefix book.epub

# Alternative (backward compatible)
biocn --english book.epub
```

#### Mixed Usage
```bash
# Verbose mode with custom output
biocn --mode svo --verbose book.epub --output processed.epub

# With custom CSS for Chinese mode
biocn --mode svo book.epub --css-path ~/my-styles.css
```

## How It Works

### Chinese (SVO) Mode
1. **Load EPUB**: Reads the EPUB file using `ebooklib`
2. **Extract Text**: Extracts Chinese text from HTML content using `BeautifulSoup4`
3. **Grammar Analysis**: Uses DDParser (via HanLP) for dependency parsing
4. **Identify SVO**: Marks subjects, verbs, and objects based on dependency relations
5. **Apply Styles**: Wraps SVO elements in HTML spans with CSS classes
6. **Generate Output**: Creates new EPUB with modified content and CSS styles

### English (Bold-Prefix) Mode
1. **Load EPUB**: Same as Chinese mode
2. **Extract English Text**: Identifies Latin-alphabet text
3. **Word Segmentation**: Splits text into words, preserving punctuation
4. **Bold Prefixes**: For each word, bolds the first third (minimum 1 character)
5. **Apply Styles**: Wraps processed words in HTML spans
6. **Generate Output**: Creates new EPUB with modified content

## CSS Styles

### Default Styles
The built-in CSS provides styles for both Chinese and English modes:

**Chinese Styles**:
- `.chinese-subject`: Blue, bold (主语)
- `.chinese-verb`: Red, bold (谓语)
- `.chinese-object`: Green, bold (宾语)
- `.chinese-normal`: Gray, normal weight (其他词)
- `.chinese-bionic-reading`: Container for Chinese text

**English Styles**:
- `.english-bold-prefix`: Container for English words
- `.english-bold-prefix b`: Bold portion (first third of word)
- `.english-bionic-reading`: Container for English text

### Custom CSS
You can provide your own CSS file. Make sure it includes appropriate styles for the mode you're using.

Example custom CSS for Chinese mode:
```css
.chinese-subject {
    font-weight: 900;
    color: #1a237e;
    background: #e8eaf6;
    border-radius: 3px;
    padding: 1px 3px;
}
```

## Development

### Project Structure
```
src/
├── cli.py              # Rich TUI with click
├── processor.py        # EPUB processor with mode switching
├── analyzer.py         # Chinese grammar analyzer (DDParser)
├── english_analyzer.py # English bold-prefix analyzer
├── css.py             # CSS manager
└── text_analyzer.py   # Base analyzer interface
```

### Running Tests
```bash
# Run system tests
python tests/test_bionic_reading.py

# Note: Chinese mode tests require DDParser
```

### Design Principles

1. **"Good Taste" Over Special Cases**: Unified `TextAnalyzer` interface eliminates `if english:` branches
2. **Never Break Userspace**: Backward compatibility maintained via `--english` flag mapping
3. **Practicality Over Purity**: Simple word segmentation for English, no over-engineering
4. **Minimal Complexity**: No fallback paths, no silent error handling

## Dependencies

- **Core**: `click`, `rich`, `ebooklib`, `beautifulsoup4`
- **Chinese Mode**: `hanlp>=2.1.3` (includes DDParser)
- **Optional**: `ddparser`, `paddlepaddle` (for Chinese grammar analysis)

## Troubleshooting

### "DDParser is required but not installed"
- **Cause**: Chinese mode requires DDParser
- **Fix**: `pip install ddparser paddlepaddle`

### "CSS file does not exist"
- **Cause**: Custom CSS path is invalid
- **Fix**: Provide valid CSS file path or omit `--css-path`

### "Invalid mode: X"
- **Cause**: Invalid `--mode` value
- **Fix**: Use `svo` or `bold-prefix`

### EPUB not processing any text
- **Cause**: Text doesn't match mode (e.g., English text in Chinese mode)
- **Fix**: Use appropriate mode for your content

## License

MIT