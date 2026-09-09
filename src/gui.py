"""Kivy GUI for the Bionic Reading EPUB processor.

Thin shell over EpubParser/ChineseAnalyzer: pick an EPUB, pick an output
location, hit "Process", watch the progress bar. The slow work (HanLP model
load + annotation) runs in a background thread so the UI stays responsive.
"""

from __future__ import annotations

import threading
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar

from src.analyzer import ChineseAnalyzer
from src.epub_parser import EpubParser


# Kivy 默认字体不含中文字形，探测各系统常见 CJK 字体并注册。
def _register_cjk_font() -> str:
    """Register a system CJK font; return its name, or "Roboto" if none found."""
    candidates = [
        # Linux
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        # Windows
        r"C:/Windows/Fonts/msyh.ttc",
        r"C:/Windows/Fonts/simhei.ttf",
        r"C:/Windows/Fonts/simsun.ttc",
    ]
    for path in candidates:
        if Path(path).is_file():
            LabelBase.register(name="cjk", fn_regular=path)
            return "cjk"
    return "Roboto"


_FONT = _register_cjk_font()


def _pick(title: str, filters: list[str], on_pick) -> None:
    """Open a file chooser popup; call on_pick(path) on confirm."""
    chooser = FileChooserListView(
        path=str(Path.home()),
        font_name=_FONT,
        filters=filters,
        filter_dirs=True,
        dirselect=False,
    )

    content = BoxLayout(orientation="vertical", spacing=8, padding=8)
    content.add_widget(chooser)

    actions = BoxLayout(size_hint_y=None, height=48, spacing=8)
    cancel = Button(text="取消", font_name=_FONT)
    confirm = Button(text="确定", font_name=_FONT, bold=True)
    actions.add_widget(cancel)
    actions.add_widget(confirm)
    content.add_widget(actions)

    popup = Popup(
        title=title,
        title_font=_FONT,
        content=content,
        size_hint=(0.9, 0.9),
    )
    cancel.bind(on_release=popup.dismiss)
    confirm.bind(on_release=lambda *_: _finish(chooser, on_pick, popup))
    chooser.bind(on_submit=lambda *_: _finish(chooser, on_pick, popup))
    popup.open()


def _finish(chooser, on_pick, popup):
    if chooser.selection:
        path = Path(chooser.selection[0])
        if path.is_dir():
            return
        on_pick(path)
        popup.dismiss()


class BioCNRoot(BoxLayout):
    input_path = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = 16
        self.spacing = 12
        self._build()

    def _build(self):
        self.add_widget(
            Label(
                text="中文仿生阅读 EPUB 处理器",
                font_name=_FONT,
                font_size=22,
                bold=True,
            )
        )
        self.input_label = self._label("请选择输入 EPUB 文件")
        self.output_label = self._label("默认输出到输入文件同目录 *_bio.epub")
        self.status_label = self._label("就绪")

        row = BoxLayout(spacing=8, size_hint_y=None, height=44)
        row.add_widget(
            Button(text="选择输入...", font_name=_FONT, on_release=self._pick_input)
        )
        self.add_widget(row)

        self.progress = ProgressBar(max=100, value=0)
        self.add_widget(self.progress)

        self.process_btn = Button(
            text="开始处理", font_name=_FONT, size_hint=(None, None), size=(160, 48)
        )
        self.process_btn.bind(on_release=self._process)
        self.add_widget(self.process_btn)

    def _label(self, text: str) -> Label:
        label = Label(text=text, font_name=_FONT, size_hint_y=None, height=30)
        self.add_widget(label)
        return label

    def _pick_input(self, *_):
        _pick("选择输入 EPUB", ["*.epub"], self._set_input)

    def _set_input(self, path: Path):
        self.input_path = path
        self.input_label.text = f"输入: {path}"

    def _process(self, *_):
        if not self.input_path:
            self.status_label.text = "请先选择输入 EPUB 文件"
            return
        output = self.input_path.parent / f"{self.input_path.stem}_bio.epub"
        self.process_btn.disabled = True
        self.progress.value = 0
        self.status_label.text = "正在后台处理，请稍候..."
        threading.Thread(
            target=self._worker, args=(str(self.input_path), str(output)), daemon=True
        ).start()

    def _worker(self, input_path: str, output_path: str):
        """Run the slow pipeline off the UI thread."""

        def status(msg):
            self._ui(lambda: setattr(self.status_label, "text", msg))

        try:
            analyzer = ChineseAnalyzer()
            status("模型已加载，开始分析...")

            parser = EpubParser(input_path)
            total = parser.get_document_count()
            self._ui(lambda: setattr(self.progress, "max", total))

            parser.parse_chinese(
                analyzer,
                progress_callback=lambda: self._ui(
                    lambda: setattr(self.progress, "value", self.progress.value + 1)
                ),
            )
            parser.save(output_path)
            status(f"完成! 输出: {output_path}")
        except Exception as exc:  # noqa: BLE001
            status(f"出错: {exc}")
        finally:
            self._ui(lambda: setattr(self.process_btn, "disabled", False))

    def _ui(self, fn):
        Clock.schedule_once(lambda dt: fn())


class BioCNApp(App):
    def build(self):
        Window.size = (560, 360)
        return BioCNRoot()


def main():
    BioCNApp().run()


if __name__ == "__main__":
    main()
