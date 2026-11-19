# 本地发布配置验收 · 2026-11-19

修改范围：合集说明、依赖清单、各软件打包入口、共享构建/源码打包脚本、Actions 工作流，以及 PicaPhoto Android 的可选环境变量签名配置。各软件的目录名保留，移动到 `Pica软件合集/`。已删除旧 EXE 和四个本地验证 EXE，记录文件保留。

## 已实际执行

- 四个 Windows EXE 均通过统一脚本在本地成功构建：PicaCrop、PicaPhoto、PicaHotkeyTool、PicaMarkdownExporter。检查生成文件存在及 PE 文件头；不代表全功能运行回归。
- Markdown 制作器按现有 requirements 使用 PyMuPDF 1.28.2；构建器固定 PyInstaller 6.19.0。
- 10 个发布脚本测试通过：目录隔离、多软件与中文路径、共享工具改动、首次推送、手动选择、版本标签校验、真实入口与依赖、源码排除旧 EXE 与其他软件、缺 APK 阻止 PicaPhoto 残缺发布、源码完整性与摘要。
- 两个工作流通过 actionlint 1.7.12 检查；Actions 引用版本与输入已核对官方元数据。
- 已实际检查源码 ZIP 包含入口、requirements、共享构建工具，且排除二进制、签名文件和本机配置；校验文件回读验证摘要。

本地测试使用 Python 3.13，云端配置 Python 3.12；构建日志和旧校验记录保存在 `D:/desk/Codex Operate files/PicaSoft-validation/`，不加入 Git。

## 衬线 P 图标检查

- 四款 Windows 源码入口加载同一设计的 PNG，已核对窗口图标和快捷键工具的托盘图标。PyInstaller 已实际将 PNG 转为包含 16、32、256 像素尺寸的 ICO，构建命令同时指定 EXE 图标和运行时资源。
- 已核对 Android 五档启动图标的像素尺寸、WebView 与网页版的 192/512 像素图标，以及四款软件的源码清单均收录图标。
- 本轮验证不生成新的本地 EXE。带新图标的 Release 由推送后触发的 GitHub Actions 构建，发布说明链接到对应运行记录。

## 未验证

本机当前 PATH 未提供 Gradle，JAVA_HOME 指向旧 JDK 8，也未配置 Android SDK 环境变量。本次没有本地构建 Android APK；工作流已指定 JDK 17、Gradle 8.2 和 SDK 34，需要推送后在 Actions 验证。正式 Android 签名还需要用户配置既有密钥 secrets。

遵照用户要求，未执行 Git 提交、推送、远端设置变更或创建 Release。Actions 的真实触发、云端构建和上传尚未验收；本地静态检查与构建成功不能替代云端运行结果。
