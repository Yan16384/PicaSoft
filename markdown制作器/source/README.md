# PicaReader 原卷 Markdown 制作工具

打开 PicaMarkdownExporter.exe，选择单份 PDF、多份 PDF 或文件夹，选好保存位置，再点「生成 Markdown」。同名结果会另存一份。

每份 PDF 生成一个 Markdown，包含按页记录的原卷和学习附录。独立插图以 Base64 内嵌。

工具读取 PDF 已有的文字层。它只去除已确认的页底推广文字和生成器签名，保留听力入口、页码和试题内容。空白页保留原页码；有画面却没有文字层的页面会提示核对。

输出用于后续补全学习内容。双栏以空格近似对齐，扫描底图里的图题需对照原卷核查。

使用源码时安装 requirements.txt 中的依赖，再运行 paper_export_app.py。双击 build_windows.bat 可重新打包。

