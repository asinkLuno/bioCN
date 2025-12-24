#!/usr/bin/env python3
"""
System test for batch processing performance.
Compares single vs batch processing to verify GPU optimization.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer import ChineseAnalyzer


def test_batch_vs_single():
    """Compare batch processing vs single processing performance."""
    analyzer = ChineseAnalyzer()

    # Test texts simulating multiple paragraphs
    test_texts = [
        "我每天早上起床后会先刷牙洗脸。",
        "然后我会去厨房准备早餐。",
        "早餐通常包括面包、牛奶和鸡蛋。",
        "吃完早餐后我就开始工作。",
        "中午的时候我会休息一会儿。",
        "下午我继续完成我的任务。",
        "晚上回家后我喜欢看书或者看电影。",
        "睡觉前我会思考一天的收获。",
        "周末我会和朋友们一起出去玩。",
        "生活总是充满了各种有趣的事情。",
    ] * 5  # 50 texts total

    print("=" * 60)
    print("批处理性能测试")
    print("=" * 60)
    print(f"测试文本数量: {len(test_texts)}")
    print()

    # Method 1: Single processing (old way)
    print("方法 1: 逐个处理 (旧方法)")
    start = time.time()
    single_results = []
    for text in test_texts:
        result = analyzer.analyze(text)
        single_results.append(result)
    single_time = time.time() - start
    print(f"  耗时: {single_time:.2f} 秒")
    print()

    # Method 2: Batch processing (new way)
    print("方法 2: 批量处理 (新方法)")
    start = time.time()
    batch_results = analyzer.batch_analyze(test_texts)
    batch_time = time.time() - start
    print(f"  耗时: {batch_time:.2f} 秒")
    print()

    # Verify results are the same
    print("验证结果一致性:")
    all_match = True
    for i, (single, batch) in enumerate(zip(single_results, batch_results)):
        if single != batch:
            print(f"  ❌ 文本 {i} 结果不一致")
            all_match = False

    if all_match:
        print("  ✅ 所有结果一致")
    print()

    # Performance comparison
    speedup = single_time / batch_time if batch_time > 0 else 0
    print("=" * 60)
    print("性能对比:")
    print(f"  逐个处理: {single_time:.2f} 秒")
    print(f"  批量处理: {batch_time:.2f} 秒")
    print(f"  加速比: {speedup:.2f}x")
    print("=" * 60)

    if speedup < 1.5:
        print("⚠️  警告: 批处理加速效果不明显（可能在 CPU 上运行）")
    else:
        print(f"✅ 批处理成功加速 {speedup:.2f}x！")


if __name__ == "__main__":
    print("🧪 开始批处理性能测试\n")

    try:
        test_batch_vs_single()
        print("\n✅ 测试完成！")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
