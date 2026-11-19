# Markdown 制作器

独立维护的 PDF 原卷 Markdown 制作工具。功能和源码运行说明见 [source/README.md](source/README.md)。

在合集根目录运行 `python .github/tools/release/build.py picamarkdown --install`，或双击 `source/build_windows.bat`。新包输出到 `.release-output/picamarkdown/windows/PicaMarkdownExporter.exe`。

自动发布的软件 ID 为 `picamarkdown`；版本标签格式 `picamarkdown-v1.0.0`。EXE 从 [GitHub Releases](https://github.com/Yan16384/PicaSoft/releases) 下载，由 Actions 从 `source/` 重新构建。
