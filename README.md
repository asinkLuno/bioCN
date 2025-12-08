# bioCN

中文仿生阅读 EPUB 处理器，基于 HanLP 语法分析实现。

## 功能简介

bioCN 是一个为中文 EPUB 电子书增强阅读体验的工具。它通过以下方式帮助提升中文阅读效率：

- **语法高亮**：使用自然语言处理分析中文句子结构
- **主谓宾标记**：
  - 主语（Subject）- 红色粗体
  - 谓语（Predicate）- 蓝色粗体
  - 宾语（Object）- 绿色粗体
- **进度显示**：处理大型 EPUB 时显示实时进度

## 安装

### 前置要求

- Python 3.10+

### 方法一：使用 pip 安装（推荐）

```bash
# 从源码安装最新版本
pip install git+https://github.com/asinkLuno/bioCN.git
```

### 方法二：使用 uv 开发安装

```bash
# 克隆仓库
git clone https://github.com/asinkLuno/bioCN.git
cd bioCN

# 安装依赖
uv sync

# 激活虚拟环境
source .venv/bin/activate
```

### 开发环境安装

```bash
# 安装开发依赖
uv sync --group dev
```

## 使用方法

### pip 安装后使用

```bash
# 处理 EPUB 文件（自动生成输出路径）
biocn --input-path your-book.epub

# 指定输出路径
biocn --input-path your-book.epub --output-path processed-book.epub
```

### uv 开发环境使用

```bash
# 处理 EPUB 文件
uv run biocn --input-path your-book.epub

# 或者使用模块方式
uv run python -m src.cli --input-path your-book.epub
```

### 输出规则

如果不指定 `--output-path`，工具会在输入文件同目录下生成 `原文件名_bio.epub`。

### 示例

```bash
# 处理《窄门.epub》，生成《窄门_bio.epub》
# pip 安装后：
biocn --input-path 窄门.epub

# uv 开发环境：
uv run biocn --input-path tests/窄门.epub
```

## 技术原理

### 核心组件

1. **EpubParser**：解析 EPUB 文件，提取文本内容
2. **ChineseAnalyzer**：使用 HanLP 进行中文语法分析
3. **CLI 界面**：提供友好的命令行交互和进度显示

### 语法分析

基于 HanLP 的语义角色标注（SRL）技术：
- 自动识别句子中的主谓宾结构
- 支持复杂句子的多谓语分析
- 准确提取中文语法成分

### 标记规则

- **主语**：`<span style="color: red; font-weight: bold;">文本</span>`
- **谓语**：`<span style="color: blue; font-weight: bold;">文本</span>`
- **宾语**：`<span style="color: green; font-weight: bold;">文本</span>`

## 开发

### 运行测试

```bash
# 运行所有测试
uv run pytest

# 运行特定测试
uv run pytest tests/test_cli.py
```

### 代码格式化

```bash
# 格式化所有代码
./format_all.sh
```

### 项目结构

```
bioCN/
├── src/
│   ├── cli.py          # 命令行界面
│   ├── analyzer.py     # 中文语法分析器
│   └── epub_parser.py  # EPUB 文件解析器
├── tests/
│   ├── test_cli.py     # CLI 测试
│   └── *.epub         # 测试用 EPUB 文件
├── pyproject.toml      # 项目配置
└── README.md          # 本文档
```

## 依赖

- **click**: 命令行界面框架
- **ebooklib**: EPUB 文件处理
- **beautifulsoup4**: HTML 解析
- **hanlp**: 中文自然语言处理
- **rich**: 终端美化

## 注意事项

1. **包发布状态**：bioCN 尚未发布到 PyPI，请使用 `pip install git+https://github.com/asinkLuno/bioCN.git` 从源码安装
2. **首次运行**：HanLP 会自动下载预训练模型，需要网络连接
3. **处理时间**：大型 EPUB 文件可能需要几分钟处理时间
4. **兼容性**：仅支持标准 EPUB 格式
5. **语言支持**：专门针对中文文本设计

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 贡献

欢迎提交 Issue 和 Pull Request！

## 相关链接

- [HanLP 官方文档](https://hanlp.hankcs.com/)
- [Bionic Reading 概念](https://bionic-reading.com/)
- [EPUB 规范](https://www.w3.org/publishing/epub3/)
