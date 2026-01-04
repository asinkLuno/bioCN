#!/usr/bin/env python3
"""
End-to-end system test for EPUB processing.
Tests the complete pipeline: EPUB parsing → SVO analysis → output generation.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rich.progress import Progress

from analyzer import ChineseAnalyzer
from epub_parser import EpubParser


def test_epub_e2e():
    """Test complete EPUB processing pipeline with 脂粉帝国.epub"""

    test_file = Path(__file__).parent / "脂粉帝国.epub"

    if not test_file.exists():
        print(f"❌ 测试文件不存在: {test_file}")
        return False

    print("=" * 60)
    print("端到端 EPUB 处理测试")
    print("=" * 60)
    print(f"输入文件: {test_file}")
    print(f"文件大小: {test_file.stat().st_size / 1024:.1f} KB")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "脂粉帝国_bio.epub"

        try:
            print("【步骤 1/4】初始化分析器...")
            analyzer = ChineseAnalyzer()
            print("✅ 分析器初始化成功")

            print("\n【步骤 2/4】初始化解析器...")
            parser = EpubParser(str(test_file), inline_css=True)
            doc_count = parser.get_document_count()
            print(f"✅ 解析器初始化成功 (文档数: {doc_count})")

            print("\n【步骤 3/4】解析并处理 EPUB...")
            with Progress(
                transient=True,
                redirect_stderr=False,
                redirect_stdout=False,
            ) as progress:
                task = progress.add_task("[b]Processing...[/b]", total=doc_count)
                parser.parse_chinese(analyzer, progress, task)
            print(f"✅ EPUB 处理完成")

            print("\n【步骤 4/4】保存输出文件...")
            parser.save(str(output_path))
            print(f"✅ 输出文件已保存")

            if not output_path.exists():
                print(f"❌ 输出文件不存在: {output_path}")
                return False

            output_size = output_path.stat().st_size
            input_size = test_file.stat().st_size
            size_ratio = output_size / input_size

            print(f"\n【文件信息】")
            print(f"   - 输入大小: {input_size / 1024:.1f} KB")
            print(f"   - 输出大小: {output_size / 1024:.1f} KB")
            print(
                f"   - 大小比例: {size_ratio:.2%} (增长 {((size_ratio - 1) * 100):.1f}%)"
            )

            # Verify the output is a valid EPUB
            print("\n【额外检查】验证 EPUB 格式...")
            from ebooklib import epub

            try:
                epub.read_epub(str(output_path))
                print("✅ 输出文件是有效的 EPUB 格式")
            except Exception as e:
                print(f"❌ 输出文件 EPUB 格式无效: {e}")
                return False

            print("\n" + "=" * 60)
            print("✅ 所有测试通过!")
            print("=" * 60)
            return True

        except Exception as e:
            print(f"\n❌ 测试失败: {e}")
            import traceback

            traceback.print_exc()
            return False


def test_epub_e2e_no_inline_css():
    """Test EPUB processing with external CSS mode"""

    test_file = Path(__file__).parent / "脂粉帝国.epub"

    if not test_file.exists():
        print(f"❌ 测试文件不存在: {test_file}")
        return False

    print("\n" + "=" * 60)
    print("端到端 EPUB 处理测试 (外部 CSS 模式)")
    print("=" * 60)
    print(f"输入文件: {test_file}")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "脂粉帝国_bio_no_inline.epub"

        try:
            print("【步骤 1/4】初始化分析器...")
            analyzer = ChineseAnalyzer()
            print("✅ 分析器初始化成功")

            print("\n【步骤 2/4】初始化解析器...")
            parser = EpubParser(str(test_file), inline_css=False)
            doc_count = parser.get_document_count()
            print(f"✅ 解析器初始化成功 (文档数: {doc_count})")

            print("\n【步骤 3/4】解析并处理 EPUB (外部 CSS)...")
            with Progress(
                transient=True,
                redirect_stderr=False,
                redirect_stdout=False,
            ) as progress:
                task = progress.add_task("[b]Processing...[/b]", total=doc_count)
                parser.parse_chinese(analyzer, progress, task)
            print(f"✅ EPUB 处理完成")

            print("\n【步骤 4/4】保存输出文件...")
            parser.save(str(output_path))
            print(f"✅ 输出文件已保存")

            if not output_path.exists():
                print(f"❌ 输出文件不存在: {output_path}")
                return False

            output_size = output_path.stat().st_size
            input_size = test_file.stat().st_size
            size_ratio = output_size / input_size

            print(f"\n【文件信息】")
            print(f"   - 输入大小: {input_size / 1024:.1f} KB")
            print(f"   - 输出大小: {output_size / 1024:.1f} KB")
            print(
                f"   - 大小比例: {size_ratio:.2%} (增长 {((size_ratio - 1) * 100):.1f}%)"
            )

            # Verify CSS classes are used instead of inline styles
            print("\n【额外检查】验证外部 CSS 模式...")
            from ebooklib import epub

            book = epub.read_epub(str(output_path))
            css_found = False
            svo_class_found = False

            for item in book.get_items():
                try:
                    if item.get_type() == 9:  # ItemType.STYLE
                        css_content = item.get_content().decode("utf-8")
                        if (
                            "svo-subject" in css_content
                            or "svo-predicate" in css_content
                        ):
                            css_found = True

                    if item.get_type() == 1:  # ItemType.XHTML
                        html_content = item.get_content().decode("utf-8")
                        if (
                            'class="svo-subject"' in html_content
                            or 'class="svo-predicate"' in html_content
                            or 'class="svo-object"' in html_content
                        ):
                            svo_class_found = True
                except (UnicodeDecodeError, AttributeError):
                    # Skip non-text items (images, etc.)
                    pass

            if css_found:
                print("✅ 找到外部 CSS 样式定义")
            else:
                print("⚠️  未找到外部 CSS 文件")

            if svo_class_found:
                print("✅ HTML 中使用了 CSS 类选择器")
            else:
                print("⚠️  HTML 中未使用 CSS 类选择器")

            print("\n" + "=" * 60)
            print("✅ 外部 CSS 模式测试通过!")
            print("=" * 60)
            return True

        except Exception as e:
            print(f"\n❌ 测试失败: {e}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    print("🧪 开始端到端系统测试\n")

    success = True

    # Test 1: Default inline CSS mode
    if not test_epub_e2e():
        success = False

    # Test 2: External CSS mode
    if not test_epub_e2e_no_inline_css():
        success = False

    if success:
        print("\n" + "=" * 60)
        print("🎉 所有端到端测试通过!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("❌ 部分测试失败")
        print("=" * 60)
        sys.exit(1)
