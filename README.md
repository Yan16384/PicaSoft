# PicaSoft

打开 [Pica软件合集](Pica软件合集/)，按文件夹浏览四款独立软件。安装包从 GitHub Releases 下载。

| 软件 | 用途 | 平台 | 源码与说明 |
| --- | --- | --- | --- |
| PicaCrop | 图片裁剪、拼接、标注和导出 | Windows | [PicaCrop](Pica软件合集/PicaCrop/README.md) |
| PicaPhoto | 照片/视频分类、相册整理 | Windows、Android | [PicaPhoto](Pica软件合集/PicaPhoto/README.md) |
| PicaHotkeyTool | 全局快捷键切换窗口、系统托盘运行 | Windows | [PicaHotkeyTool](Pica软件合集/PicaHotkeyTool/README.md) |
| Markdown 制作器 | PDF 文字层转换为原卷 Markdown | Windows | [markdown制作器](Pica软件合集/markdown制作器/README.md) |

[下载发布包](https://github.com/Yan16384/PicaSoft/releases) · [构建记录](https://github.com/Yan16384/PicaSoft/actions/workflows/release.yml) · [发布与签名配置](.github/RELEASING.md)

## 自动打包

- 推送到 `main`：只构建内容发生变化的软件目录。一次修改多个软件会分别发布。
- 修改共享构建脚本、工作流或发布清单：重新构建全部软件。
- 在 Actions 的 **Release independent apps** 中点 **Run workflow**：选择某个软件或 `all`。
- 推送 `picacrop-v2.0.1` 这样的版本标签：只发布对应软件的正式版本；带 `-beta.1` 等后缀的版本标签生成预发布。

每条 Release 包含该软件的 Windows EXE、完整源码 ZIP、`SHA256SUMS.txt`；PicaPhoto 另含 APK。任一必需平台构建失败，不发布缺少文件的半成品。历史 Release 不自动删除。

普通推送与手动构建采用 `<软件ID>-build-<工作流编号>`，作为预发布保存。四个软件独立编号，不使用仓库唯一的 Latest 来混指不同软件。

GitHub Release 的 EXE 由 GitHub Actions 从对应提交的源码重新构建，发布正文附有构建记录链接。四款软件的程序图标统一使用蓝底白色衬线体大写 P。

## 本地构建

Windows 安装 Python 3.12（需包含 Tcl/Tk），在仓库根运行：

```powershell
python .github/tools/release/build.py picacrop --install
python .github/tools/release/build.py picaphoto --install
python .github/tools/release/build.py picahotkeytool --install
python .github/tools/release/build.py picamarkdown --install
```

也可以双击各软件的打包 BAT。输出默认保存在 `.release-output/<软件ID>/windows/`。

目录、入口、产物名称和依赖位置统一登记在 [projects.json](.github/tools/release/projects.json)。新增软件时同步工作流的手动选项和标签过滤，详见[发布指南](.github/RELEASING.md)。

## 验证范围

本次本地配置修改的检查记录见 [RELEASE-VALIDATION.md](.github/RELEASE-VALIDATION.md)。GitHub Actions 的实际执行和远端发布需在你提交、推送后验证；本地没有提交、推送或创建远端 Release。

软件文件夹存放源码与说明。源码包排除二进制、缓存、运行配置及签名密钥。上游与依赖的许可证声明应继续保留，不在此擅自为所有软件授予统一许可。
