#!/usr/bin/env python3
"""
Test script to verify keyword extraction functionality.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer import AnalyzerFactory, KeywordAnalyzer


def test_keyword_analyzer():
    """Test the KeywordAnalyzer with different methods."""
    test_text = """
    人工智能技术在医疗领域的应用越来越广泛，包括辅助诊断、药物研发和个性化治疗等方面。
    机器学习算法可以帮助医生进行疾病诊断，深度学习模型在医学影像分析方面表现出色。
    自然语言处理技术可以用于病历分析和医学文献挖掘，提高医疗服务的效率和质量。
    区块链技术在医疗数据安全和隐私保护方面也显示出巨大潜力。
    """

    methods = ["tfidf", "keybert", "textrank", "yake"]

    print("=" * 60)
    print("测试关键词提取功能")
    print("=" * 60)

    for method in methods:
        print(f"\n【{method.upper()} 方法】")
        print("-" * 40)

        try:
            analyzer = KeywordAnalyzer(method=method)
            results = analyzer.analyze(test_text)

            print(f"提取的关键词数量: {len(results.get('text', []))}")

            for i, (keyword, score, importance) in enumerate(
                results.get("text", [])[:10], 1
            ):
                importance_symbol = {
                    "super": "🔶",
                    "required": "🔷",
                    "important": "🔹",
                }.get(importance, "•")

                print(
                    f"{i:2d}. {importance_symbol} {keyword:<15} (分数: {score:.4f}, 级别: {importance})"
                )

        except Exception as e:
            print(f"❌ 错误: {e}")


def test_analyzer_factory():
    """Test the AnalyzerFactory."""
    print("\n" + "=" * 60)
    print("测试分析器工厂")
    print("=" * 60)

    # Test SVO analyzer
    print("\n【SVO 分析器】")
    try:
        svo_analyzer = AnalyzerFactory.create_analyzer(mode="svo")
        print(f"✅ 成功创建 SVO 分析器: {type(svo_analyzer).__name__}")
    except Exception as e:
        print(f"❌ 错误: {e}")

    # Test Keyword analyzers
    methods = ["tfidf", "keybert", "textrank", "yake"]
    for method in methods:
        print(f"\n【关键词分析器 - {method.upper()}】")
        try:
            keyword_analyzer = AnalyzerFactory.create_analyzer(
                mode="keywords", method=method
            )
            print(f"✅ 成功创建关键词分析器: {type(keyword_analyzer).__name__}")
        except Exception as e:
            print(f"❌ 错误: {e}")

    # Test invalid mode
    print(f"\n【无效模式测试】")
    try:
        invalid_analyzer = AnalyzerFactory.create_analyzer(mode="invalid")
        print("❌ 应该抛出错误但没有")
    except ValueError as e:
        print(f"✅ 正确捕获错误: {e}")


def test_importance_classification():
    """Test keyword importance classification."""
    print("\n" + "=" * 60)
    print("测试重要性分级")
    print("=" * 60)

    test_keywords = [
        ("人工智能", 0.95),
        ("医疗", 0.85),
        ("技术", 0.75),
        ("应用", 0.65),
        ("分析", 0.55),
        ("算法", 0.45),
        ("数据", 0.35),
        ("模型", 0.25),
    ]

    try:
        analyzer = KeywordAnalyzer(method="tfidf")
        classified = analyzer._classify_importance(test_keywords)

        print("\n重要性分级结果:")
        for keyword, score, importance in classified:
            color = {"super": "🟠", "required": "🟣", "important": "🟢"}.get(
                importance, "⚪"
            )

            print(f"{color} {keyword:<10} (分数: {score:.2f}, 级别: {importance})")

    except Exception as e:
        print(f"❌ 错误: {e}")


if __name__ == "__main__":
    print("🧪 开始测试 bioCN 关键词提取功能\n")

    test_keyword_analyzer()
    test_analyzer_factory()
    test_importance_classification()

    print("\n" + "=" * 60)
    print("✅ 测试完成！")
    print("=" * 60)
