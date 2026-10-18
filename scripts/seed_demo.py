"""生成演示用学习记录。

内部工具演示时，空白的学情页没有说服力。这里灌几条真实结构的学习记录，
让「完成率 / 薄弱章节 / 趋势」都有东西可看。数据是模拟的，但走的是
和生产完全相同的判分与落库路径，不是直接改数据库。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import training  # noqa: E402

# (章节, 答对数, 总题数)：故意留两章没过，学情页才有「最该补」可讲
DEMO = [
    ("security-policy.md", 4, 4),
    ("handbook-attendance.md", 5, 5),
    ("product-overview.md", 4, 4),
    ("support-faq.md", 3, 5),
    ("sales-playbook.md", 2, 5),
]


def main() -> None:
    for chapter, correct, total in DEMO:
        answers = [0] * total
        key = [0] * total
        for i in range(total):
            key[i] = 0 if i < correct else 1
        training.submit_quiz(chapter, answers, key)
        print(f"已记录：{chapter} {correct}/{total}")
    data = training.mastery()
    print(f"\n完成率 {data['completion']:.0%}，已通过 {data['passed']}/{data['total_chapters']} 章")


if __name__ == "__main__":
    main()
