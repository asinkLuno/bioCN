#!/usr/bin/env python3
"""
System test for SVO extraction functionality.
Tests the improved SVO extraction algorithm.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer import ChineseAnalyzer


def test_svo_extraction():
    """Test SVO extraction with various sentence patterns."""
    analyzer = ChineseAnalyzer()

    test_cases = [
        # Basic SVO
        "我吃饭",
        # Adjective modifier
        "那个穿红衣服的女孩很漂亮",
        # Complex sentence
        "他昨天在图书馆看书",
        # VA (adjective verb)
        "天气很好",
        # VC (copula)
        "这是一本书",
        # VE (you verb)
        "我有一个梦想",
        # Multiple predicates
        "我吃完饭就去睡觉",
    ]

    print("=" * 60)
    print("测试 SVO 提取功能")
    print("=" * 60)

    for text in test_cases:
        print(f"\n句子: {text}")
        print("-" * 40)

        results = analyzer.analyze(text)

        if not results:
            print("❌ 未提取到 SVO")
            continue

        for sentence, svo_list in results.items():
            for i, svo in enumerate(svo_list, 1):
                print(f"SVO #{i}:")
                print(f"  主语 (S): {svo['subject'] or '(无)'}")
                print(f"  谓语 (V): {svo['predicate'] or '(无)'}")
                print(f"  宾语 (O): {svo['object'] or '(无)'}")


if __name__ == "__main__":
    print("🧪 开始测试 bioCN SVO 提取功能\n")

    try:
        test_svo_extraction()
        print("\n" + "=" * 60)
        print("✅ 测试完成！")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
