# 独立发布指南

## 推送后自动生效

将本次本地修改提交并推送到 `main`，GitHub Actions 会按软件目录分别构建。第一次新增工作流和共享工具时会触发全部四个软件。后续仅修改 README.md 或本指南不会生成新包。

无需个人访问令牌：发布 job 使用 Actions 自带 `GITHUB_TOKEN`，只对发布 job 授予 `contents: write`。若仓库或组织策略禁止该权限，需要仓库管理员放行。手动 Run workflow 入口需要工作流已进入默认分支。

工作流分为入口 `release.yml` 和可复用的 `release-one.yml`。Windows EXE 构建使用 Python 3.12 / PyInstaller 6.19.0；PicaPhoto Android 使用 JDK 17 / Gradle 8.2 / SDK 34。Actions 版本已在本次配置时核对其官方元数据。

## 发布指定软件

| 文件夹 | 软件 ID / 标签前缀 | 版本标签示例 |
| --- | --- | --- |
| PicaCrop | picacrop | `picacrop-v2.0.1` |
| PicaPhoto | picaphoto | `picaphoto-v2.0.15` |
| PicaHotkeyTool | picahotkeytool | `picahotkeytool-v1.0.0` |
| markdown制作器 | picamarkdown | `picamarkdown-v1.0.0` |

软件源码统一存放在 `Pica软件合集/`。先提交源码与版本信息，再创建标签并推送。例如由你执行：

```powershell
git tag picacrop-v2.0.1
git push origin picacrop-v2.0.1
```

标签仅决定 Release 名称，**不会自动修改软件内版本**。Android 更新前必须同步提高 `Pica软件合集/PicaPhoto/android/app/build.gradle` 的 versionCode，并更新 versionName；保留原 applicationId 和签名。

普通主分支推送和手动构建使用 `软件ID-build-编号` 预发布；正式版本使用各自的版本标签。重新运行同一次构建会更新同名资产，不删除其他历史版本。手动运行应选择分支，不选择别的软件版本标签。

## Android 签名

未配置签名时会生成 `PicaPhoto-android-debug.apk`，Release 正文会注明它是测试包。GitHub 托管 runner 的临时 debug 密钥不能保证跨次构建一致，因此不能保证直接覆盖旧安装。不要为解决签名冲突直接卸载有重要数据的旧应用。

正式发布时，在仓库 Settings → Secrets and variables → Actions 中配置下列四个 secret：

- `PICA_ANDROID_KEYSTORE_BASE64`：你原来使用的 keystore 文件的 Base64 内容。
- `PICA_ANDROID_STORE_PASSWORD`：keystore 密码。
- `PICA_ANDROID_KEY_ALIAS`：密钥别名。
- `PICA_ANDROID_KEY_PASSWORD`：密钥密码。

四项须同时存在。构建时才将密钥解码到构建输出的私有子目录；只上传白名单中的 EXE、APK、源码 ZIP 和校验文件，不上传密钥。源码包还会排除 keystore、local.properties、.env、缓存和用户运行配置。请在自己的凭据存储中保管原始签名密钥，不写入仓库或 MD。

## 本地打包与源码包

```powershell
python .github/tools/release/build.py picacrop --install
python .github/tools/release/package.py picacrop --output .release-output/picacrop
```

PicaPhoto 需要先生成两个平台：

```powershell
python .github/tools/release/build.py picaphoto --install
python .github/tools/release/build.py picaphoto --platform android
python .github/tools/release/package.py picaphoto --output .release-output/picaphoto
```

本地 Android 构建需要可用的 `gradle`（8.2）、JDK 17 和 Android SDK 34；SDK 可由 ANDROID_HOME 或未提交的 local.properties 指定。云端由工作流配置这些工具，不依赖本机路径，也不要求已有 Gradle wrapper。

源码 ZIP 包含选中软件整个源码目录、发布清单、共享构建脚本与工作流。Markdown 制作器的源码包包含 `source/`、requirements 和构建入口，EXE 从 Release 下载。图片、网页资源和依赖说明照常进入源码包。

校验摘要中的路径相对于 Release 下载目录。下载 EXE 放入 windows/、APK 放入 android/ 后可使用 `sha256sum -c SHA256SUMS.txt`；也可直接用 PowerShell `Get-FileHash` 对照摘要。

## 新增软件

1. 在 `Pica软件合集/` 新建独立软件文件夹，添加源码、依赖清单及说明。
2. 在 `tools/release/projects.json` 登记唯一软件 ID、目录、入口和产物名。
3. 同步 release.yml 的 push.tags 与 workflow_dispatch 选项。
4. 若平台不同于现有 Windows/Android 构建器，先补构建脚本和必需产物检查。
5. 运行 `python -m unittest discover -s .github/tools/release/tests -v`，先本地打包，再由你提交推送。

## 官方参考

- [GitHub Actions 工作流语法](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions)
- [GITHUB_TOKEN 权限](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
- [Gradle setup-gradle](https://github.com/gradle/actions/tree/main/setup-gradle)
